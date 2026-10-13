# Multi-Agent AI-Powered CRM Workflow

This app turns raw customer conversation text into **separate, prioritized, actionable CRM tickets**.

Paste a conversation, or pick one of 15 built-in samples. A team of AI agents then:

1. finds every distinct issue in the conversation (one conversation can produce several tickets),
2. classifies each issue against a fixed taxonomy,
3. sets a priority of **URGENT**, **MEDIUM** or **LOW**, enforced by business rules the AI cannot override,
4. recommends the next action, the responsible team and an SLA,
5. saves everything to PostgreSQL and shows it on a React dashboard, where you can review, reassign and update tickets.

| Layer | Technology |
|-------|------------|
| Frontend | React 18 + Vite |
| Backend API | Python, FastAPI, Pydantic |
| Agent orchestration | LangGraph (1 orchestrator + 4 specialist agents) |
| LLM | OpenAI (`gpt-4o-mini` by default) |
| Database | PostgreSQL 16 (Docker), SQLAlchemy, Alembic |
| Tests | pytest (55 tests, mocked LLM, no API cost) |

Everything runs locally. Nothing is deployed and there are no external services besides the OpenAI API.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [One-time setup](#2-one-time-setup)
3. [Start the app](#3-start-the-app)
4. [5-minute demo](#4-5-minute-demo)
5. [Troubleshooting](#5-troubleshooting)
6. [Run the tests](#6-run-the-tests)
7. [How it works](#7-how-it-works)
8. [Reference](#8-reference): configuration, rules, database, API, project layout

---

## 1. Prerequisites

| Tool | Version | Check with |
|------|---------|------------|
| Python | 3.11 or newer (developed on 3.13) | `python --version` |
| Node.js | 18 or newer | `node --version` |
| Docker Desktop | any recent version, **running** | `docker version` (must show a **Server** section) |
| OpenAI API key | needs a little credit; a full demo costs a few cents | https://platform.openai.com/api-keys |

> No Docker? Install PostgreSQL 14+ yourself, create the databases `crm`, `crm_test` and `crm_eval`, and set `DATABASE_URL` in `backend/.env`.

---

## 2. One-time setup

Run these **in order**. Every command shows which folder it must run from.

### Windows (PowerShell)

```powershell
# 1) Get the code
git clone <REPO_URL> crm-workflow
cd crm-workflow

# 2) Start PostgreSQL (Docker Desktop must be open and running)
docker compose up -d postgres

# 3) Backend: create and activate a virtual environment
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
#    If you see "running scripts is disabled":
#    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (then run the line above again)

# 4) Install Python packages
pip install -r requirements.txt

# 5) Create your settings file and add your OpenAI key
copy .env.example .env
notepad .env          # set:  OPENAI_API_KEY=sk-...   then save and close

# 6) Create the database tables
alembic upgrade head

# 7) Frontend packages
cd ..\frontend
npm install
```

### macOS / Linux

```bash
# 1) Get the code
git clone <REPO_URL> crm-workflow
cd crm-workflow

# 2) Start PostgreSQL (Docker must be running)
docker compose up -d postgres

# 3) Backend: virtual environment
cd backend
python3 -m venv .venv
source .venv/bin/activate

# 4) Install Python packages
pip install -r requirements.txt

# 5) Settings file: add your OpenAI key
cp .env.example .env
#    edit .env and set:  OPENAI_API_KEY=sk-...

# 6) Create the database tables
alembic upgrade head

# 7) Frontend packages
cd ../frontend
npm install
```

**Check before moving on:**
- `docker compose ps` shows `postgres` as running.
- `alembic upgrade head` finishes without errors. The first time, it prints `Running upgrade -> ..., initial schema`.
- `backend/.env` contains your key. Never commit this file; it is already in `.gitignore`.

---

## 3. Start the app

You need **two terminals**, and both stay open while you use the app.

**Terminal 1: backend API** (from the `backend` folder)

```powershell
# Windows
cd crm-workflow\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000
```
```bash
# macOS / Linux
cd crm-workflow/backend
source .venv/bin/activate
uvicorn app.main:app --port 8000
```
Wait for `Application startup complete`.

**Terminal 2: dashboard** (from the `frontend` folder)

```bash
cd crm-workflow/frontend
npm run dev
```
Wait for `Local: http://localhost:5173/`.

**Open http://localhost:5173 in your browser.**

| Address | What it is |
|---------|------------|
| http://localhost:5173 | **The dashboard. Use this one.** |
| http://localhost:8000/docs | Interactive API documentation (Swagger) |
| http://localhost:8000 | The API root. A `404 Not Found` here is normal. |

The top-right corner of the dashboard should show a green **LLM: gpt-4o-mini** badge. A red **LLM not configured** badge means the key in `backend/.env` was not found (see [Troubleshooting](#5-troubleshooting)).

---

## 4. 5-minute demo

1. Click **Load samples into database**. This stores the 15 sample customers and conversations (input only, no results).
2. In **Sample conversation**, choose a sample and click **Process conversation**. The button shows *Processing… (agents running)* until the agents finish. This usually takes a few seconds.

Try these samples. Results come from the live model, so wording can vary slightly between runs.

| Sample | What it shows | What to expect |
|--------|---------------|----------------|
| **S15** · Three distinct issues | One conversation becomes several tickets | **3 tickets**: wrong item, missing discount, warranty question |
| **S01** · Double deduction + missing confirmation | Mandatory financial-harm rule | Duplicate charge is **URGENT** and needs human approval |
| **S07** · Suspicious account activity | Security rule | **URGENT**, routed to Account Security |
| **S10** · Angry customer, routine question | Sentiment doesn't drive priority | **LOW**, despite the all-caps anger |
| **S11** · Late delivery, no details | No invented facts | Customer *unknown*, status **Needs review**, next action asks for the order number |
| **S13** · Shared inbox, two customers | Issues stay with the right customer | 2 tickets, one each for CUST-2101 and CUST-2102 |

3. **Open a ticket.** Click any row to see:
   - the evidence quoted from the conversation,
   - the classification and confidence,
   - the priority decision: what the AI proposed, the final priority, and which rules applied,
   - the recommended action, the team and SLA, and the event history.
4. **Manage it.** Change the status or team, add a note, and click **Save changes**. For a *Needs review* ticket, click **Mark review complete** first. Every change appears in the event history.
5. **Check duplicate protection.** Process **S15** again. The result says *"3 already existed (no duplicates)"*.
6. **Use the ticket list.** Filter by priority, category or status, or search by ticket ID (e.g. `TKT-00001`), customer reference or summary text.

**Optional: confirm the data is in PostgreSQL**

```bash
docker compose exec postgres psql -U postgres -d crm -c "select id, priority, status, team, category from tickets order by id;"
docker compose exec postgres psql -U postgres -d crm -c "select ticket_id, event_type, from_value, to_value, actor from ticket_events order by id;"
```

---

## 5. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `failed to connect to the docker API` / `error during connect` | Docker Desktop isn't running | Open Docker Desktop, wait for **Engine running**, then repeat `docker compose up -d postgres` |
| `ModuleNotFoundError: No module named 'app'` | `uvicorn` was started outside the `backend` folder | `cd backend`, activate `.venv`, then run `uvicorn` again |
| `ModuleNotFoundError: No module named 'fastapi'` (or another package) | The virtual environment isn't active | Windows: `.\.venv\Scripts\Activate.ps1`; macOS/Linux: `source .venv/bin/activate`. The prompt should start with `(.venv)` |
| `running scripts is disabled on this system` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| Dashboard shows **LLM not configured** | No key in `backend/.env` | Put `OPENAI_API_KEY=sk-...` in `backend/.env` (not `.env.example`), then restart the backend |
| Run fails with *authentication failed* | Wrong key, or stray spaces or quotes | Paste the key again without quotes or spaces, then restart the backend |
| Run fails with *rate limit reached* | Often means the OpenAI account has no credit | Add credit at platform.openai.com → Billing |
| Dashboard says *Cannot reach the backend* | Terminal 1 isn't running | Start the backend (step 3) |
| `connection refused` on port 5432 | PostgreSQL isn't running | `docker compose up -d postgres` from the repo root |
| `database "crm_test"` / `"crm_eval" does not exist` | These databases are created only when the Postgres volume is first initialised | `docker compose exec postgres psql -U postgres -c "CREATE DATABASE crm_test;"` (same for `crm_eval`) |
| `npm` is not recognized | Node.js isn't installed | Install Node.js LTS from https://nodejs.org, then reopen the terminal |

A failed run never crashes the app. The dashboard shows the run status and a safe error message, and **Retry** reprocesses only what failed.

---

## 6. Run the tests

The automated tests use a **separate `crm_test` database** and a **mocked LLM**. They make no OpenAI calls and cost nothing.

```bash
cd backend          # with .venv active
pytest -q           # expected: 55 passed
```

| # | Acceptance criterion | Test(s) |
|---|----------------------|---------|
| 1 | Structured issue records | `test_workflow.py::test_normal_conversation_produces_structured_issue_records`, `test_agents.py` |
| 2 | Three issues → three tickets | `test_workflow.py::test_three_issues_produce_three_distinct_tickets` |
| 3 | Customers never mixed | `test_workflow.py::test_multiple_customers_are_not_mixed`, `test_repeat_customer_links_to_same_customer_record` |
| 4 | Taxonomy enforced | `test_rules.py::test_classification_outside_taxonomy_is_rejected`, `test_agents.py::test_classification_valid_and_taxonomy_enforced` |
| 5 | Urgent / medium / low rules | `test_rules.py` (security, outage, duplicate charge, amount, deadline, floors) |
| 6 | LLM can't override escalation | `test_rules.py::test_llm_cannot_lower_mandatory_escalation`, `test_workflow.py::test_llm_cannot_override_escalation_end_to_end` |
| 7 | Sentiment alone doesn't set priority | `test_rules.py::test_angry_routine_question_stays_low`, `test_workflow.py::test_angry_routine_question_is_low_end_to_end` |
| 8 | Missing info → review, never invented | `test_validation.py`, `test_workflow.py::test_missing_information_routes_to_review` |
| 9 | Invalid LLM output handled safely | `test_workflow.py` (invalid classification, malformed JSON, timeout, extraction failure), `test_agents.py` (shape drift) |
| 10 | No duplicate tickets on retry | `test_workflow.py::test_duplicate_processing_creates_no_duplicate_tickets` |
| 11 | Status changes persisted and recorded | `test_api.py::test_status_and_assignment_changes_are_persisted_and_recorded`, `test_review_gate_and_review_completion` |
| 12 | Dashboard data comes from the API | `test_api.py::test_dashboard_data_comes_from_backend` |
| 13 | Sample dataset loads and processes | `test_api.py::test_sample_dataset_loads_and_processes` (all 15 samples vs. `expected_outcomes.json`) |
| 14 | Core workflow needs no n8n | n8n isn't used; every workflow test runs with no external services |
| 15 | One failure doesn't erase other results | `test_workflow.py::test_one_issue_failure_keeps_other_results_and_retry_completes` |

### Live-model evaluation (optional, uses the OpenAI API)

This runs the real model over all 15 samples and compares the results with `tests/fixtures/expected_outcomes.json`. It uses its own `crm_eval` database, so the dashboard data is untouched.

```powershell
# Windows (from backend, .venv active)
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/crm_eval"
alembic upgrade head
python -m evals.run_eval
Remove-Item Env:DATABASE_URL      # switch this terminal back to the main database
```
```bash
# macOS / Linux
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/crm_eval
alembic upgrade head && python -m evals.run_eval
unset DATABASE_URL
```

One measured run with `gpt-4o-mini`:

| Measure | Result |
|---------|--------|
| Issue count exact | 15/15 samples |
| Category accuracy | 18/21 (86%) |
| Priority accuracy | 17/21 (81%) |
| Failed runs | 0 |

LLM output varies, so expect small differences between runs.

---

## 7. How it works

```
React dashboard ──/api──▶ FastAPI ──▶ LangGraph orchestrator ──▶ OpenAI (4 agents)
                              │                │
                              └──── SQLAlchemy ┴──▶ PostgreSQL
```

### The agents

| # | Agent | Module | Responsibility | Deterministic guard rails |
|---|-------|--------|----------------|---------------------------|
| 1 | Orchestrator | `app/workflow/graph.py`, `runner.py` | Runs the graph, keeps the shared state, processes issues in parallel, retries, creates tickets | Bounded retries, per-issue isolation, idempotent writes |
| 2 | Issue Extraction | `app/agents/extraction.py` | Splits a conversation into distinct issues: summary, evidence, customer reference, impact, request, missing information | `workflow/validation.py` drops evidence, customer references and deadlines that don't appear in the source text, and removes duplicates |
| 3 | Classification | `app/agents/classification.py` | Assigns issue type, category, subcategory and confidence | Must match `rules/taxonomy.yaml`; otherwise it is retried, then falls back to *Other / needs review* |
| 4 | Prioritization | `app/agents/prioritization.py` | Proposes URGENT, MEDIUM or LOW with reasons | `rules/priority_rules.py` applies mandatory rules afterwards, and the LLM cannot override them |
| 5 | Resolution & Routing | `app/agents/resolution.py` | Writes the next action and ticket title, and flags whether approval is needed | Team and SLA come from `rules/routing.yaml`; sensitive categories always require human approval; nothing is ever executed |

The LLM never touches the database. Every agent returns JSON that is validated by Pydantic (`app/agents/schemas.py`), and only the fixed functions in `app/workflow/persistence.py` write records.

### Workflow (LangGraph)

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

The graph routes five cases:

- **Invalid output, timeouts, rate limits and malformed JSON** are retried up to `WORKFLOW_MAX_STAGE_ATTEMPTS` (default 2). After that the stage uses a safe fallback and the ticket is flagged for review.
- **Urgent issues** pass through `escalate`.
- **Issues needing human review** start in `needs_review`. They can't move to in-progress or resolved until a reviewer completes the review.
- **Extraction failure** marks the run `failed` with a safe error message.
- **Successful issues** become tickets, and the final graph state is stored in `workflow_runs.state` for inspection.

### Reliability

- **No duplicates.** Extracted issues are checkpointed with a deterministic key, so a retry reuses them instead of calling the LLM again. `tickets.issue_id` is unique and inserts use `ON CONFLICT DO NOTHING`, so a retry never creates a second ticket or overwrites human edits.
- **Failure isolation.** If one issue fails, the others are still ticketed. The run is marked `partially_completed`, and a retry processes only the failed issue.
- **Safety.** The API key lives only in `backend/.env` and never reaches the browser. Errors returned to the client are generic. The system only recommends refunds or account changes; it never performs them.

---

## 8. Reference

### Configuration (`backend/.env`)

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENAI_API_KEY` | – | Required. Without it, runs fail safely with a clear message. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Must support JSON mode |
| `LLM_TEMPERATURE` | `0` | Leave blank for models that reject a temperature parameter |
| `LLM_TIMEOUT_SECONDS` | `30` | Per LLM call |
| `WORKFLOW_MAX_STAGE_ATTEMPTS` | `2` | Bounded retries per stage |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/crm` | Matches `docker-compose.yml` |

### Business rules (`backend/app/rules/`): edit YAML, not code

| File | Controls |
|------|----------|
| `taxonomy.yaml` | The only categories and subcategories the classifier may return |
| `routing.yaml` | Team per category, SLA hours per priority, categories that always need human approval |
| `priority_rules.yaml` | URGENT triggers (account security, multi-user outage, duplicate charge, amount ≥ 1000, deadline ≤ 48h), MEDIUM floors, and guards |

The guards work like this:
- URGENT requires a rule to fire. An unsupported AI "URGENT" is lowered to MEDIUM and flagged for review.
- A plain question with no rule support is capped at LOW, so tone can't inflate priority.
- Sentiment words are never used as rule inputs.

### Database

The tables are `customers`, `conversations`, `issues`, `tickets`, `workflow_runs` and `ticket_events`. They use foreign keys, CHECK constraints on every status or priority column, unique keys (`customers.reference`, `conversations.content_hash`, `issues.issue_key`, `tickets.issue_id`) and indexes on filter columns. Migrations are in `backend/alembic/versions/`.

### API

Full interactive docs are at http://localhost:8000/docs while the backend is running.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Database and LLM configuration check |
| GET | `/api/meta` | Categories, teams, statuses |
| GET | `/api/samples` | Sample conversations |
| POST | `/api/samples/seed` | Store sample customers and conversations (idempotent) |
| POST | `/api/conversations` | Submit and process a conversation |
| POST | `/api/conversations/{id}/process` | Retry or reprocess (idempotent) |
| GET | `/api/conversations[/{id}]` | Conversations with issues, tickets and the latest run |
| GET | `/api/tickets?priority=&category=&status=&team=&q=` | List, filter and search tickets |
| GET | `/api/tickets/{id}` | Full ticket detail with event history |
| PATCH | `/api/tickets/{id}` | Change `status` and/or `team` (transitions are validated) |
| POST | `/api/tickets/{id}/review` | Complete a human review |
| GET | `/api/stats` | Dashboard counts and tickets grouped by priority |

### Project layout

```
crm-workflow/
├── docker-compose.yml        PostgreSQL 16
├── scripts/                  creates the crm_test and crm_eval databases on first start
├── backend/
│   ├── app/
│   │   ├── main.py           FastAPI app
│   │   ├── config.py         settings from .env
│   │   ├── models.py         database tables
│   │   ├── schemas.py        API request/response shapes
│   │   ├── api/              HTTP endpoints
│   │   ├── agents/           the 4 LLM agents + output schemas
│   │   ├── workflow/         LangGraph orchestrator, validation, persistence
│   │   └── rules/            taxonomy, routing and priority rules (YAML)
│   ├── alembic/              database migrations
│   ├── data/samples.json     15 sample conversations
│   ├── tests/                55 automated tests + fixtures (mocked LLM)
│   └── evals/run_eval.py     live-model evaluation
└── frontend/
    └── src/                  React dashboard (Overview, Process, Tickets, Ticket detail)
```
