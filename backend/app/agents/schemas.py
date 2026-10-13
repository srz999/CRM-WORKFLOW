"""Structured output schemas for each agent. LLM output is never trusted until it validates here.

Real models drift on JSON shape (a single string where a list is expected, "$89.99" for a number,
"MEDIUM " with a trailing space). Harmless shape differences are normalised in `mode="before"`
validators; anything that changes meaning (unknown category, invalid priority, empty summary) still
fails validation and goes through the workflow's bounded retry / fallback path.
"""
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..rules.config import is_valid_classification

_NULLISH = {"", "null", "none", "n/a", "na", "unknown", "not stated", "not provided", "-"}
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def as_str_list(v: Any, limit: int) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    elif isinstance(v, dict):
        v = list(v.values())
    elif not isinstance(v, (list, tuple)):
        v = [v]
    out = []
    for item in v:
        s = str(item).strip() if item is not None else ""
        if s and s.lower() not in _NULLISH:
            out.append(s)
    return out[:limit]


def as_opt_str(v: Any, limit: int) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(x) for x in v if x is not None)
    s = str(v).strip()
    return None if s.lower() in _NULLISH else s[:limit]


def as_opt_number(v: Any) -> float | None:
    """'$89.99' -> 89.99, '35 employees' -> 35, 'all' -> None. Never invents a value."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    m = _NUM.search(str(v))
    if not m:
        return None
    n = float(m.group().replace(",", ""))
    return n if n >= 0 else None


def as_confidence(v: Any) -> Any:
    n = as_opt_number(v)
    if n is None:
        return v  # let validation fail
    return n / 100 if 1 < n <= 100 else n


class ExtractedIssue(BaseModel):
    summary: str = Field(min_length=5, max_length=300)
    evidence: list[str] = Field(default_factory=list)
    customer_ref: str | None = Field(default=None, max_length=64)
    customer_impact: str | None = None
    requested_action: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    affected_users: int | None = Field(default=None, ge=0, le=1_000_000)
    amount: float | None = Field(default=None, ge=0)
    deadline_text: str | None = None
    deadline_hours: int | None = Field(default=None, ge=0, le=24 * 365)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, v):
        return as_opt_str(v, 300) or ""

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, v):
        return as_str_list(v, 5)

    @field_validator("missing_information", mode="before")
    @classmethod
    def _missing(cls, v):
        return as_str_list(v, 8)

    @field_validator("customer_ref", mode="before")
    @classmethod
    def _ref(cls, v):
        return as_opt_str(v, 64)

    @field_validator("customer_impact", "requested_action", mode="before")
    @classmethod
    def _text(cls, v):
        return as_opt_str(v, 500)

    @field_validator("deadline_text", mode="before")
    @classmethod
    def _deadline_text(cls, v):
        return as_opt_str(v, 120)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return as_opt_number(v)

    @field_validator("affected_users", "deadline_hours", mode="before")
    @classmethod
    def _ints(cls, v):
        n = as_opt_number(v)
        return int(n) if n is not None else None


class ExtractionOutput(BaseModel):
    issues: list[ExtractedIssue]

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, v):
        if isinstance(v, dict):
            v = [v]
        return (v or [])[:20] if isinstance(v, list) else v


class ClassificationOutput(BaseModel):
    issue_type: Literal["complaint", "inquiry", "request"]
    category: str
    subcategory: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""
    needs_review: bool = False

    @field_validator("issue_type", "category", "subcategory", mode="before")
    @classmethod
    def _key(cls, v):
        return str(v).strip().lower().replace(" ", "_") if v is not None else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return as_confidence(v)

    @field_validator("rationale", mode="before")
    @classmethod
    def _rat(cls, v):
        return as_opt_str(v, 500) or ""

    @model_validator(mode="after")
    def in_taxonomy(self):
        if not is_valid_classification(self.category, self.subcategory):
            raise ValueError(f"classification {self.category}/{self.subcategory} is not in the configured taxonomy")
        return self


class PriorityOutput(BaseModel):
    priority: Literal["URGENT", "MEDIUM", "LOW"]
    factors: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False

    @field_validator("priority", mode="before")
    @classmethod
    def _prio(cls, v):
        return str(v).strip().upper() if v is not None else v

    @field_validator("factors", mode="before")
    @classmethod
    def _factors(cls, v):
        return as_str_list(v, 6)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return as_confidence(v)


class ResolutionOutput(BaseModel):
    next_action: str = Field(min_length=10)
    requires_human_approval: bool
    approval_reason: str | None = None
    ticket_title: str = Field(min_length=5)

    @field_validator("next_action", mode="before")
    @classmethod
    def _action(cls, v):
        return as_opt_str(v, 600) or ""

    @field_validator("ticket_title", mode="before")
    @classmethod
    def _title(cls, v):
        return as_opt_str(v, 120) or ""

    @field_validator("approval_reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return as_opt_str(v, 300)
