"""LangGraph state schemas.

WorkflowState - one conversation, owned by the orchestrator graph.
IssueState    - one issue, processed in its own bounded sub-graph. Each issue carries its
                issue_id, conversation_id and customer reference so parallel branches can
                never mix records.
"""
import operator
from typing import Annotated, Any, TypedDict


class WorkflowState(TypedDict, total=False):
    run_id: int
    conversation_id: int
    conversation_text: str
    customer_ref: str | None

    stage_status: str  # ok | retry | failed
    extraction_attempts: int
    errors: Annotated[list[str], operator.add]
    notes: Annotated[list[str], operator.add]

    extracted: list[dict]  # validated, not yet persisted
    reused_checkpoint: bool  # issues were already extracted by an earlier run
    pending: list[dict]  # persisted issues still needing a ticket
    already_ticketed: int

    results: Annotated[list[dict], operator.add]  # one entry per processed issue
    status: str
    error: str | None


class IssueTask(TypedDict):
    """Payload sent to each parallel `process_issue` branch."""

    run_id: int
    conversation_id: int
    issue_id: int
    issue_key: str
    customer_id: int | None
    customer_ref: str | None
    issue: dict[str, Any]
    initial_review_reasons: list[str]


class IssueState(TypedDict, total=False):
    run_id: int
    conversation_id: int
    issue_id: int
    issue_key: str
    customer_id: int | None
    customer_ref: str | None
    issue: dict[str, Any]

    attempts: dict[str, int]
    stage_status: str  # ok | retry | fallback
    errors: Annotated[list[str], operator.add]
    review_reasons: Annotated[list[str], operator.add]

    classification: dict[str, Any]
    priority_llm: dict[str, Any] | None
    priority: dict[str, Any]  # final: {final, llm_priority, factors, rules_applied}
    escalated: bool
    recommendation: dict[str, Any]

    ticket_id: int | None
    ticket_created: bool
    outcome: str  # ticketed | failed
