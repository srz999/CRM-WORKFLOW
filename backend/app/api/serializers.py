from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Conversation, Ticket, WorkflowRun
from ..rules.config import category_label
from ..schemas import (
    ConversationOut,
    CustomerOut,
    IssueOut,
    TicketDetail,
    TicketEventOut,
    TicketSummary,
    WorkflowRunOut,
)

TICKET_LOAD = (selectinload(Ticket.issue), selectinload(Ticket.customer))


def ticket_summary(t: Ticket) -> TicketSummary:
    return TicketSummary(
        id=t.id,
        issue_id=t.issue_id,
        ticket_number=t.ticket_number,
        customer_ref=t.customer.reference if t.customer else None,
        summary=t.issue.summary,
        title=t.title,
        category=t.category,
        category_label=category_label(t.category),
        subcategory=t.subcategory,
        priority=t.priority,
        team=t.team,
        status=t.status,
        review_required=t.review_required,
        conversation_id=t.conversation_id,
        due_at=t.due_at,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def ticket_detail(session: Session, t: Ticket) -> TicketDetail:
    base = ticket_summary(t).model_dump()
    return TicketDetail(
        **base,
        description=t.description,
        recommended_action=t.recommended_action,
        requires_human_approval=t.requires_human_approval,
        approval_reason=t.approval_reason,
        sla_hours=t.sla_hours,
        customer=CustomerOut.model_validate(t.customer) if t.customer else None,
        conversation_text=t.conversation.text,
        issue=IssueOut.model_validate(t.issue),
        events=[TicketEventOut.model_validate(e) for e in t.events],
    )


def conversation_out(session: Session, conv: Conversation) -> ConversationOut:
    tickets = session.scalars(
        select(Ticket).options(*TICKET_LOAD).where(Ticket.conversation_id == conv.id).order_by(Ticket.id)
    ).all()
    run = session.scalars(
        select(WorkflowRun).where(WorkflowRun.conversation_id == conv.id).order_by(WorkflowRun.id.desc()).limit(1)
    ).first()
    return ConversationOut(
        id=conv.id,
        customer_ref=conv.customer.reference if conv.customer else None,
        sample_id=conv.sample_id,
        text=conv.text,
        status=conv.status,
        error=conv.error,
        created_at=conv.created_at,
        issues=[IssueOut.model_validate(i) for i in conv.issues],
        tickets=[ticket_summary(t) for t in tickets],
        latest_run=WorkflowRunOut.model_validate(run) if run else None,
    )
