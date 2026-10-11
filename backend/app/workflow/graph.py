"""Orchestrator (Agent 1): LangGraph workflows.

Conversation graph
    validate_input -> [has checkpoint?] load_issues | extract
    extract -(retry, bounded)-> extract | -(failed)-> fail | -(ok)-> validate_issues
    validate_issues -> persist_issues -> load_issues -> Send(process_issue) x N (parallel) -> aggregate

Issue graph (one per issue, run inside `process_issue`)
    classify -(retry)-> classify
    prioritize -(retry)-> prioritize
    apply_rules -> [URGENT?] escalate -> resolve | resolve
    resolve -(retry)-> resolve
    validate_record -> [review needed?] flag_review -> persist_ticket | persist_ticket

Every LLM stage retries only retryable failures, at most `workflow_max_stage_attempts` times.
When retries are exhausted the stage falls back to a safe default and the ticket is flagged
for human review, so one issue's failure never discards the others.
"""
import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..agents.classification import classify_issue
from ..agents.extraction import extract_issues
from ..agents.llm import LLMClient, LLMError
from ..agents.prioritization import prioritize_issue
from ..agents.resolution import recommend_resolution, sensitive_approval_reason
from ..config import Settings, get_settings
from ..rules.config import (
    FALLBACK_CATEGORY,
    FALLBACK_SUBCATEGORY,
    PRIORITY_ORDER,
    is_valid_classification,
    priority_rules,
    sla_hours,
    team_for_category,
    teams,
)
from ..rules.priority_rules import RuleInput, apply_priority_rules
from . import persistence as db
from .state import IssueState, IssueTask, WorkflowState
from .validation import validate_extraction

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- helpers
def _agent_step(state: dict, stage: str, max_attempts: int, call: Callable[[], dict],
                fallback: Callable[[str], dict] | None) -> dict:
    attempts = dict(state.get("attempts", {}))
    attempts[stage] = attempts.get(stage, 0) + 1
    try:
        return {**call(), "attempts": attempts, "stage_status": "ok"}
    except LLMError as e:
        err = f"{stage} attempt {attempts[stage]}: {e}"
        if e.retryable and attempts[stage] < max_attempts:
            return {"attempts": attempts, "stage_status": "retry", "errors": [err]}
        if fallback is None:
            return {"attempts": attempts, "stage_status": "failed", "errors": [err]}
        return {**fallback(str(e)), "attempts": attempts, "stage_status": "fallback", "errors": [err]}


def _retry_or(next_node: str, self_node: str):
    def route(state: dict) -> str:
        return self_node if state.get("stage_status") == "retry" else next_node

    return route


# --------------------------------------------------------------------------- issue graph
def build_issue_graph(llm: LLMClient, settings: Settings):
    max_attempts = settings.workflow_max_stage_attempts
    min_conf = priority_rules()["guards"]["min_confidence"]

    def classify(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "classification")

        def call():
            out = classify_issue(llm, issue=state["issue"])
            reasons = []
            if out.needs_review or out.category == FALLBACK_CATEGORY:
                reasons.append("Classification agent flagged the issue for review")
            if out.confidence < min_conf:
                reasons.append(f"Low classification confidence ({out.confidence:.2f})")
            return {"classification": out.model_dump(), "review_reasons": reasons}

        def fallback(err: str):
            return {
                "classification": {
                    "issue_type": "unknown", "category": FALLBACK_CATEGORY,
                    "subcategory": FALLBACK_SUBCATEGORY, "confidence": 0.0, "rationale": "fallback",
                },
                "review_reasons": [f"Classification unavailable ({err})"],
            }

        return _agent_step(state, "classification", max_attempts, call, fallback)

    def prioritize(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "prioritization")

        def call():
            out = prioritize_issue(llm, issue=state["issue"], classification=state["classification"])
            reasons = []
            if out.needs_review:
                reasons.append("Prioritization agent flagged uncertainty")
            if out.confidence < min_conf:
                reasons.append(f"Low priority confidence ({out.confidence:.2f})")
            return {"priority_llm": out.model_dump(), "review_reasons": reasons}

        def fallback(err: str):
            return {"priority_llm": None, "review_reasons": [f"Priority agent unavailable ({err})"]}

        return _agent_step(state, "prioritization", max_attempts, call, fallback)

    def apply_rules(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "business_rules")
        cls, facts, llm_p = state["classification"], state["issue"].get("facts", {}), state.get("priority_llm")
        outcome = apply_priority_rules(
            RuleInput(
                issue_type=cls["issue_type"], category=cls["category"], subcategory=cls["subcategory"],
                summary=state["issue"]["summary"], evidence=state["issue"].get("evidence", []),
                affected_users=facts.get("affected_users"), amount=facts.get("amount"),
                deadline_hours=facts.get("deadline_hours"),
            ),
            llm_p["priority"] if llm_p else None,
        )
        factors = [*(llm_p["factors"] if llm_p else []), *outcome.adjustments]
        factors += [f"Rule {r['id']}: {r['description']}" for r in outcome.rules_applied]
        return {
            "priority": {
                "final": outcome.final_priority,
                "llm_priority": outcome.llm_priority,
                "factors": factors,
                "rules_applied": outcome.rules_applied,
            },
            "escalated": outcome.final_priority == "URGENT",
            "review_reasons": outcome.review_reasons,
        }

    def escalate(state: IssueState) -> dict:
        pr = dict(state["priority"])
        pr["factors"] = [*pr["factors"], f"Escalated for urgent handling (SLA {sla_hours('URGENT')}h)"]
        return {"priority": pr}

    def resolve(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "resolution")
        cls, prio = state["classification"], state["priority"]["final"]

        def call():
            rec = recommend_resolution(
                llm, issue=state["issue"], classification=cls, priority=prio,
                missing_information=state["issue"].get("missing_information", []),
            )
            return {"recommendation": rec.__dict__}

        def fallback(err: str):
            return {
                "recommendation": {
                    "ticket_title": state["issue"]["summary"][:80],
                    "next_action": "Manual triage required: review the issue and decide the next step "
                                   "(automatic recommendation unavailable).",
                    "team": team_for_category(cls["category"]),
                    "sla_hours": sla_hours(prio),
                    "requires_human_approval": True,
                    "approval_reason": sensitive_approval_reason(cls["category"], cls["subcategory"])
                    or "No automatic recommendation",
                },
                "review_reasons": [f"Resolution agent unavailable ({err})"],
            }

        return _agent_step(state, "resolution", max_attempts, call, fallback)

    def validate_record(state: IssueState) -> dict:
        """Final deterministic check of critical fields before anything is persisted."""
        problems = []
        cls = dict(state.get("classification") or {})
        pr = dict(state.get("priority") or {})
        rec = dict(state.get("recommendation") or {})
        if not state["issue"].get("summary"):
            problems.append("summary")
        if not is_valid_classification(cls.get("category", ""), cls.get("subcategory", "")):
            problems.append("category")
            cls.update(category=FALLBACK_CATEGORY, subcategory=FALLBACK_SUBCATEGORY)
        if pr.get("final") not in PRIORITY_ORDER:
            problems.append("priority")
            pr["final"] = "MEDIUM"
        if rec.get("team") not in teams():
            problems.append("team")
            rec["team"] = team_for_category(cls["category"])
        if not rec.get("next_action"):
            problems.append("recommended action")
            rec["next_action"] = "Manual triage required."
        if not rec.get("ticket_title"):
            rec["ticket_title"] = state["issue"]["summary"][:80]
        rec.setdefault("sla_hours", sla_hours(pr["final"]))
        out: dict[str, Any] = {"classification": cls, "priority": pr, "recommendation": rec}
        if problems:
            out["review_reasons"] = ["Missing or invalid critical field(s): " + ", ".join(problems)]
        return out

    def flag_review(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "human_review_flag")
        return {}

    def persist_ticket(state: IssueState) -> dict:
        db.set_stage(state["run_id"], "ticket_creation")
        try:
            ticket_id, created = db.persist_ticket(conversation_id=state["conversation_id"], state=state)
            return {"ticket_id": ticket_id, "ticket_created": created, "outcome": "ticketed"}
        except Exception as e:  # noqa: BLE001 - isolate the failure to this issue
            log.exception("persisting ticket failed for issue %s", state["issue_id"])
            msg = f"ticket persistence failed: {type(e).__name__}"
            db.mark_issue_failed(state["issue_id"], msg)
            return {"outcome": "failed", "errors": [msg]}

    g = StateGraph(IssueState)
    for name, fn in [("classify", classify), ("prioritize", prioritize), ("apply_rules", apply_rules),
                     ("escalate", escalate), ("resolve", resolve), ("validate_record", validate_record),
                     ("flag_review", flag_review), ("persist_ticket", persist_ticket)]:
        g.add_node(name, fn)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", _retry_or("prioritize", "classify"), ["classify", "prioritize"])
    g.add_conditional_edges("prioritize", _retry_or("apply_rules", "prioritize"), ["prioritize", "apply_rules"])
    g.add_conditional_edges(
        "apply_rules", lambda s: "escalate" if s["escalated"] else "resolve", ["escalate", "resolve"]
    )
    g.add_edge("escalate", "resolve")
    g.add_conditional_edges("resolve", _retry_or("validate_record", "resolve"), ["resolve", "validate_record"])
    g.add_conditional_edges(
        "validate_record",
        lambda s: "flag_review" if s.get("review_reasons") else "persist_ticket",
        ["flag_review", "persist_ticket"],
    )
    g.add_edge("flag_review", "persist_ticket")
    g.add_edge("persist_ticket", END)
    return g.compile()


# --------------------------------------------------------------------------- conversation graph
def build_conversation_graph(llm: LLMClient, settings: Settings | None = None):
    settings = settings or get_settings()
    issue_graph = build_issue_graph(llm, settings)
    max_attempts = settings.workflow_max_stage_attempts

    def validate_input(state: WorkflowState) -> dict:
        db.set_stage(state["run_id"], "input_validation")
        text = (state.get("conversation_text") or "").strip()
        if len(text) < 10:
            return {"stage_status": "failed", "error": "Conversation text is empty or too short"}
        return {"stage_status": "ok", "reused_checkpoint": db.conversation_has_issues(state["conversation_id"])}

    def route_input(state: WorkflowState) -> str:
        if state["stage_status"] == "failed":
            return "fail"
        return "load_issues" if state.get("reused_checkpoint") else "extract"

    def extract(state: WorkflowState) -> dict:
        db.set_stage(state["run_id"], "extraction")

        def call():
            out = extract_issues(
                llm, conversation_text=state["conversation_text"], known_customer_ref=state.get("customer_ref")
            )
            return {"extracted": [i.model_dump() for i in out.issues]}

        res = _agent_step(
            {"attempts": {"extraction": state.get("extraction_attempts", 0)}},
            "extraction", max_attempts, call, None,
        )
        res["extraction_attempts"] = res.pop("attempts")["extraction"]
        if res["stage_status"] == "failed":
            res["error"] = res["errors"][-1]
        return res

    def route_extract(state: WorkflowState) -> str:
        return {"retry": "extract", "failed": "fail"}.get(state["stage_status"], "validate_issues")

    def validate_issues(state: WorkflowState) -> dict:
        db.set_stage(state["run_id"], "issue_validation")
        from ..agents.schemas import ExtractionOutput

        issues, notes = validate_extraction(
            ExtractionOutput.model_validate({"issues": state.get("extracted", [])}),
            conversation_text=state["conversation_text"],
            conversation_customer_ref=state.get("customer_ref"),
            max_issues=settings.max_issues_per_conversation,
        )
        return {"extracted": issues, "notes": notes}

    def persist_issues(state: WorkflowState) -> dict:
        db.set_stage(state["run_id"], "issue_persistence")
        db.persist_issues(state["conversation_id"], state["extracted"])
        return {}

    def load_issues(state: WorkflowState) -> dict:
        pending, done = db.load_pending_issues(state["conversation_id"])
        notes = []
        if state.get("reused_checkpoint"):
            notes.append(f"Reused previously extracted issues; {done} already ticketed, {len(pending)} pending")
        return {"pending": pending, "already_ticketed": done, "notes": notes}

    def dispatch(state: WorkflowState):
        if not state.get("pending"):
            return "aggregate"
        return [
            Send("process_issue", IssueTask(
                run_id=state["run_id"], conversation_id=state["conversation_id"], **p,
            ))
            for p in state["pending"]
        ]

    def process_issue(task: IssueTask) -> dict:
        """Delegates one issue to the issue graph. Exceptions are contained to this issue."""
        base = {"issue_id": task["issue_id"], "customer_ref": task["customer_ref"]}
        try:
            final = issue_graph.invoke(
                {
                    "run_id": task["run_id"], "conversation_id": task["conversation_id"],
                    "issue_id": task["issue_id"], "issue_key": task["issue_key"],
                    "customer_id": task["customer_id"], "customer_ref": task["customer_ref"],
                    "issue": task["issue"], "attempts": {}, "errors": [],
                    "review_reasons": list(task.get("initial_review_reasons", [])),
                },
                {"recursion_limit": 40},
            )
        except Exception as e:  # noqa: BLE001
            log.exception("issue %s failed", task["issue_id"])
            msg = f"issue processing failed: {type(e).__name__}"
            db.mark_issue_failed(task["issue_id"], msg)
            return {"results": [{**base, "outcome": "failed", "errors": [msg]}]}
        return {
            "results": [{
                **base,
                "outcome": final.get("outcome", "failed"),
                "ticket_id": final.get("ticket_id"),
                "created": final.get("ticket_created", False),
                "priority": (final.get("priority") or {}).get("final"),
                "category": (final.get("classification") or {}).get("category"),
                "review_required": bool(final.get("review_reasons")),
                "attempts": final.get("attempts", {}),
                "errors": final.get("errors", []),
            }]
        }

    def aggregate(state: WorkflowState) -> dict:
        db.set_stage(state["run_id"], "aggregation")
        results = state.get("results", [])
        failed = [r for r in results if r["outcome"] != "ticketed"]
        if failed and len(failed) == len(results) and not state.get("already_ticketed"):
            status = "failed"
        elif failed:
            status = "partially_completed"
        else:
            status = "completed"
        error = f"{len(failed)} issue(s) failed; see run state" if failed else None
        return {"status": status, "error": error}

    def fail(state: WorkflowState) -> dict:
        return {"status": "failed", "error": state.get("error") or "Workflow failed"}

    g = StateGraph(WorkflowState)
    g.add_node("validate_input", validate_input)
    g.add_node("extract", extract)
    g.add_node("validate_issues", validate_issues)
    g.add_node("persist_issues", persist_issues)
    g.add_node("load_issues", load_issues)
    g.add_node("process_issue", process_issue)
    g.add_node("aggregate", aggregate)
    g.add_node("fail", fail)

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", route_input, ["fail", "load_issues", "extract"])
    g.add_conditional_edges("extract", route_extract, ["extract", "fail", "validate_issues"])
    g.add_edge("validate_issues", "persist_issues")
    g.add_edge("persist_issues", "load_issues")
    g.add_conditional_edges("load_issues", dispatch, ["process_issue", "aggregate"])
    g.add_edge("process_issue", "aggregate")
    g.add_edge("aggregate", END)
    g.add_edge("fail", END)
    return g.compile()
