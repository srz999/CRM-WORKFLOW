"""Entry point used by the API: run the orchestrator for one stored conversation."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..agents.llm import LLMClient, LLMError, get_llm
from ..config import get_settings
from ..db import session_scope
from ..models import Conversation, WorkflowRun
from . import persistence as db
from .graph import build_conversation_graph

log = logging.getLogger(__name__)

STALE_RUN_MINUTES = 10


class ConversationBusy(Exception):
    pass


def start_run(conversation_id: int) -> int:
    """Create a workflow run, refusing if another run for this conversation is in progress."""
    with session_scope() as s:
        conv = s.scalars(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        ).one()
        if conv.status == "processing" and conv.updated_at > datetime.now(timezone.utc) - timedelta(
            minutes=STALE_RUN_MINUTES
        ):
            raise ConversationBusy(f"Conversation {conversation_id} is already being processed")
        conv.status = "processing"
        conv.error = None
        run = WorkflowRun(conversation_id=conversation_id, status="running", current_stage="received", state={})
        s.add(run)
        s.flush()
        return run.id


def run_workflow(conversation_id: int, llm: LLMClient | None = None) -> int:
    run_id = start_run(conversation_id)
    with session_scope() as s:
        conv = s.get(Conversation, conversation_id)
        text = conv.text
        customer_ref = conv.customer.reference if conv.customer else None

    try:
        llm = llm or get_llm()
    except LLMError as e:
        db.finalize_run(run_id, status="failed", error=str(e), counters={}, state={"errors": [str(e)]})
        return run_id

    try:
        graph = build_conversation_graph(llm, get_settings())
        final = graph.invoke(
            {
                "run_id": run_id, "conversation_id": conversation_id, "conversation_text": text,
                "customer_ref": customer_ref, "errors": [], "notes": [], "results": [],
                "extraction_attempts": 0,
            },
            {"recursion_limit": 50},
        )
    except Exception as e:  # noqa: BLE001
        log.exception("workflow crashed for conversation %s", conversation_id)
        db.finalize_run(run_id, status="failed", error=f"Unexpected workflow error ({type(e).__name__})",
                        counters={}, state={})
        return run_id

    results = final.get("results", [])
    created_ids = [r["ticket_id"] for r in results if r.get("created")]
    counters = {
        "issues_found": len(results) + final.get("already_ticketed", 0),
        "tickets_created": len(created_ids),
        "tickets_existing": final.get("already_ticketed", 0) + sum(
            1 for r in results if r["outcome"] == "ticketed" and not r.get("created")
        ),
        "issues_failed": sum(1 for r in results if r["outcome"] != "ticketed"),
    }
    state_summary = {
        "extraction_attempts": final.get("extraction_attempts", 0),
        "reused_checkpoint": final.get("reused_checkpoint", False),
        "notes": final.get("notes", []),
        "errors": final.get("errors", []),
        "issues": results,
    }
    db.finalize_run(run_id, status=final.get("status", "failed"), error=final.get("error"),
                    counters=counters, state=state_summary)

    return run_id
