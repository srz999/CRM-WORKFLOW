"""Agent 5 - Resolution and Routing Agent.

The LLM writes the recommended next action. Team and SLA come from routing.yaml, and
sensitive actions are forced to require human approval. Nothing is executed here.
"""
from dataclasses import dataclass

from ..rules.config import routing, sla_hours, team_for_category
from .base import call_structured
from .llm import LLMClient
from .schemas import ResolutionOutput

AGENT = "resolution"

SYSTEM = """You are the Resolution and Routing Agent in a customer-support CRM.
For ONE classified and prioritized issue, recommend the concrete next step a support agent should take.

Rules:
- Recommend; never claim an action has already happened (no "refund issued", "password reset done").
- Never promise refunds, credits or account changes - those need human approval.
- If information is missing, the next action should be to ask the customer for it.
- ticket_title: short, specific title (max ~10 words).
- requires_human_approval: true if the next step involves money, refunds, account/security changes, or
  a judgement call; give approval_reason in that case.

Respond with JSON only: {"next_action", "requires_human_approval", "approval_reason", "ticket_title"}"""


@dataclass
class Recommendation:
    ticket_title: str
    next_action: str
    team: str
    sla_hours: int
    requires_human_approval: bool
    approval_reason: str | None


def recommend_resolution(
    llm: LLMClient, *, issue: dict, classification: dict, priority: str, missing_information: list[str]
) -> Recommendation:
    team = team_for_category(classification["category"])
    sla = sla_hours(priority)
    payload = {
        "task": "recommend_resolution",
        "issue": issue,
        "classification": classification,
        "final_priority": priority,
        "assigned_team": team,
        "sla_hours": sla,
        "missing_information": missing_information,
    }
    out = call_structured(llm, agent=AGENT, system=SYSTEM, payload=payload, schema=ResolutionOutput)

    approval, reason = out.requires_human_approval, out.approval_reason
    forced = sensitive_approval_reason(classification["category"], classification["subcategory"])
    if forced:
        approval = True
        reason = reason or forced
    return Recommendation(
        ticket_title=out.ticket_title.strip(),
        next_action=out.next_action.strip(),
        team=team,
        sla_hours=sla,
        requires_human_approval=approval,
        approval_reason=reason if approval else None,
    )


def sensitive_approval_reason(category: str, subcategory: str) -> str | None:
    cfg = routing()["human_approval"]
    if category in cfg.get("categories", []) or subcategory in cfg.get("subcategories", []):
        return f"Policy: {category}/{subcategory} actions require human approval"
    return None
