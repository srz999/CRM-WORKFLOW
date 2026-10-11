"""8. Missing information leads to review/clarification - never invented facts."""
from app.agents.schemas import ExtractionOutput
from app.workflow.validation import deterministic_deadline_hours, validate_extraction

TEXT = "Customer: hi my delivery is late. it should have been here already. where is it?"


def run(issue, text=TEXT, ref=None):
    return validate_extraction(ExtractionOutput.model_validate({"issues": [issue]}), conversation_text=text,
                               conversation_customer_ref=ref, max_issues=10)


def test_fabricated_evidence_removed_and_flagged():
    issues, notes = run({"summary": "Late delivery of order #X-999", "evidence": ["order #X-999 is late"]})
    assert issues[0]["evidence"] == []
    assert any("evidence" in r for r in issues[0]["review_reasons"])
    assert notes


def test_invented_customer_reference_is_dropped():
    issues, _ = run({"summary": "Late delivery", "evidence": ["my delivery is late"], "customer_ref": "CUST-4242"})
    assert issues[0]["customer_ref"] is None
    assert any("Customer could not be identified" in r for r in issues[0]["review_reasons"])


def test_missing_information_requires_clarification():
    issues, _ = run({"summary": "Late delivery", "evidence": ["my delivery is late"],
                     "missing_information": ["order number"]})
    assert any("order number" in r for r in issues[0]["review_reasons"])


def test_invented_deadline_ignored():
    issues, _ = run({"summary": "Late delivery", "evidence": ["my delivery is late"], "deadline_text": "by tomorrow",
                     "deadline_hours": 24})
    assert issues[0]["facts"]["deadline_hours"] is None


def test_known_customer_used_when_text_has_no_ref():
    issues, _ = run({"summary": "Late delivery", "evidence": ["my delivery is late"]}, ref="CUST-1")
    assert issues[0]["customer_ref"] == "CUST-1"
    assert issues[0]["review_reasons"] == []


def test_duplicate_issues_collapsed():
    out = ExtractionOutput.model_validate({"issues": [
        {"summary": "Late delivery", "evidence": ["my delivery is late"]},
        {"summary": "late delivery.", "evidence": ["my delivery is late"]},
    ]})
    issues, notes = validate_extraction(out, conversation_text=TEXT, conversation_customer_ref="C", max_issues=10)
    assert len(issues) == 1 and notes


def test_deadline_parsing():
    assert deterministic_deadline_hours("tomorrow") == 24
    assert deterministic_deadline_hours("which is in 3 days") == 72
    assert deterministic_deadline_hours("in two hours") == 2
    assert deterministic_deadline_hours("Saturday") is None
