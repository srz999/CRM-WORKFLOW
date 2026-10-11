# Multi-Agent AI-Powered CRM Workflow

This app turns customer conversation text into individual CRM tickets. Each ticket is categorized, prioritized and comes with a recommended action. The pieces are:

- **FastAPI** for the backend.
- **LangGraph** for orchestration: one orchestrator plus four specialist agents.
- **OpenAI** for the LLM steps.
- **PostgreSQL** as the source of truth.
- **React (Vite)** for the dashboard.
- **n8n** for optional urgent-ticket notifications.

Everything runs locally. Speech-to-text, RAG, external CRMs and auth are not included.

---

## 1. Quick start

Prerequisites: Python 3.11+, Node 18+, and Docker (or your own PostgreSQL 14+).

```bash
# 1. Database
docker compose up -d postgres          # creates crm, crm_test and crm_eval

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # then set OPENAI_API_KEY in .env
alembic upgrade head                   # create the schema
python -m app.seed                     # optional: load the 15 sample conversations
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev                            # http://localhost:5173  (proxies /api to :8000)
```

If you use your own PostgreSQL instead of Docker, create the databases yourself (`CREATE DATABASE crm; CREATE DATABASE crm_test;`) and set `DATABASE_URL` in `backend/.env`.

### Run the sample workflow

1. Open http://localhost:5173.
2. Click **Load samples into database**. This seeds customer and conversation metadata only, never results.
3. Pick a sample, for example **S15 · Three distinct issues in one conversation**, and click **Process conversation**.
4. The result panel lists each extracted issue next to the ticket it produced. S15 gives three tickets.
5. Click a ticket to inspect it. You'll see the evidence, classification, priority factors, recommended action and event history.
6. Change the ticket's status or team, or complete a human review. Every change is written to `ticket_events`.
7. Submit the same sample again. The run reports *"already existed (no duplicates)"*.

To check that the records persisted:

```bash
psql postgresql://postgres:postgres@localhost:5432/crm \
  -c "select id, priority, status, team, category from tickets order by id;" \
  -c "select ticket_id, event_type, from_value, to_value, actor from ticket_events order by id;"
```

You can also process conversations through the API:

```bash
curl -X POST localhost:8000/api/conversations -H 'content-type: application/json' \
  -d '{"text": "My app crashes whenever I open my account page.", "customer_ref": "CUST-9001"}'
```

Interactive API docs are at http://localhost:8000/docs.

---

## 2. Architecture

```
React dashboard ──/api──▶ FastAPI ──▶ LangGraph orchestrator ──▶ OpenAI (4 agents)
                              │                │
                              └──── SQLAlchemy ┴──▶ PostgreSQL
                                               └──▶ n8n webhook (optional, fire-and-forget)
```

### Agents

| # | Agent | Module | Responsibility | Deterministic guard rails |
|---|-------|--------|----------------|---------------------------|
| 1 | Orchestrator | `app/workflow/graph.py`, `runner.py` | Runs the graph, keeps the shared state, fans out issues in parallel, retries, creates tickets | Bounded retries, per-issue isolation, idempotent writes |
| 2 | Issue Extraction | `app/agents/extraction.py` | Splits a conversation into distinct issues and extracts summary, evidence, customer reference, impact, request and missing information | `workflow/validation.py` drops evidence, customer references and deadlines that don't appear in the source text, and de-duplicates issues |
| 3 | Classification | `app/agents/classification.py` | Assigns issue type, category, subcategory and confidence | Output must match `rules/taxonomy.yaml`. If not, it's retried, then falls back to *Other / needs review* |
| 4 | Prioritization | `app/agents/prioritization.py` | Proposes URGENT, MEDIUM or LOW with factors | `rules/priority_rules.py` applies mandatory rules afterwards, and the LLM can't override them |
| 5 | Resolution & Routing | `app/agents/resolution.py` | Writes the next action and ticket title, and flags whether approval is needed | The team and SLA come from `rules/routing.yaml`. Sensitive categories always require human approval, and nothing is executed |

The LLM never writes to the database. It returns JSON that is validated by Pydantic (`app/agents/schemas.py`). Only the fixed, parameterized functions in `app/workflow/persistence.py` write records.

### Data flow (LangGraph)

```
Conversation graph
  validate_input ─┬─(issues already extracted by an earlier run)─▶ load_issues
                  └─▶ extract ─(retry ≤ N)─┐
                        │ (exhausted) ──▶ fail
                        ▼
                  validate_issues ─▶ persist_issues ─▶ load_issues
                                                         │ Send() per pending issue (parallel)
                                                         ▼
                                                   process_issue × N ─▶ aggregate

Issue graph (one per issue)
  classify ─(retry)─▶ prioritize ─(retry)─▶ apply_rules ─┬─(URGENT)─▶ escalate ─┐
                                                         └──────────────────────┴▶ resolve ─(retry)─▶
  validate_record ─┬─(review reasons)─▶ flag_review ─┐
                   └─────────────────────────────────┴▶ persist_ticket
```

Conditional routing covers five cases:

- **Invalid output, timeouts, rate limits and malformed JSON** are retried up to `WORKFLOW_MAX_STAGE_ATTEMPTS` (default 2). After that the stage uses a safe fallback and the ticket is flagged for review.
- **Urgent issues** go through `escalate`.
- **Issues needing human review** go through `flag_review`. The ticket starts in `needs_review` and can't move to in-progress or resolved until a reviewer completes the review.
- **Extraction failure** sends the run to `fail`. The run is marked `failed` with a safe error message.
- **Successful tickets** go to `persist_ticket` and then `aggregate`.

The final graph state is written to `workflow_runs.state` (JSONB), so you can inspect each issue's outcome, attempts and errors.

### Reliability

- **Idempotency.** Extracted issues are saved as a checkpoint, keyed by a deterministic `issue_key`. A retry or resubmission reuses them instead of calling the LLM again. `tickets.issue_id` is `UNIQUE`, and inserts use `ON CONFLICT DO NOTHING`, so a retry never creates a second ticket or overwrites human edits. Identical text for the same customer maps to the same conversation through `content_hash`. Concurrent processing of one conversation is rejected with HTTP 409.
- **Failure isolation.** One issue failing is recorded on that issue (`processing_status='failed'`, `error`). The others are still ticketed, and the run is `partially_completed`. `POST /api/conversations/{id}/process` retries only the unticketed issues.
- **Safety.** API keys live only in the backend `.env`, and the frontend talks to `/api` through the Vite proxy. Unhandled errors return a generic 500 message. The workflow never performs refunds or account changes. It only recommends them and marks them for approval.

### Database (PostgreSQL)

The tables are `customers`, `conversations`, `issues`, `tickets`, `workflow_runs` and `ticket_events`. They use foreign keys, CHECK constraints on every enum-like column, unique keys (`customers.reference`, `conversations.content_hash`, `issues.issue_key`, `tickets.issue_id`) and indexes on the filter columns. The schema is managed by Alembic (`backend/alembic/versions/`).

---

## 3. Configurable rules (`backend/app/rules/`)

| File | What it controls |
|------|------------------|
| `taxonomy.yaml` | The only categories and subcategories the classifier may return. Extend it deliberately. |
| `routing.yaml` | Team for each category, SLA hours per priority, and which categories or subcategories always require human approval |
| `priority_rules.yaml` | URGENT escalations (account security, multi-user outage, duplicate charge, amount ≥ 1000, deadline ≤ 48h), MEDIUM floors, and guards |

The guards work like this:

- URGENT requires a rule to fire. An unsupported LLM URGENT is lowered to MEDIUM and flagged.
- A plain inquiry with no rule support is capped at LOW, so tone can't inflate priority.
- Low confidence leads to review.

Sentiment words are deliberately not used as inputs to any rule.

---

## 4. LLM configuration

Set these in `backend/.env`:

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENAI_API_KEY` | – | Required for live processing. Without it, runs fail safely with a clear message. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Must support `response_format={"type":"json_object"}` |
| `LLM_TEMPERATURE` | `0` | Leave blank for models that reject a temperature parameter |
| `LLM_TIMEOUT_SECONDS` | `30` | Per call |
| `WORKFLOW_MAX_STAGE_ATTEMPTS` | `2` | Bounded retries per stage |
| `OPENAI_BASE_URL` | blank | Only for an OpenAI-compatible endpoint |

---

## 5. Tests and evaluation

```bash
cd backend
pytest -q                     # uses the crm_test database and a mocked LLM; no API calls
```

The suite has 55 tests and maps to the acceptance criteria as follows:

| # | Criterion | Test(s) |
|---|-----------|---------|
| 1 | Structured issue records | `test_workflow.py::test_normal_conversation_produces_structured_issue_records`, `test_agents.py` |
| 2 | Three issues give three tickets | `test_workflow.py::test_three_issues_produce_three_distinct_tickets` |
| 3 | Customers not mixed | `test_workflow.py::test_multiple_customers_are_not_mixed`, `test_repeat_customer_links_to_same_customer_record` |
| 4 | Taxonomy enforced | `test_rules.py::test_classification_outside_taxonomy_is_rejected`, `test_agents.py::test_classification_valid_and_taxonomy_enforced` |
| 5 | Priority rules | `test_rules.py` (security, outage, duplicate charge, amount, deadline, floors) |
| 6 | LLM can't override escalation | `test_rules.py::test_llm_cannot_lower_mandatory_escalation`, `test_workflow.py::test_llm_cannot_override_escalation_end_to_end` |
| 7 | Sentiment alone doesn't set priority | `test_rules.py::test_angry_routine_question_stays_low`, `test_workflow.py::test_angry_routine_question_is_low_end_to_end` |
| 8 | Missing info leads to review, not invention | `test_validation.py`, `test_workflow.py::test_missing_information_routes_to_review` |
| 9 | Invalid LLM output handled | `test_workflow.py::test_invalid_classification_output_falls_back_safely`, `…malformed_json…`, `…timeout…`, `…extraction_failure…` |
| 10 | No duplicate tickets | `test_workflow.py::test_duplicate_processing_creates_no_duplicate_tickets` |
| 11 | Status changes persisted and recorded | `test_api.py::test_status_and_assignment_changes_are_persisted_and_recorded`, `test_review_gate_and_review_completion` |
| 12 | Dashboard data from the API | `test_api.py::test_dashboard_data_comes_from_backend` |
| 13 | Sample dataset loads and processes | `test_api.py::test_sample_dataset_loads_and_processes` (all 15 samples against `expected_outcomes.json`) |
| 14 | Works without n8n | `test_workflow.py::test_core_workflow_works_when_n8n_unreachable` |
| 15 | One failure doesn't erase other results | `test_workflow.py::test_one_issue_failure_keeps_other_results_and_retry_completes` |

Where things live:

- Mocked LLM responses: `tests/fixtures/mock_llm/responses.py`, for tests only. Some deliberately disagree with the rules, to prove the rules win.
- Expected outcomes: `tests/fixtures/expected_outcomes.json`. They're never read at runtime.

**Live-model evaluation** is separate from the unit tests and costs API calls. It measures the real model against the expected outcomes:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/crm_eval alembic upgrade head
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/crm_eval python -m evals.run_eval
```

No accuracy numbers are claimed here. Run the eval to measure them.

**Offline end-to-end without an API key (dev only).** `tests/e2e/stub_openai_server.py` serves the mocked responses over the OpenAI wire format, which exercises the real client, API and UI:

```bash
cd backend
uvicorn tests.e2e.stub_openai_server:app --port 8099 &
OPENAI_API_KEY=stub OPENAI_BASE_URL=http://localhost:8099/v1 uvicorn app.main:app --port 8000
```

The stub only knows the 15 sample conversations, so don't treat it as real AI output.

---

## 6. Optional n8n integration

The core app works without n8n. To enable it:

1. Run n8n locally, for example `docker run -it --rm -p 5678:5678 -e CRM_NOTIFY_URL=https://webhook.site/<your-id> -e CRM_API_URL=http://host.docker.internal:8000 n8nio/n8n`.
2. In n8n, go to **Workflows → Import from file** and choose `n8n/urgent_ticket_workflow.json`.
3. Set `CRM_NOTIFY_URL` to any test endpoint you control, such as webhook.site or a local mock. No Slack or email credentials are needed or included.
4. Activate the workflow, then copy the **Production URL** of the *CRM ticket created* webhook into `backend/.env`:
   `N8N_WEBHOOK_URL=http://localhost:5678/webhook/crm-ticket-created`
5. Restart the API.

The workflow has two branches:

- **Ticket created → Only URGENT → Notify destination.** The backend POSTs `{event:"tickets.created", tickets:[…]}` after each run.
- **Every hour → GET `/api/tickets?overdue=true` → Notify overdue.** This is the follow-up for open tickets past their SLA.

If n8n is down, the backend logs a warning and carries on.

---

## 7. API reference

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Database and LLM configuration check |
| GET | `/api/meta` | Categories, teams, statuses |
| GET | `/api/samples` | Sample conversations |
| POST | `/api/samples/seed` | Seed sample customers and conversations (idempotent) |
| POST | `/api/conversations` | Submit and process a conversation |
| POST | `/api/conversations/{id}/process` | Retry or reprocess (idempotent) |
| GET | `/api/conversations[/{id}]` | Conversations with issues, tickets and the latest run |
| GET | `/api/tickets?priority=&category=&status=&team=&q=&overdue=` | List, filter and search |
| GET | `/api/tickets/{id}` | Full ticket detail with event history |
| PATCH | `/api/tickets/{id}` | `{status?, team?, note?, actor}`, with validated status transitions |
| POST | `/api/tickets/{id}/review` | Complete a human review |
| GET | `/api/stats` | Dashboard counts and tickets grouped by priority |

## 8. Project layout

```
backend/
  app/            main.py, config.py, db.py, models.py, schemas.py, seed.py, notify.py
    agents/       llm.py, base.py, schemas.py, extraction.py, classification.py, prioritization.py, resolution.py
    workflow/     state.py, graph.py, runner.py, validation.py, persistence.py
    rules/        taxonomy.yaml, routing.yaml, priority_rules.yaml, config.py, priority_rules.py
    api/          conversations.py, tickets.py, meta.py, serializers.py
  alembic/        migrations
  data/           samples.json (15 conversations)
  tests/          unit, workflow, API tests, fixtures, e2e stub
  evals/          run_eval.py (live model)
frontend/         Vite + React dashboard
n8n/              urgent_ticket_workflow.json
docker-compose.yml
```
