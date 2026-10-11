from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, session_scope
from ..models import Conversation, WorkflowRun
from ..schemas import ConversationIn, ConversationOut, ProcessResult, WorkflowRunOut
from ..workflow.persistence import get_or_create_customer
from ..workflow.runner import ConversationBusy, run_workflow
from ..workflow.validation import conversation_hash
from .serializers import conversation_out

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def upsert_conversation(text: str, customer_ref: str | None, customer_name: str | None = None,
                        sample_id: str | None = None, email: str | None = None, phone: str | None = None) -> int:
    """Store a conversation once. Identical text for the same customer maps to the same record."""
    with session_scope() as s:
        customer_id = (
            get_or_create_customer(s, customer_ref, name=customer_name, email=email, phone=phone)
            if customer_ref else None
        )
        h = conversation_hash(text, customer_ref)
        s.execute(
            pg_insert(Conversation)
            .values(customer_id=customer_id, sample_id=sample_id, text=text.strip(), content_hash=h, status="received")
            .on_conflict_do_nothing(index_elements=["content_hash"])
        )
        conv = s.scalars(select(Conversation).where(Conversation.content_hash == h)).one()
        if sample_id and not conv.sample_id:
            conv.sample_id = sample_id
        return conv.id


def _load(db: Session, conversation_id: int) -> Conversation:
    conv = db.scalars(
        select(Conversation).options(selectinload(Conversation.issues), selectinload(Conversation.customer))
        .where(Conversation.id == conversation_id)
    ).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


def _process(db: Session, conversation_id: int) -> ProcessResult:
    try:
        run_id = run_workflow(conversation_id)
    except ConversationBusy as e:
        raise HTTPException(409, str(e)) from e
    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    return ProcessResult(conversation=conversation_out(db, _load(db, conversation_id)),
                         run=WorkflowRunOut.model_validate(run))


@router.post("", response_model=ProcessResult)
def submit_conversation(body: ConversationIn, db: Session = Depends(get_db)):
    """Store the conversation and run the multi-agent workflow synchronously."""
    conv_id = upsert_conversation(body.text, body.customer_ref, body.customer_name, body.sample_id)
    return _process(db, conv_id)


@router.post("/{conversation_id}/process", response_model=ProcessResult)
def reprocess(conversation_id: int, db: Session = Depends(get_db)):
    """Retry a conversation. Already-ticketed issues are skipped; no duplicate tickets are created."""
    _load(db, conversation_id)
    return _process(db, conversation_id)


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), limit: int = 50):
    convs = db.scalars(
        select(Conversation).options(selectinload(Conversation.issues), selectinload(Conversation.customer))
        .order_by(Conversation.id.desc()).limit(min(limit, 200))
    ).all()
    return [conversation_out(db, c) for c in convs]


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    return conversation_out(db, _load(db, conversation_id))
