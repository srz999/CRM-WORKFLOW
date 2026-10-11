"""Agent 3 - Classification Agent. Output is constrained to the configured taxonomy."""
import json

from ..rules.config import taxonomy
from .base import call_structured
from .llm import LLMClient
from .schemas import ClassificationOutput

AGENT = "classification"


def _system() -> str:
    tax = taxonomy()
    cats = {
        key: {"label": c["label"], "subcategories": c["subcategories"]} for key, c in tax["categories"].items()
    }
    return f"""You are the Classification Agent in a customer-support CRM.
Classify ONE extracted customer issue.

issue_type must be one of: {json.dumps(tax["issue_types"])}
category and subcategory MUST be keys from this taxonomy (use the keys, not the labels):
{json.dumps(cats, indent=1)}

If the issue does not clearly fit, or the text is too vague, use category "other_needs_review",
subcategory "unclassified" and needs_review=true. Never invent new categories.
confidence is your confidence (0-1) in the category.

Respond with JSON only: {{"issue_type", "category", "subcategory", "confidence", "rationale", "needs_review"}}"""


def classify_issue(llm: LLMClient, *, issue: dict) -> ClassificationOutput:
    payload = {"task": "classify_issue", "issue": issue}
    return call_structured(llm, agent=AGENT, system=_system(), payload=payload, schema=ClassificationOutput)
