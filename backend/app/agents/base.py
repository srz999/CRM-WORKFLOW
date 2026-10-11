"""Shared helper: call the LLM and validate against a Pydantic schema."""
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .llm import LLMClient, RetryableLLMError

T = TypeVar("T", bound=BaseModel)


class AgentOutputError(RetryableLLMError):
    """LLM output failed schema validation. Retryable (a new sample may be valid)."""


def call_structured(llm: LLMClient, *, agent: str, system: str, payload: dict[str, Any], schema: type[T]) -> T:
    raw = llm.complete_json(agent=agent, system=system, payload=payload)
    try:
        return schema.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or "output"
        raise AgentOutputError(f"{agent}: invalid output ({loc}: {first.get('msg')})") from e
