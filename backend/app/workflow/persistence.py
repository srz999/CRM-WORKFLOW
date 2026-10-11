"""All database writes performed by the workflow. The LLM never touches the database:
only these fixed, parameterised operations are executed, driven by validated state."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import session_scope
from ..models import Conversation, Customer, Issue, Ticket, TicketEvent, WorkflowRun
from .validation import issue_key


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_customer(session, reference: str, name: str | None = None, email: str | None = None,
                           phone: str | None = None) -> int:
    stmt = (
        pg_insert(Customer)
        .values(reference=reference, name=name, email=email, phone=phone)
        .on_conflict_do_nothing(index_elements=["reference"])
    )
    session.execute(stmt)
    cust = session.scalars(select(Customer).where(Customer.reference == reference)).one()
    # fill in details only if provided and not yet known
    if name and not cust.name:
        cust.name = name
    if email and not cust.email:
        cust.email = email
    if phone and not cust.phone:
        cust.phone = phone
    return cust.id


def set_stage(run_id: int, stage: str) -> None:
    with session_scope() as s:
        s.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(current_stage=stage))


def conversation_has_issues(conversation_id: int) -> bool:
    with session_scope() as s:
        return s.scalar(select(Issue.id).where(Issue.conversation_id == conversation_id).limit(1)) is not None


def persist_issues(conversation_id: int, issues: list[dict]) -> None:
    """Checkpoint validated extraction results. Idempotent via the unique issue_key."""
    with session_scope() as s:
        for ordinal, issue in enumerate(issues, start=1):
            customer_id = get_or_create_customer(s, issue["customer_ref"]) if issue["customer_ref"] else None
            stmt = (
                pg_insert(Issue)
                .values(
                    issue_key=issue_key(conversation_id, ordinal, issue["summary"]),
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    ordinal=ordinal,
                    issue_type="unknown",
                    summary=issue["summary"],
                    evidence=issue["evidence"],
                    customer_impact=issue["customer_impact"],
                    requested_action=issue["requested_action"],
                    missing_information=issue["missing_information"],
                    facts=issue["facts"],
                    review_reasons=issue["review_reasons"],
                    review_status="required" if issue["review_reasons"] else "not_required",
                    processing_status="pending",
                )
                .on_conflict_do_nothing(index_elements=["issue_key"])
            )
            s.execute(stmt)


def load_pending_issues(conversation_id: int) -> tuple[list[dict], int]:
    """Issues of this conversation that do not have a ticket yet, plus the count already ticketed."""
    with session_scope() as s:
        rows = s.scalars(
            select(Issue).where(Issue.conversation_id == conversation_id).order_by(Issue.ordinal)
        ).all()
        ticketed_ids = set(
            s.scalars(select(Ticket.issue_id).where(Ticket.conversation_id == conversation_id)).all()
        )
        pending = []
        for r in rows:
            if r.id in ticketed_ids:
                continue
            pending.append(
                {
                    "issue_id": r.id,
                    "issue_key": r.issue_key,
                    "customer_id": r.customer_id,
                    "customer_ref": r.customer.reference if r.customer else None,
                    "issue": {
                        "summary": r.summary,
                        "evidence": r.evidence,
                        "customer_impact": r.customer_impact,
                        "requested_action": r.requested_action,
                        "missing_information": r.missing_information,
                        "facts": r.facts,
                    },
                    # keep only extraction-stage reasons; later stages are recomputed on retry
                    "initial_review_reasons": [
                        x for x in r.review_reasons
                        if x.startswith(("No supporting evidence", "Customer could not", "Clarification needed"))
                    ],
                }
            )
        return pending, len(ticketed_ids)


def build_description(issue: dict, recommendation: dict) -> str:
    parts = [issue["summary"]]
    if issue.get("customer_impact"):
        parts.append(f"Impact: {issue['customer_impact']}")
    if issue.get("requested_action"):
        parts.append(f"Customer request: {issue['requested_action']}")
    if issue.get("missing_information"):
        parts.append("Missing information: " + ", ".join(issue["missing_information"]))
    if issue.get("evidence"):
        parts.append("Evidence: " + " | ".join(f'"{q}"' for q in issue["evidence"]))
    return "\n".join(parts)


def persist_ticket(*, conversation_id: int, state: dict) -> tuple[int, bool]:
    """Update the issue with agent results and create its ticket exactly once.

    Returns (ticket_id, created). ON CONFLICT (issue_id) DO NOTHING guarantees a retried
    workflow never creates a second ticket and never overwrites human edits.
    """
    issue = state["issue"]
    cls = state["classification"]
    pr = state["priority"]
    rec = state["recommendation"]
    review_reasons = list(dict.fromkeys(state.get("review_reasons", [])))
    review_required = bool(review_reasons)
    now = utcnow()

    with session_scope() as s:
        s.execute(
            update(Issue)
            .where(Issue.id == state["issue_id"])
            .values(
                issue_type=cls["issue_type"],
                category=cls["category"],
                subcategory=cls["subcategory"],
                classification_confidence=cls.get("confidence"),
                llm_priority=pr.get("llm_priority"),
                recommended_priority=pr["final"],
                priority_factors=pr["factors"],
                rules_applied=pr["rules_applied"],
                review_status="required" if review_required else "not_required",
                review_reasons=review_reasons,
                processing_status="ticketed",
                error=None,
            )
        )
        stmt = (
            pg_insert(Ticket)
            .values(
                issue_id=state["issue_id"],
                customer_id=state.get("customer_id"),
                conversation_id=conversation_id,
                title=rec["ticket_title"][:200],
                description=build_description(issue, rec),
                category=cls["category"],
                subcategory=cls["subcategory"],
                priority=pr["final"],
                team=rec["team"],
                recommended_action=rec["next_action"],
                requires_human_approval=rec["requires_human_approval"],
                approval_reason=rec.get("approval_reason"),
                review_required=review_required,
                status="needs_review" if review_required else "open",
                sla_hours=rec["sla_hours"],
                due_at=now + timedelta(hours=rec["sla_hours"]),
            )
            .on_conflict_do_nothing(index_elements=["issue_id"])
            .returning(Ticket.id)
        )
        new_id = s.execute(stmt).scalar_one_or_none()
        if new_id is None:
            existing = s.scalar(select(Ticket.id).where(Ticket.issue_id == state["issue_id"]))
            return existing, False
        s.add(
            TicketEvent(
                ticket_id=new_id,
                event_type="created",
                field="status",
                to_value="needs_review" if review_required else "open",
                description=(
                    f"Ticket created by workflow run {state['run_id']} with priority {pr['final']}, "
                    f"assigned to {rec['team']}"
                    + (" (human review required)" if review_required else "")
                ),
                actor="workflow",
            )
        )
        return new_id, True


def mark_issue_failed(issue_id: int, error: str) -> None:
    with session_scope() as s:
        s.execute(
            update(Issue).where(Issue.id == issue_id).values(processing_status="failed", error=error[:1000])
        )


def finalize_run(run_id: int, *, status: str, error: str | None, counters: dict, state: dict) -> None:
    with session_scope() as s:
        run = s.get(WorkflowRun, run_id)
        run.status = status
        run.current_stage = "done" if status != "failed" else run.current_stage
        run.error = error
        run.issues_found = counters.get("issues_found", 0)
        run.tickets_created = counters.get("tickets_created", 0)
        run.tickets_updated = counters.get("tickets_existing", 0)
        run.issues_failed = counters.get("issues_failed", 0)
        run.state = state
        run.completed_at = utcnow()
        conv = s.get(Conversation, run.conversation_id)
        conv.status = status
        conv.error = error
