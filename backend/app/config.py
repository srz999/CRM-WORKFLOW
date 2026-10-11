"""Application settings loaded from environment variables (and an optional .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/crm"

    # LLM
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None  # optional; leave unset for api.openai.com
    llm_timeout_seconds: float = 30.0
    llm_temperature: float | None = 0.0
    # Bounded retries per workflow stage (applies to retryable failures only)
    workflow_max_stage_attempts: int = 2
    # Upper bound on issues extracted from one conversation
    max_issues_per_conversation: int = 10

    # Optional n8n notification webhook. Core workflow never depends on it.
    n8n_webhook_url: str | None = None
    n8n_timeout_seconds: float = 2.0

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
