"""Structured output schemas for each agent. LLM output is never trusted until it validates here."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..rules.config import is_valid_classification


class ExtractedIssue(BaseModel):
    summary: str = Field(min_length=5, max_length=300)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    customer_ref: str | None = Field(default=None, max_length=64)
    customer_impact: str | None = Field(default=None, max_length=500)
    requested_action: str | None = Field(default=None, max_length=500)
    missing_information: list[str] = Field(default_factory=list, max_length=8)
    affected_users: int | None = Field(default=None, ge=0, le=1_000_000)
    amount: float | None = Field(default=None, ge=0)
    deadline_text: str | None = Field(default=None, max_length=120)
    deadline_hours: int | None = Field(default=None, ge=0, le=24 * 365)

    @field_validator("evidence", "missing_information")
    @classmethod
    def strip_items(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]


class ExtractionOutput(BaseModel):
    issues: list[ExtractedIssue] = Field(max_length=20)


class ClassificationOutput(BaseModel):
    issue_type: Literal["complaint", "inquiry", "request"]
    category: str
    subcategory: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(default="", max_length=500)
    needs_review: bool = False

    @model_validator(mode="after")
    def in_taxonomy(self):
        if not is_valid_classification(self.category, self.subcategory):
            raise ValueError(f"classification {self.category}/{self.subcategory} is not in the configured taxonomy")
        return self


class PriorityOutput(BaseModel):
    priority: Literal["URGENT", "MEDIUM", "LOW"]
    factors: list[str] = Field(min_length=1, max_length=6)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False


class ResolutionOutput(BaseModel):
    next_action: str = Field(min_length=10, max_length=600)
    requires_human_approval: bool
    approval_reason: str | None = Field(default=None, max_length=300)
    ticket_title: str = Field(min_length=5, max_length=120)
