"""Thin LLM client wrapper.

All agents talk to the model through `LLMClient.complete_json`. The OpenAI implementation
classifies failures as retryable (timeout, rate limit, connection, 5xx, malformed JSON) or
not (auth, bad request). Retries themselves are bounded by the workflow graph, not here.
Tests substitute a fake client that returns canned responses.
"""
import json
import logging
from typing import Any, Protocol

from ..config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Non-retryable LLM failure. Message is safe to show to users."""

    retryable = False


class RetryableLLMError(LLMError):
    retryable = True


class LLMNotConfigured(LLMError):
    pass


class LLMClient(Protocol):
    def complete_json(self, *, agent: str, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class OpenAILLM:
    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        if not settings.openai_api_key:
            raise LLMNotConfigured("LLM is not configured: set OPENAI_API_KEY in backend/.env")
        from openai import OpenAI

        self.model = settings.openai_model
        self.temperature = settings.llm_temperature
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # the workflow owns the (bounded) retry policy
        )

    def complete_json(self, *, agent: str, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        import openai

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as e:
            raise RetryableLLMError(f"{agent}: LLM request timed out") from e
        except openai.RateLimitError as e:
            raise RetryableLLMError(f"{agent}: LLM rate limit reached") from e
        except openai.APIConnectionError as e:
            raise RetryableLLMError(f"{agent}: could not connect to the LLM provider") from e
        except openai.InternalServerError as e:
            raise RetryableLLMError(f"{agent}: LLM provider error") from e
        except openai.AuthenticationError as e:
            raise LLMError(f"{agent}: LLM authentication failed - check OPENAI_API_KEY") from e
        except openai.APIStatusError as e:
            log.warning("LLM request rejected (%s) for %s", e.status_code, agent)
            raise LLMError(f"{agent}: LLM request rejected (HTTP {e.status_code})") from e

        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return parse_json_object(content, agent)


def parse_json_object(content: str, agent: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        raise RetryableLLMError(f"{agent}: LLM returned malformed JSON") from e
    if not isinstance(data, dict):
        raise RetryableLLMError(f"{agent}: LLM returned JSON that is not an object")
    return data


def get_llm() -> LLMClient:
    return OpenAILLM()
