"""Pydantic request/response schemas for the HTTP API."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Priority = Literal["URGENT", "MEDIUM", "LOW"]
TicketStatus = Literal["open", "needs_review", "in_progress", "waiting_on_customer", "resolved", "closed"]


class ConversationIn(BaseModel):
    text: str = Field(min_length=10, max_length=20_000)
    customer_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_\-\.]+$")
    customer_name: str | None = Field(default=None, max_length=200)
    sample_id: str | None = Field(default=None, max_length=32)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("conversation text is empty")
        return v

    @field_validator("customer_ref", "customer_name")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    name: str | None
    email: str | None
    phone: str | None


class TicketSummary(BaseModel):
    id: int
    ticket_number: str
    customer_ref: str | None
    summary: str
    title: str
    category: str
    category_label: str
    subcategory: str | None
    priority: Priority
    team: str
    status: TicketStatus
    review_required: bool
    conversation_id: int
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TicketEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    field: str | None
    from_value: str | None
    to_value: str | None
    description: str
    actor: str
    created_at: datetime


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ordinal: int
    issue_type: str
    category: str | None
    subcategory: str | None
    summary: str
    evidence: list
    customer_impact: str | None
    requested_action: str | None
    missing_information: list
    facts: dict
    classification_confidence: float | None
    llm_priority: str | None
    recommended_priority: str | None
    priority_factors: list
    rules_applied: list
    review_status: str
    review_reasons: list
    processing_status: str
    error: str | None


class TicketDetail(TicketSummary):
    description: str
    recommended_action: str
    requires_human_approval: bool
    approval_reason: str | None
    sla_hours: int | None
    customer: CustomerOut | None
    conversation_text: str
    issue: IssueOut
    events: list[TicketEventOut]


class TicketUpdate(BaseModel):
    status: TicketStatus | None = None
    team: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    actor: str = Field(default="agent", min_length=1, max_length=100)


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    actor: str = Field(default="reviewer", min_length=1, max_length=100)


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    current_stage: str
    error: str | None
    issues_found: int
    tickets_created: int
    tickets_updated: int
    issues_failed: int
    state: dict
    started_at: datetime
    completed_at: datetime | None


class ConversationOut(BaseModel):
    id: int
    customer_ref: str | None
    sample_id: str | None
    text: str
    status: str
    error: str | None
    created_at: datetime
    issues: list[IssueOut]
    tickets: list[TicketSummary]
    latest_run: WorkflowRunOut | None


class ProcessResult(BaseModel):
    conversation: ConversationOut
    run: WorkflowRunOut


class StatsOut(BaseModel):
    total: int
    by_priority: dict[str, int]
    by_status: dict[str, int]
    open: int
    resolved: int
    needs_review: int
    grouped_by_priority: dict[str, list[TicketSummary]]


class SampleOut(BaseModel):
    sample_id: str
    title: str
    customer_ref: str | None
    customer_name: str | None
    text: str


class SeedResult(BaseModel):
    customers: int
    conversations: int


class MetaOut(BaseModel):
    categories: list[dict]
    teams: list[str]
    statuses: list[str]
    priorities: list[str]
    llm_configured: bool
    model: str
