"""Deterministic business rules - no LLM, no DB."""
import pytest
from pydantic import ValidationError

from app.agents.schemas import ClassificationOutput
from app.rules.config import category_keys, is_valid_classification, sla_hours, team_for_category
from app.rules.priority_rules import RuleInput, apply_priority_rules


def ri(**kw):
    base = dict(issue_type="complaint", category="general_support", subcategory="other", summary="x", evidence=[])
    base.update(kw)
    return RuleInput(**base)


# --- 4. taxonomy enforced -------------------------------------------------
def test_taxonomy_contains_required_categories():
    assert set(category_keys()) == {
        "billing_payments", "technical_support", "refunds", "delivery_orders", "account_access",
        "product_subscription", "general_support", "other_needs_review",
    }


@pytest.mark.parametrize("cat,sub", [("shipping", "late"), ("billing_payments", "app_crash"), ("", "")])
def test_classification_outside_taxonomy_is_rejected(cat, sub):
    assert not is_valid_classification(cat, sub)
    with pytest.raises(ValidationError):
        ClassificationOutput(issue_type="complaint", category=cat, subcategory=sub, confidence=0.9)


def test_every_category_routes_to_a_team_and_slas_exist():
    for c in category_keys():
        assert team_for_category(c)
    assert sla_hours("URGENT") < sla_hours("MEDIUM") < sla_hours("LOW")


# --- 5. urgent / medium / low rules work as configured ---------------------
def test_security_rule_escalates():
    out = apply_priority_rules(ri(category="account_access", subcategory="suspicious_activity"), "LOW")
    assert out.final_priority == "URGENT"
    assert "account_security" in [r["id"] for r in out.urgent_rules]


def test_multi_user_outage_needs_multiple_users():
    one = ri(category="technical_support", subcategory="service_outage", summary="portal down for me")
    many = ri(category="technical_support", subcategory="service_outage", summary="portal down",
              affected_users=35)
    assert apply_priority_rules(one, "MEDIUM").final_priority == "MEDIUM"
    assert apply_priority_rules(many, "MEDIUM").final_priority == "URGENT"


def test_multi_user_outage_detected_from_text():
    inp = ri(category="technical_support", subcategory="service_outage", summary="Portal unavailable",
             evidence=["All 35 employees on the dispatch floor get a 503 error"])
    assert apply_priority_rules(inp, "LOW").final_priority == "URGENT"


def test_duplicate_charge_and_high_amount_and_deadline():
    assert apply_priority_rules(
        ri(category="billing_payments", subcategory="other", evidence=["I've been charged twice"]), "LOW"
    ).final_priority == "URGENT"
    assert apply_priority_rules(ri(category="refunds", subcategory="refund_request", amount=2500), "LOW"
                                ).final_priority == "URGENT"
    assert apply_priority_rules(ri(deadline_hours=24), "LOW").final_priority == "URGENT"
    assert apply_priority_rules(ri(deadline_hours=72), "LOW").final_priority == "MEDIUM"


def test_medium_floor_and_low_default():
    assert apply_priority_rules(ri(category="technical_support", subcategory="app_crash"), "LOW"
                                ).final_priority == "MEDIUM"
    assert apply_priority_rules(ri(issue_type="inquiry", category="product_subscription",
                                   subcategory="plan_inclusions"), "LOW").final_priority == "LOW"


# --- 6. mandatory escalation cannot be overridden by the LLM ---------------
@pytest.mark.parametrize("llm_priority", ["LOW", "MEDIUM", None, "garbage"])
def test_llm_cannot_lower_mandatory_escalation(llm_priority):
    out = apply_priority_rules(
        ri(category="billing_payments", subcategory="duplicate_charge", evidence=["two deductions of $89.99"]),
        llm_priority,
    )
    assert out.final_priority == "URGENT"


def test_llm_urgent_without_rule_is_lowered_and_flagged():
    out = apply_priority_rules(ri(category="delivery_orders", subcategory="order_status"), "URGENT")
    assert out.final_priority == "MEDIUM"
    assert out.review_reasons


# --- 7. sentiment alone does not determine priority ------------------------
def test_angry_routine_question_stays_low():
    angry = ri(issue_type="inquiry", category="product_subscription", subcategory="feature_question",
               summary="Furious customer asks how to export contacts",
               evidence=["THIS IS RIDICULOUS", "I am honestly furious", "How do I export my contacts to a CSV file??"])
    out = apply_priority_rules(angry, "URGENT")
    assert out.final_priority == "LOW"
    assert not out.rules_applied  # no rule reacts to sentiment words


def test_calm_security_report_is_still_urgent():
    calm = ri(category="account_access", subcategory="other",
              evidence=["Just letting you know, there is a purchase I don't recognize on my account. No rush."])
    assert apply_priority_rules(calm, "LOW").final_priority == "URGENT"
