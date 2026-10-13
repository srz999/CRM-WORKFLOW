"""Orchestrator end-to-end against PostgreSQL with a mocked LLM."""
import openai
import pytest
from sqlalchemy import func, select

from app.agents.llm import RetryableLLMError
from app.db import session_scope
from app.models import Conversation, Customer, Issue, Ticket, TicketEvent, WorkflowRun
from app.api.conversations import upsert_conversation
from app.workflow.runner import run_workflow

from .fake_llm import SAMPLE_TEXT, FakeLLM

REFS = {"S01": "CUST-1001", "S04": "CUST-1302", "S07": "CUST-1608", "S10": "CUST-1955", "S11": None,
        "S13": None, "S14": "CUST-2210", "S15": "CUST-2305", "S12": "CUST-1001"}


def submit(sid, llm=None):
    conv_id = upsert_conversation(SAMPLE_TEXT[sid], REFS.get(sid))
    run_id = run_workflow(conv_id, llm or FakeLLM())
    return conv_id, run_id


def tickets_for(conv_id):
    with session_scope() as s:
        rows = s.scalars(select(Ticket).where(Ticket.conversation_id == conv_id).order_by(Ticket.id)).all()
        return [
            {"id": t.id, "priority": t.priority, "category": t.category, "status": t.status, "team": t.team,
             "review": t.review_required, "customer": t.customer.reference if t.customer else None,
             "approval": t.requires_human_approval, "issue_id": t.issue_id}
            for t in rows
        ]


def run_row(run_id):
    with session_scope() as s:
        r = s.get(WorkflowRun, run_id)
        return {c: getattr(r, c) for c in ("status", "error", "tickets_created", "tickets_updated",
                                            "issues_failed", "state", "current_stage")}


# 1 -------------------------------------------------------------------------
def test_normal_conversation_produces_structured_issue_records():
    conv_id, run_id = submit("S04")
    assert run_row(run_id)["status"] == "completed"
    with session_scope() as s:
        issue = s.scalars(select(Issue).where(Issue.conversation_id == conv_id)).one()
        assert issue.issue_type == "inquiry"
        assert (issue.category, issue.subcategory) == ("product_subscription", "plan_inclusions")
        assert issue.evidence and issue.summary
        assert issue.recommended_priority == "LOW"
        assert issue.processing_status == "ticketed"
        assert issue.customer.reference == "CUST-1302"
        conv = s.get(Conversation, conv_id)
        assert conv.status == "completed"
    [t] = tickets_for(conv_id)
    assert t["team"] == "Customer Success" and t["status"] == "open"


# 2 -------------------------------------------------------------------------
def test_three_issues_produce_three_distinct_tickets():
    conv_id, run_id = submit("S15")
    ts = tickets_for(conv_id)
    assert len(ts) == 3
    assert len({t["issue_id"] for t in ts}) == 3
    assert sorted(t["category"] for t in ts) == ["billing_payments", "delivery_orders", "product_subscription"]
    assert run_row(run_id)["tickets_created"] == 3


# 3 -------------------------------------------------------------------------
def test_multiple_customers_are_not_mixed():
    shared, _ = submit("S13")  # one transcript, two customers
    by_cat = {t["category"]: t["customer"] for t in tickets_for(shared)}
    assert by_cat == {"delivery_orders": "CUST-2101", "general_support": "CUST-2102"}

    a, _ = submit("S07")
    b, _ = submit("S04")
    assert {t["customer"] for t in tickets_for(a)} == {"CUST-1608"}
    assert {t["customer"] for t in tickets_for(b)} == {"CUST-1302"}


def test_repeat_customer_links_to_same_customer_record():
    c1, _ = submit("S01")
    c2, _ = submit("S12")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Customer).where(Customer.reference == "CUST-1001")) == 1
    assert {t["customer"] for t in tickets_for(c1) + tickets_for(c2)} == {"CUST-1001"}


# 6 -------------------------------------------------------------------------
def test_llm_cannot_override_escalation_end_to_end():
    conv_id, _ = submit("S07")  # mocked agent proposes MEDIUM for a security incident
    [t] = tickets_for(conv_id)
    assert t["priority"] == "URGENT"
    assert t["approval"]
    with session_scope() as s:
        issue = s.scalars(select(Issue).where(Issue.conversation_id == conv_id)).one()
        assert issue.llm_priority == "MEDIUM"
        assert "account_security" in [r["id"] for r in issue.rules_applied]


# 7 -------------------------------------------------------------------------
def test_angry_routine_question_is_low_end_to_end():
    conv_id, _ = submit("S10")  # mocked agent proposes URGENT because the customer is angry
    [t] = tickets_for(conv_id)
    assert t["priority"] == "LOW"


# 8 -------------------------------------------------------------------------
def test_missing_information_routes_to_review():
    conv_id, _ = submit("S11")
    [t] = tickets_for(conv_id)
    assert t["review"] and t["status"] == "needs_review"
    assert t["customer"] is None  # never invented
    with session_scope() as s:
        issue = s.scalars(select(Issue).where(Issue.conversation_id == conv_id)).one()
        assert issue.missing_information == ["order number", "customer name or account"]
        assert issue.review_status == "required"


def test_ambiguous_case_goes_to_triage_review():
    conv_id, _ = submit("S14")
    [t] = tickets_for(conv_id)
    assert t["category"] == "other_needs_review" and t["team"] == "Triage" and t["review"]


# 9 -------------------------------------------------------------------------
def test_invalid_classification_output_falls_back_safely():
    bad = {"issue_type": "complaint", "category": "invented_category", "subcategory": "x", "confidence": 0.99}
    llm = FakeLLM(overrides={"classification": lambda p, n: bad})
    conv_id, run_id = submit("S04", llm)
    assert llm.calls["classification"] == 2  # bounded retry
    [t] = tickets_for(conv_id)
    assert t["category"] == "other_needs_review" and t["review"]
    assert run_row(run_id)["status"] == "completed"


def test_malformed_json_then_success_is_retried():
    llm = FakeLLM(overrides={"prioritization": lambda p, n: "{{not json" if n == 1 else None})
    conv_id, run_id = submit("S04", llm)
    [t] = tickets_for(conv_id)
    assert t["priority"] == "LOW" and not t["review"]
    assert run_row(run_id)["state"]["issues"][0]["attempts"]["prioritization"] == 2


def test_timeout_and_rate_limit_are_retried_then_fall_back():
    def boom(p, n):
        raise RetryableLLMError("resolution: LLM request timed out")

    llm = FakeLLM(overrides={"resolution": boom})
    conv_id, _ = submit("S04", llm)
    assert llm.calls["resolution"] == 2
    [t] = tickets_for(conv_id)
    assert t["review"] and t["approval"]


def test_extraction_failure_fails_run_safely():
    llm = FakeLLM(overrides={"extraction": lambda p, n: "<<garbage>>"})
    conv_id, run_id = submit("S04", llm)
    r = run_row(run_id)
    assert r["status"] == "failed" and "malformed" in r["error"]
    assert llm.calls["extraction"] == 2
    assert tickets_for(conv_id) == []


def test_openai_errors_are_classified(monkeypatch):
    import httpx

    from app.agents.llm import LLMError, OpenAILLM
    from app.config import Settings

    llm = OpenAILLM(Settings(openai_api_key="sk-test"))
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def raise_(exc):
        def f(**_):
            raise exc
        return f

    monkeypatch.setattr(llm.client.chat.completions, "create", raise_(openai.APITimeoutError(request=req)))
    with pytest.raises(RetryableLLMError):
        llm.complete_json(agent="extraction", system="s", payload={})
    resp = httpx.Response(401, request=req)
    monkeypatch.setattr(llm.client.chat.completions, "create",
                        raise_(openai.AuthenticationError("bad key", response=resp, body=None)))
    with pytest.raises(LLMError) as e:
        llm.complete_json(agent="extraction", system="s", payload={})
    assert not e.value.retryable and "sk-" not in str(e.value)


# 10 ------------------------------------------------------------------------
def test_duplicate_processing_creates_no_duplicate_tickets():
    llm = FakeLLM()
    conv_id, run1 = submit("S01", llm)
    first = tickets_for(conv_id)
    assert len(first) == 2

    conv_again, run2 = submit("S01", llm)  # same text + customer resubmitted
    assert conv_again == conv_id
    assert tickets_for(conv_id) == first
    assert run_row(run2)["tickets_created"] == 0
    assert run_row(run2)["tickets_updated"] == 2
    assert llm.calls["extraction"] == 1  # extraction checkpoint reused, not re-run
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Ticket)) == 2
        assert s.scalar(select(func.count()).select_from(TicketEvent)) == 2  # one 'created' each


# 15 ------------------------------------------------------------------------
def test_one_issue_failure_keeps_other_results_and_retry_completes():
    target = "Promo code SPRING20 accepted but 20% discount missing from receipt"

    def crash(p, n):
        if p["issue"]["summary"] == target:
            raise RuntimeError("simulated crash")
        return None

    llm = FakeLLM(overrides={"resolution": crash})
    conv_id, run_id = submit("S15", llm)
    r = run_row(run_id)
    assert r["status"] == "partially_completed"
    assert r["issues_failed"] == 1 and r["tickets_created"] == 2
    assert len(tickets_for(conv_id)) == 2
    with session_scope() as s:
        failed = s.scalars(select(Issue).where(Issue.processing_status == "failed")).one()
        assert failed.summary == target and failed.error

    # Retry: only the failed issue is processed; no duplicates.
    run2 = run_workflow(conv_id, FakeLLM())
    assert run_row(run2)["status"] == "completed"
    assert run_row(run2)["tickets_created"] == 1
    assert len(tickets_for(conv_id)) == 3
