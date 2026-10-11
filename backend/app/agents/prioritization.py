"""Agent 4 - Prioritization Agent.

The LLM proposes a priority with evidence-based factors. Deterministic rules
(app.rules.priority_rules) are applied afterwards by the workflow and always win.
"""
from .base import call_structured
from .llm import LLMClient
from .schemas import PriorityOutput

AGENT = "prioritization"

SYSTEM = """You are the Prioritization Agent in a customer-support CRM.
Recommend a priority for ONE classified issue based on impact, urgency, deadlines and context.

URGENT: critical outage, credible account-security concern, significant financial harm.
MEDIUM: a meaningful customer problem needing timely investigation that is not urgent.
LOW: routine inquiry or non-blocking request for the normal queue.

Do NOT base priority on tone or sentiment. An angry customer asking a routine question is LOW.
factors: 1-4 short, evidence-based reasons that reference facts from the issue.
Set needs_review=true if the information is too thin to judge.

Respond with JSON only: {"priority", "factors", "confidence", "needs_review"}"""


def prioritize_issue(llm: LLMClient, *, issue: dict, classification: dict) -> PriorityOutput:
    payload = {"task": "prioritize_issue", "issue": issue, "classification": classification}
    return call_structured(llm, agent=AGENT, system=SYSTEM, payload=payload, schema=PriorityOutput)
