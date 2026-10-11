"""HTTP API: ticket management, dashboard data and the sample dataset."""
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Ticket, TicketEvent

EXPECTED = json.loads((Path(__file__).parent / "fixtures" / "expected_outcomes.json").read_text())


def process_sample(client, sid):
    s = next(x for x in client.get("/api/samples").json() if x["sample_id"] == sid)
    r = client.post("/api/conversations", json={"text": s["text"], "customer_ref": s["customer_ref"],
                                                "sample_id": sid})
    assert r.status_code == 200, r.text
    return r.json()


def test_input_validation(client, use_llm):
    assert client.post("/api/conversations", json={"text": "   "}).status_code == 422
    assert client.post("/api/conversations", json={"text": "valid text here", "customer_ref": "bad ref!"}
                       ).status_code == 422
    assert client.patch("/api/tickets/999", json={"status": "open"}).status_code == 404


# 11 ------------------------------------------------------------------------
def test_status_and_assignment_changes_are_persisted_and_recorded(client, use_llm):
    res = process_sample(client, "S04")
    tid = res["conversation"]["tickets"][0]["id"]

    r = client.patch(f"/api/tickets/{tid}", json={"status": "in_progress", "actor": "maria", "note": "on it"})
    assert r.status_code == 200 and r.json()["status"] == "in_progress"
    r = client.patch(f"/api/tickets/{tid}", json={"team": "Billing", "actor": "maria"})
    assert r.json()["team"] == "Billing"
    assert client.patch(f"/api/tickets/{tid}", json={"team": "Hackers"}).status_code == 422
    assert client.patch(f"/api/tickets/{tid}", json={"status": "closed"}).status_code == 409  # invalid transition
    r = client.patch(f"/api/tickets/{tid}", json={"status": "resolved"})
    assert r.status_code == 200

    with session_scope() as s:  # persisted in PostgreSQL
        t = s.get(Ticket, tid)
        assert (t.status, t.team) == ("resolved", "Billing")
        events = s.scalars(select(TicketEvent).where(TicketEvent.ticket_id == tid).order_by(TicketEvent.id)).all()
        assert [(e.event_type, e.from_value, e.to_value) for e in events] == [
            ("created", None, "open"),
            ("status_change", "open", "in_progress"),
            ("assignment_change", "Customer Success", "Billing"),
            ("status_change", "in_progress", "resolved"),
        ]
        assert events[1].actor == "maria" and "on it" in events[1].description

    detail = client.get(f"/api/tickets/{tid}").json()
    assert len(detail["events"]) == 4


def test_review_gate_and_review_completion(client, use_llm):
    res = process_sample(client, "S11")
    t = res["conversation"]["tickets"][0]
    assert t["status"] == "needs_review" and t["review_required"]
    tid = t["id"]
    assert client.patch(f"/api/tickets/{tid}", json={"status": "in_progress"}).status_code == 409
    r = client.post(f"/api/tickets/{tid}/review", json={"note": "asked for order number", "actor": "lead"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "open" and not body["review_required"] and body["issue"]["review_status"] == "approved"
    assert client.patch(f"/api/tickets/{tid}", json={"status": "waiting_on_customer"}).status_code == 200
    assert client.post(f"/api/tickets/{tid}/review", json={}).status_code == 409


# 12 ------------------------------------------------------------------------
def test_dashboard_data_comes_from_backend(client, use_llm):
    empty = client.get("/api/stats").json()
    assert empty["total"] == 0

    process_sample(client, "S01")
    process_sample(client, "S15")
    stats = client.get("/api/stats").json()
    with session_scope() as s:
        rows = s.scalars(select(Ticket)).all()
        db_prio = Counter(t.priority for t in rows)
    assert stats["total"] == len(rows) == 5
    assert stats["by_priority"] == {p: db_prio.get(p, 0) for p in ("URGENT", "MEDIUM", "LOW")}
    assert stats["open"] == 5 and stats["resolved"] == 0
    assert sum(len(v) for v in stats["grouped_by_priority"].values()) == 5

    tickets = client.get("/api/tickets").json()
    assert len(tickets) == 5
    assert all(t["ticket_number"].startswith("TKT-") for t in tickets)
    assert len(client.get("/api/tickets", params={"priority": "URGENT"}).json()) == db_prio["URGENT"]
    assert len(client.get("/api/tickets", params={"category": "delivery_orders"}).json()) == 2
    assert len(client.get("/api/tickets", params={"status": "open"}).json()) == 5
    assert {t["customer_ref"] for t in client.get("/api/tickets", params={"q": "cust-2305"}).json()} == {"CUST-2305"}
    assert len(client.get("/api/tickets", params={"q": "SPRING20"}).json()) == 1
    tid = tickets[0]["id"]
    assert [t["id"] for t in client.get("/api/tickets", params={"q": f"TKT-{tid:05d}"}).json()] == [tid]

    # changes are reflected immediately
    client.patch(f"/api/tickets/{tid}", json={"status": "closed"})
    assert client.get("/api/stats").json()["resolved"] == 1


# 13 ------------------------------------------------------------------------
def test_sample_dataset_loads_and_processes(client, use_llm):
    samples = client.get("/api/samples").json()
    assert len(samples) >= 12
    seeded = client.post("/api/samples/seed").json()
    assert seeded["conversations"] == len(samples)
    assert len(client.get("/api/conversations").json()) == len(samples)
    assert client.post("/api/samples/seed").json()["conversations"] == len(samples)  # idempotent
    assert len(client.get("/api/conversations").json()) == len(samples)

    mismatches = []
    for s in samples:
        res = process_sample(client, s["sample_id"])
        assert res["run"]["status"] == "completed", (s["sample_id"], res["run"])
        got = sorted((t["category"], t["priority"], t["customer_ref"] or "") for t in res["conversation"]["tickets"])
        exp = sorted((e["category"], e["priority"], e["customer_ref"] or "") for e in EXPECTED[s["sample_id"]])
        if got != exp:
            mismatches.append((s["sample_id"], got, exp))
        for e in EXPECTED[s["sample_id"]]:
            if e.get("review_required"):
                assert any(t["review_required"] for t in res["conversation"]["tickets"])
    assert not mismatches, mismatches

    stats = client.get("/api/stats").json()
    assert all(stats["by_priority"][p] > 0 for p in ("URGENT", "MEDIUM", "LOW"))
    # seeded conversation records were reused, not duplicated
    assert len(client.get("/api/conversations").json()) == len(samples)


def test_llm_not_configured_fails_safely(client):
    r = client.post("/api/conversations", json={"text": "my app crashes on login", "customer_ref": "C-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["status"] == "failed"
    assert "OPENAI_API_KEY" in body["run"]["error"] and "sk-" not in json.dumps(body)


def test_meta_and_health(client):
    assert client.get("/api/health").json()["database"] == "ok"
    meta = client.get("/api/meta").json()
    assert len(meta["categories"]) == 8 and "Triage" in meta["teams"]
