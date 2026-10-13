"""Model-based evaluation against the REAL LLM (costs API calls; not part of pytest).

Processes every sample through the full workflow with the configured OpenAI model, then
compares the resulting tickets to tests/fixtures/expected_outcomes.json and prints measured
agreement. Run against a scratch database:

    DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/crm_eval python -m evals.run_eval
"""
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.api.conversations import upsert_conversation
from app.api.meta import load_samples
from app.db import session_scope
from app.models import Ticket, WorkflowRun
from app.workflow.runner import run_workflow

EXPECTED = json.loads((Path(__file__).parents[1] / "tests" / "fixtures" / "expected_outcomes.json").read_text())


def match(expected: list[dict], got: list[dict]) -> tuple[int, int, int]:
    """Greedy match on (customer, category); returns (issue-count ok, category hits, priority hits)."""
    remaining = list(got)
    cat_hits = prio_hits = 0
    for e in expected:
        cand = next((g for g in remaining if g["category"] == e["category"] and g["customer"] == e["customer_ref"]),
                    None)
        if cand:
            remaining.remove(cand)
            cat_hits += 1
            prio_hits += cand["priority"] == e["priority"]
    return int(len(got) == len(expected)), cat_hits, prio_hits


def main() -> int:
    totals = Counter()
    for s in load_samples():
        sid = s["sample_id"]
        ref = (s.get("customer") or {}).get("reference")
        conv_id = upsert_conversation(s["text"], ref, sample_id=sid)
        run_id = run_workflow(conv_id)
        with session_scope() as db:
            run = db.get(WorkflowRun, run_id)
            got = [{"category": t.category, "priority": t.priority,
                    "customer": t.customer.reference if t.customer else None}
                   for t in db.scalars(select(Ticket).where(Ticket.conversation_id == conv_id))]
            status, error = run.status, run.error
        exp = EXPECTED[sid]
        count_ok, cat, prio = match(exp, got)
        totals.update(samples=1, count_ok=count_ok, expected=len(exp), cat=cat, prio=prio,
                      failed=status == "failed")
        print(f"{sid} run={status:<20} issues {len(got)}/{len(exp)}  category {cat}/{len(exp)}  priority {prio}/{len(exp)}")
        if error:
            print(f"    error: {error}")

    n = totals["expected"] or 1
    print("\nMeasured on", totals["samples"], "samples:")
    print(f"  issue count exact: {totals['count_ok']}/{totals['samples']}")
    print(f"  category accuracy: {totals['cat']}/{n} = {totals['cat'] / n:.0%}")
    print(f"  priority accuracy: {totals['prio']}/{n} = {totals['prio'] / n:.0%}")
    print(f"  failed runs: {totals['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
