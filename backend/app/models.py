"""SQLAlchemy ORM models. PostgreSQL is the source of truth for all business records."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PRIORITIES = ("URGENT", "MEDIUM", "LOW")
ISSUE_TYPES = ("complaint", "inquiry", "request", "unknown")
CONVERSATION_STATUSES = ("received", "processing", "completed", "partially_completed", "failed")
RUN_STATUSES = ("running", "completed", "partially_completed", "failed")
ISSUE_PROCESSING_STATUSES = ("pending", "ticketed", "failed")
REVIEW_STATUSES = ("not_required", "required", "approved")
TICKET_STATUSES = ("open", "needs_review", "in_progress", "waiting_on_customer", "resolved", "closed")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(_in("status", CONVERSATION_STATUSES), name="ck_conversations_status"),
        Index("ix_conversations_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    sample_id: Mapped[str | None] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256(customer_ref + normalized text) – identical resubmissions map to the same conversation
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    customer: Mapped[Customer | None] = relationship()
    issues: Mapped[list["Issue"]] = relationship(back_populates="conversation", order_by="Issue.ordinal")


class Issue(TimestampMixin, Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(_in("issue_type", ISSUE_TYPES), name="ck_issues_type"),
        CheckConstraint(
            "recommended_priority IS NULL OR " + _in("recommended_priority", PRIORITIES), name="ck_issues_priority"
        ),
        CheckConstraint(_in("processing_status", ISSUE_PROCESSING_STATUSES), name="ck_issues_processing_status"),
        CheckConstraint(_in("review_status", REVIEW_STATUSES), name="ck_issues_review_status"),
        Index("ix_issues_conversation_ordinal", "conversation_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Deterministic idempotency key (conversation + ordinal + normalized summary)
    issue_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    issue_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    customer_impact: Mapped[str | None] = mapped_column(Text)
    requested_action: Mapped[str | None] = mapped_column(Text)
    missing_information: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    classification_confidence: Mapped[float | None] = mapped_column(Float)
    llm_priority: Mapped[str | None] = mapped_column(String(8))
    recommended_priority: Mapped[str | None] = mapped_column(String(8))
    priority_factors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    rules_applied: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_required")
    review_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    processing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="issues")
    customer: Mapped[Customer | None] = relationship()
    ticket: Mapped["Ticket | None"] = relationship(back_populates="issue", uselist=False)


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(_in("priority", PRIORITIES), name="ck_tickets_priority"),
        CheckConstraint(_in("status", TICKET_STATUSES), name="ck_tickets_status"),
        Index("ix_tickets_priority", "priority"),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_category", "category"),
        Index("ix_tickets_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # unique: one ticket per issue, so workflow retries upsert instead of duplicating
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), unique=True, nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    team: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_reason: Mapped[str | None] = mapped_column(Text)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    sla_hours: Mapped[int | None] = mapped_column(Integer)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    issue: Mapped[Issue] = relationship(back_populates="ticket")
    customer: Mapped[Customer | None] = relationship()
    conversation: Mapped[Conversation] = relationship()
    events: Mapped[list["TicketEvent"]] = relationship(
        back_populates="ticket", order_by="TicketEvent.id", cascade="all, delete-orphan"
    )

    @property
    def ticket_number(self) -> str:
        return format_ticket_number(self.id)


def format_ticket_number(ticket_id: int) -> str:
    return f"TKT-{ticket_id:05d}"


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (CheckConstraint(_in("status", RUN_STATUSES), name="ck_workflow_runs_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    issues_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tickets_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tickets_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Inspectable summary of the final LangGraph state (per-issue outcomes, stage attempts, errors)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str | None] = mapped_column(String(32))
    from_value: Mapped[str | None] = mapped_column(String(200))
    to_value: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="events")
