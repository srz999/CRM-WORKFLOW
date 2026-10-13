import os

# Point everything at the test database BEFORE the app is imported.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/crm_test"
)
os.environ["OPENAI_API_KEY"] = ""  # tests must never call the real API

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine, reset_engine  # noqa: E402

from .fake_llm import SAMPLE_TEXT, FakeLLM  # noqa: E402

BACKEND = Path(__file__).parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    get_settings.cache_clear()
    reset_engine()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")  # verifies the migrations themselves
    yield


@pytest.fixture(autouse=True)
def clean_db():
    with get_engine().begin() as c:
        c.execute(text("TRUNCATE ticket_events, tickets, issues, workflow_runs, conversations, customers "
                       "RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def use_llm(monkeypatch):
    """Install a FakeLLM as the app's LLM; returns a setter for custom instances."""
    holder = {"llm": FakeLLM()}
    monkeypatch.setattr("app.workflow.runner.get_llm", lambda: holder["llm"])

    def set_llm(llm):
        holder["llm"] = llm
        return llm

    set_llm.current = lambda: holder["llm"]
    return set_llm


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def sample_text():
    return SAMPLE_TEXT
