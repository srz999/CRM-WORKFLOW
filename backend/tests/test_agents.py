"""Each agent tested independently with mocked LLM responses."""
import pytest

from app.agents.base import AgentOutputError
from app.agents.classification import classify_issue
from app.agents.extraction import extract_issues
from app.agents.llm import RetryableLLMError, parse_json_object
from app.agents.prioritization import prioritize_issue
from app.agents.resolution import recommend_resolution

from .fake_llm import FakeLLM


class Static:
    def __init__(self, response):
        self.response = response

    def complete_json(self, **_):
        if isinstance(self.response, str):
            return parse_json_object(self.response, "test")
        return self.response


def test_extraction_returns_structured_issues(sample_text):
    out = extract_issues(FakeLLM(), conversation_text=sample_text["S15"], known_customer_ref="CUST-2305")
    assert len(out.issues) == 3
    assert all(i.summary and i.evidence for i in out.issues)


def test_extraction_rejects_invalid_shape():
    with pytest.raises(AgentOutputError):
        extract_issues(Static({"issues": [{"summary": ""}]}), conversation_text="x" * 20, known_customer_ref=None)
    with pytest.raises(AgentOutputError):
        extract_issues(Static({"tickets": []}), conversation_text="x" * 20, known_customer_ref=None)


def test_malformed_json_is_retryable():
    with pytest.raises(RetryableLLMError):
        extract_issues(Static("not json {"), conversation_text="x" * 20, known_customer_ref=None)
    with pytest.raises(RetryableLLMError):
        extract_issues(Static("[1, 2]"), conversation_text="x" * 20, known_customer_ref=None)


def test_classification_valid_and_taxonomy_enforced():
    ok = classify_issue(Static({"issue_type": "inquiry", "category": "product_subscription",
                                "subcategory": "plan_inclusions", "confidence": 0.9}), issue={"summary": "q"})
    assert ok.category == "product_subscription"
    with pytest.raises(AgentOutputError):
        classify_issue(Static({"issue_type": "inquiry", "category": "made_up_category",
                               "subcategory": "x", "confidence": 0.9}), issue={"summary": "q"})
    with pytest.raises(AgentOutputError):
        classify_issue(Static({"issue_type": "rant", "category": "general_support",
                               "subcategory": "other", "confidence": 0.9}), issue={"summary": "q"})


def test_prioritization_schema():
    out = prioritize_issue(Static({"priority": "LOW", "factors": ["routine"], "confidence": 0.8}),
                           issue={}, classification={})
    assert out.priority == "LOW"
    with pytest.raises(AgentOutputError):
        prioritize_issue(Static({"priority": "CRITICAL", "factors": ["x"], "confidence": 0.8}),
                         issue={}, classification={})
    with pytest.raises(AgentOutputError):
        prioritize_issue(Static({"priority": "LOW", "factors": [], "confidence": 0.8}), issue={}, classification={})


def test_resolution_uses_routing_rules_and_forces_approval():
    llm = Static({"ticket_title": "Refund delayed", "next_action": "Trace the refund with the provider.",
                  "requires_human_approval": False})
    rec = recommend_resolution(llm, issue={"summary": "x"}, priority="MEDIUM", missing_information=[],
                               classification={"category": "refunds", "subcategory": "refund_delayed"})
    assert rec.team == "Refunds"  # from routing.yaml, not the LLM
    assert rec.sla_hours == 24
    assert rec.requires_human_approval and rec.approval_reason  # policy override


def test_resolution_non_sensitive_respects_llm():
    llm = Static({"ticket_title": "Hours question", "next_action": "Reply with the weekend support hours.",
                  "requires_human_approval": False})
    rec = recommend_resolution(llm, issue={"summary": "x"}, priority="LOW", missing_information=[],
                               classification={"category": "general_support", "subcategory": "general_question"})
    assert rec.team == "General Support" and rec.sla_hours == 72 and not rec.requires_human_approval


# Regression: real models return slightly different JSON shapes (seen with gpt-4o-mini).
def test_extraction_tolerates_common_shape_drift():
    raw = {"issues": [{
        "summary": "Customer charged twice for one order",
        "evidence": "two deductions of $89.99",            # string instead of list
        "missing_information": None,                       # null instead of []
        "customer_ref": "N/A",                             # placeholder instead of null
        "amount": "$89.99",                                # currency string
        "affected_users": "35 employees",
        "deadline_hours": "unknown",
    }]}
    issue = extract_issues(Static(raw), conversation_text="x" * 20, known_customer_ref=None).issues[0]
    assert issue.evidence == ["two deductions of $89.99"]
    assert issue.missing_information == [] and issue.customer_ref is None
    assert issue.amount == 89.99 and issue.affected_users == 35 and issue.deadline_hours is None

    single = extract_issues(Static({"issues": {"summary": "Login fails after reset", "evidence": ["x"]}}),
                            conversation_text="x" * 20, known_customer_ref=None)
    assert len(single.issues) == 1


def test_other_agents_tolerate_shape_drift():
    c = classify_issue(Static({"issue_type": "Complaint", "category": "Billing_Payments",
                               "subcategory": "duplicate charge", "confidence": "90"}), issue={})
    assert (c.issue_type, c.category, c.subcategory, c.confidence) == (
        "complaint", "billing_payments", "duplicate_charge", 0.9)
    p = prioritize_issue(Static({"priority": " medium ", "factors": "one factor", "confidence": 0.7}),
                         issue={}, classification={})
    assert p.priority == "MEDIUM" and p.factors == ["one factor"]
    # meaning-changing problems still fail
    with pytest.raises(AgentOutputError):
        prioritize_issue(Static({"priority": "high", "factors": ["x"], "confidence": 0.7}), issue={}, classification={})
    with pytest.raises(AgentOutputError):
        extract_issues(Static({"issues": [{"summary": None, "evidence": "x"}]}), conversation_text="x" * 20,
                       known_customer_ref=None)
