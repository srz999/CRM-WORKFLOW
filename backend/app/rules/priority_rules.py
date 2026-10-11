"""Deterministic business rules for priority. Pure functions - no LLM, no DB.

The Prioritization Agent proposes a priority; `apply_priority_rules` produces the final
priority. Mandatory escalations always win, and the LLM cannot lower them.
"""
import re
from dataclasses import dataclass, field

from .config import PRIORITY_ORDER, priority_rules


@dataclass
class RuleInput:
    issue_type: str
    category: str
    subcategory: str
    summary: str
    evidence: list[str]
    affected_users: int | None = None
    amount: float | None = None
    deadline_hours: int | None = None


@dataclass
class RuleOutcome:
    final_priority: str
    llm_priority: str | None
    urgent_rules: list[dict] = field(default_factory=list)
    medium_rules: list[dict] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    @property
    def rules_applied(self) -> list[dict]:
        return [*self.urgent_rules, *self.medium_rules]


def _max(a: str, b: str) -> str:
    return a if PRIORITY_ORDER[a] >= PRIORITY_ORDER[b] else b


def _min(a: str, b: str) -> str:
    return a if PRIORITY_ORDER[a] <= PRIORITY_ORDER[b] else b


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def rule_fires(rule: dict, inp: RuleInput) -> bool:
    text = " \n".join([inp.summary, *inp.evidence])

    if "categories" in rule and inp.category not in rule["categories"]:
        return False

    if "subcategories" in rule or "patterns" in rule:
        sub_ok = inp.subcategory in rule.get("subcategories", [])
        pat_ok = _any_match(rule.get("patterns", []), text)
        if not (sub_ok or pat_ok):
            return False

    if "min_affected_users" in rule:
        count_ok = inp.affected_users is not None and inp.affected_users >= rule["min_affected_users"]
        pat_ok = _any_match(rule.get("affected_users_patterns", []), text)
        if not (count_ok or pat_ok):
            return False

    if "min_amount" in rule:
        if inp.amount is None or inp.amount < rule["min_amount"]:
            return False

    if "max_deadline_hours" in rule:
        if inp.deadline_hours is None or inp.deadline_hours > rule["max_deadline_hours"]:
            return False

    return True


def apply_priority_rules(inp: RuleInput, llm_priority: str | None) -> RuleOutcome:
    cfg = priority_rules()
    guards = cfg["guards"]

    urgent = [
        {"id": r["id"], "description": r["description"], "effect": "URGENT"}
        for r in cfg["urgent_rules"]
        if rule_fires(r, inp)
    ]
    medium = [
        {"id": r["id"], "description": r["description"], "effect": "at least MEDIUM"}
        for r in cfg["medium_rules"]
        if rule_fires(r, inp)
    ]
    out = RuleOutcome(final_priority="MEDIUM", llm_priority=llm_priority, urgent_rules=urgent, medium_rules=medium)

    if llm_priority not in PRIORITY_ORDER:
        p = "MEDIUM"
        out.adjustments.append("No valid priority from the Prioritization Agent; defaulted to MEDIUM")
        out.review_reasons.append("Priority could not be determined by the agent")
    else:
        p = llm_priority

    # Guard: URGENT must be backed by a configured rule.
    if p == "URGENT" and not urgent and guards.get("urgent_requires_rule", True):
        p = "MEDIUM"
        out.adjustments.append("Agent proposed URGENT but no urgent escalation rule applies; lowered to MEDIUM")
        out.review_reasons.append("Agent urgency is not supported by the escalation rules")

    # Guard: plain inquiries without rule support stay in the normal queue (sentiment cannot inflate them).
    if inp.issue_type == "inquiry" and not urgent and not medium:
        cap = guards["inquiry_max_priority_without_rule"]
        if PRIORITY_ORDER[p] > PRIORITY_ORDER[cap]:
            out.adjustments.append(f"Routine inquiry with no rule support; capped at {cap}")
            p = _min(p, cap)

    # Floors: mandatory rules always win over the agent.
    if medium and PRIORITY_ORDER[p] < PRIORITY_ORDER["MEDIUM"]:
        out.adjustments.append("Raised to MEDIUM by rule: " + ", ".join(r["id"] for r in medium))
        p = _max(p, "MEDIUM")
    if urgent and p != "URGENT":
        out.adjustments.append("Escalated to URGENT by rule: " + ", ".join(r["id"] for r in urgent))
        p = "URGENT"

    out.final_priority = p
    return out
