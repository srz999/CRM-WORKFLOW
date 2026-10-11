from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import TICKET_STATUSES, Customer, Issue, Ticket, TicketEvent
from ..rules.config import teams
from ..schemas import ReviewIn, StatsOut, TicketDetail, TicketSummary, TicketUpdate
from .serializers import TICKET_LOAD, ticket_detail, ticket_summary

router = APIRouter(prefix="/api", tags=["tickets"])

CLOSED_STATUSES = ("resolved", "closed")
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "waiting_on_customer", "resolved", "closed", "needs_review"},
    "needs_review": {"open", "closed"},
    "in_progress": {"open", "waiting_on_customer", "resolved"},
    "waiting_on_customer": {"in_progress", "resolved", "closed"},
    "resolved": {"closed", "open"},
    "closed": {"open"},
}
REVIEW_GATED = {"in_progress", "waiting_on_customer", "resolved"}


def _parse_ticket_id(q: str) -> int | None:
    q = q.strip().upper()
    if q.startswith("TKT-"):
        q = q[4:]
    return int(q) if q.isdigit() else None


@router.get("/tickets", response_model=list[TicketSummary])
def list_tickets(
    db: Session = Depends(get_db),
    priority: str | None = Query(None, pattern="^(URGENT|MEDIUM|LOW)$"),
    category: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=32),
    team: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200),
    conversation_id: int | None = None,
    overdue: bool = False,
    limit: int = Query(200, ge=1, le=500),
):
    stmt = select(Ticket).options(*TICKET_LOAD).join(Issue).outerjoin(Customer, Ticket.customer_id == Customer.id)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if category:
        stmt = stmt.where(Ticket.category == category)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if team:
        stmt = stmt.where(Ticket.team == team)
    if conversation_id:
        stmt = stmt.where(Ticket.conversation_id == conversation_id)
    if overdue:
        stmt = stmt.where(Ticket.due_at < datetime.now(timezone.utc), Ticket.status.not_in(CLOSED_STATUSES))
    if q and q.strip():
        like = f"%{q.strip()}%"
        conds = [Issue.summary.ilike(like), Customer.reference.ilike(like), Ticket.title.ilike(like),
                 cast(Ticket.id, String).ilike(like)]
        tid = _parse_ticket_id(q)
        if tid is not None:
            conds.append(Ticket.id == tid)
        stmt = stmt.where(or_(*conds))
    stmt = stmt.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit)
    return [ticket_summary(t) for t in db.scalars(stmt).unique().all()]


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    t = db.scalars(
        select(Ticket).options(*TICKET_LOAD, selectinload(Ticket.events), selectinload(Ticket.conversation))
        .where(Ticket.id == ticket_id)
    ).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return ticket_detail(db, t)


@router.patch("/tickets/{ticket_id}", response_model=TicketDetail)
def update_ticket(ticket_id: int, body: TicketUpdate, db: Session = Depends(get_db)):
    t = db.scalars(select(Ticket).where(Ticket.id == ticket_id).with_for_update()).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    if body.status is None and body.team is None:
        raise HTTPException(422, "Provide a status and/or team to change")

    if body.status and body.status != t.status:
        if body.status not in ALLOWED_TRANSITIONS.get(t.status, set()):
            raise HTTPException(409, f"Cannot change status from {t.status} to {body.status}")
        if t.review_required and body.status in REVIEW_GATED:
            raise HTTPException(409, "This ticket requires human review before work can start")
        db.add(TicketEvent(
            ticket_id=t.id, event_type="status_change", field="status", from_value=t.status, to_value=body.status,
            description=f"Status changed from {t.status} to {body.status}" + (f": {body.note}" if body.note else ""),
            actor=body.actor,
        ))
        t.status = body.status

    if body.team and body.team != t.team:
        if body.team not in teams():
            raise HTTPException(422, f"Unknown team {body.team!r}")
        db.add(TicketEvent(
            ticket_id=t.id, event_type="assignment_change", field="team", from_value=t.team, to_value=body.team,
            description=f"Reassigned from {t.team} to {body.team}" + (f": {body.note}" if body.note else ""),
            actor=body.actor,
        ))
        t.team = body.team

    t.updated_at = func.now()
    db.commit()
    db.expire_all()
    return get_ticket(ticket_id, db)


@router.post("/tickets/{ticket_id}/review", response_model=TicketDetail)
def complete_review(ticket_id: int, body: ReviewIn, db: Session = Depends(get_db)):
    t = db.scalars(select(Ticket).options(selectinload(Ticket.issue)).where(Ticket.id == ticket_id)
                   .with_for_update(of=Ticket)).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    if not t.review_required:
        raise HTTPException(409, "Ticket does not require review")
    t.review_required = False
    t.issue.review_status = "approved"
    prev = t.status
    if t.status == "needs_review":
        t.status = "open"
    db.add(TicketEvent(
        ticket_id=t.id, event_type="review_completed", field="status", from_value=prev, to_value=t.status,
        description="Human review completed" + (f": {body.note}" if body.note else ""), actor=body.actor,
    ))
    t.updated_at = func.now()
    db.commit()
    db.expire_all()
    return get_ticket(ticket_id, db)


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    by_priority = {p: 0 for p in ("URGENT", "MEDIUM", "LOW")}
    for p, n in db.execute(select(Ticket.priority, func.count()).group_by(Ticket.priority)):
        by_priority[p] = n
    by_status = {s: 0 for s in TICKET_STATUSES}
    for s, n in db.execute(select(Ticket.status, func.count()).group_by(Ticket.status)):
        by_status[s] = n
    total = sum(by_priority.values())
    resolved = sum(by_status[s] for s in CLOSED_STATUSES)
    needs_review = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.review_required.is_(True)))
    grouped: dict[str, list[TicketSummary]] = {}
    for p in by_priority:
        rows = db.scalars(
            select(Ticket).options(*TICKET_LOAD)
            .where(Ticket.priority == p, Ticket.status.not_in(CLOSED_STATUSES))
            .order_by(Ticket.created_at.desc()).limit(8)
        ).all()
        grouped[p] = [ticket_summary(t) for t in rows]
    return StatsOut(
        total=total, by_priority=by_priority, by_status=by_status, open=total - resolved, resolved=resolved,
        needs_review=needs_review or 0, grouped_by_priority=grouped,
    )
