# Adaptive Syllabus & Lecture Planning

A professor-facing planning system that turns a syllabus document into a
validated, dependency-aware lecture timetable — and keeps that timetable
current when lectures are completed, missed, cancelled, or the semester
configuration changes.

The scheduler is deliberately **deterministic**. An LLM may help read a
syllabus, but it never decides the timetable. Every generated schedule is a
reviewable proposal: nothing is published until the professor approves it.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
  - [Docker Compose (recommended)](#docker-compose-recommended)
  - [Local development](#local-development)
  - [Demo account](#demo-account)
- [Configuration](#configuration)
  - [Compose environment](#compose-environment)
  - [Backend settings](#backend-settings)
  - [LLM providers](#llm-providers)
- [Using the application](#using-the-application)
- [Scheduling engine](#scheduling-engine)
  - [Hard constraints](#hard-constraints)
  - [Recovery strategies](#recovery-strategies)
  - [Revision lifecycle](#revision-lifecycle)
  - [Infeasible schedules](#infeasible-schedules)
- [Syllabus extraction](#syllabus-extraction)
- [API reference](#api-reference)
  - [Conventions](#conventions)
  - [Endpoints](#endpoints)
  - [Examples](#examples)
- [Data model](#data-model)
- [Security](#security)
- [Testing and quality checks](#testing-and-quality-checks)
- [Database migrations](#database-migrations)
- [Deployment notes](#deployment-notes)
- [Troubleshooting](#troubleshooting)
- [Design decisions](#design-decisions)
- [Current limitations and possible next steps](#current-limitations-and-possible-next-steps)
- [Contributing](#contributing)
- [License](#license)

---

## Why this exists

Semester plans usually live in a syllabus document and a spreadsheet. When a
class is cancelled, a holiday is added, or a topic takes longer than expected,
the professor has to manually rebuild the remaining schedule while remembering:

- which topics are prerequisites for which;
- how many teaching minutes each topic still needs;
- which weeks and rooms are actually available;
- which lectures already happened and must not be rescheduled;
- what changed since the last version students saw.

This project automates that bookkeeping while keeping the professor in control.
The result is a plan that is explainable, reproducible, and safe to change.

## Features

- **Syllabus ingestion** — upload PDF, DOCX, or TXT files, or paste syllabus text
  directly into the UI; extract text from PDF, DOCX, and TXT files.
- **Structured extraction** — optional LLM interpretation with a deterministic
  rule-based fallback, confidence scoring, review flags, prerequisite
  resolution, and duration normalization.
- **Prompt-based AI editing** — refine and reorganize extracted units and topics
  using natural language prompts (e.g. splitting units, adjusting durations,
  adding topics) before confirming.
- **Human confirmation** — extracted units and topics are only a preview until
  the professor edits and confirms them.
- **Deterministic scheduling** — prerequisite-aware, priority-ordered planning
  into real calendar slots, with support for split topics and several short
  topics sharing a lecture.
- **Calendar constraints** — weekly availability windows, holidays, exam days,
  events, unavailable days, time zones, and fixed/occupied sessions.
- **Missed-lecture recovery** — mark a lecture completed (including partial
  delivery), missed, or cancelled; the remaining plan is recalculated without
  duplicating work already taught.
- **Reviewable revisions** — every generation or recalculation creates an
  immutable revision with a before/after diff and a change ledger.
- **Approval workflow** — approve, reject, publish, or restore revisions.
  Approving a new revision supersedes the previous one without deleting it.
- **Progress analytics** — topic and unit coverage, at-risk topics, projected
  completion, capacity utilization, and day/week/semester calendars.
- **In-app notifications** — pending approvals, approved schedules, and capacity
  warnings.
- **Audit trail** — meaningful mutations (auth, course changes, uploads,
  schedule decisions, lecture status changes) are recorded.
- **No frontend build step** — the UI is plain HTML, CSS, and ES modules served
  by the same FastAPI process.
- **Tested end to end** — 279 unit and API integration tests run against a
  throwaway SQLite database with no network or API keys required.

## How it works

The intended workflow is a loop, not a one-time import:

```text
Create course
    │
    ▼
Upload syllabus ──► extract text ──► interpret structure ──► preview
                                                               │
                                              professor edits / confirms
                                                               │
                                                               ▼
                                                    confirmed syllabus
                                                               │
                                                               ▼
                                              generate deterministic schedule
                                                               │
                                                               ▼
                                              pending revision + diff + warnings
                                                               │
                                            professor reviews ─┤
                                                               ├─► approve / publish
                                                               └─► reject
                                                               │
                                                               ▼
                                          teach ──► mark completed / missed / cancelled
                                                               │
                                                               ▼
                                             recalculate remaining plan
                                                               │
                                                               └──► back to review
```

Two rules shape the whole design:

1. **Extraction is not scheduling.** Document text is untrusted input and may be
   interpreted by an LLM. Scheduling uses only confirmed, persisted topics.
2. **A proposal is not a publication.** A recalculation creates a new revision in
   `pending_approval`. It becomes the current timetable only after approval
   (unless the course explicitly enables automatic publishing).

## Architecture

```mermaid
flowchart TB
    subgraph Browser
        UI["HTML / CSS / ES modules<br/>served at /app"]
    end

    subgraph Backend["FastAPI application"]
        API["/api/v1 routes"]
        Auth["Auth + course authorization"]
        Services["Application services"]
        Planner["Deterministic scheduling domain"]
        Extract["Extraction pipeline"]
        Audit["Audit log + notifications"]
    end

    subgraph Data
        DB[("PostgreSQL<br/>or SQLite in dev")]
        Files[("Uploaded syllabus files")]
    end

    Providers["Optional LLM<br/>OpenRouter, Gemini, or Ollama"]

    UI --> API
    API --> Auth
    API --> Services
    Services --> Planner
    Services --> Extract
    Services --> Audit
    Services --> DB
    Services --> Files
    Extract -.-> Providers
```

### Layering

| Layer | Responsibility |
| --- | --- |
| `app/api` | HTTP routes, dependencies, pagination, auth guards, exception handlers |
| `app/services` | Application orchestration and persistence |
| `app/domain/scheduling` | Pure scheduling logic: slots, planner, validation, diff |
| `app/ai` and `app/documents` | Untrusted document extraction and interpretation |
| `app/models` / `app/schemas` | SQLAlchemy persistence models and Pydantic contracts |
| `frontend` | Framework-free professor UI |

The scheduling domain has no FastAPI, SQLAlchemy, or LLM imports. It can be
tested as pure logic, and a regression in planner behavior is caught by a
separate validator before a revision is stored.

## Technology stack

| Area | Choice |
| --- | --- |
| Runtime | Python 3.12+ |
| API | FastAPI, Uvicorn |
| Validation | Pydantic v2, pydantic-settings |
| Persistence | SQLAlchemy 2.x, Alembic, PostgreSQL 16 (SQLite for local dev) |
| Auth | JWT (PyJWT), Argon2 password hashing via `pwdlib` |
| Documents | PyMuPDF (PDF), python-docx (DOCX) |
| LLM providers | OpenRouter HTTP API, Ollama HTTP API, or none |
| Frontend | Vanilla HTML, CSS, JavaScript modules; no bundler |
| Packaging | `uv`, Docker image with a pinned `uv` stage |
| Tests | pytest, pytest-cov, FastAPI TestClient |
| Quality | Ruff, Mypy |

## Repository layout

```text
.
├── backend/
│   ├── alembic/                     # migration environment and versions
│   ├── app/
│   │   ├── ai/                      # rule-based + LLM syllabus interpreters
│   │   ├── api/                     # FastAPI routes and shared dependencies
│   │   ├── core/                    # settings, security, errors, rate limiting, logging
│   │   ├── db/                      # engine, session factory, declarative base
│   │   ├── documents/               # PDF/DOCX/TXT text extraction
│   │   ├── domain/scheduling/       # calendar expansion, planner, validation, diff
│   │   ├── models/                  # SQLAlchemy entities
│   │   ├── schemas/                 # Pydantic request/response models
│   │   ├── services/                # auth, courses, syllabus, scheduling, progress, audit
│   │   └── main.py                  # app factory, lifespan, static frontend mount
│   ├── tests/
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/
│   ├── js/
│   │   ├── views/                   # auth, courses, dashboard, syllabus, schedule, ...
│   │   ├── api.js                   # API client and session storage
│   │   ├── app.js                   # hash router and app shell
│   │   ├── dom.js                   # DOM helpers, modals, toasts
│   │   ├── format.js
│   │   └── strategies.js
│   ├── index.html
│   └── styles.css
├── docker-compose.yml               # PostgreSQL + API + static UI
├── .env.example                     # Compose configuration template
└── README.md
```

## Quick start

### Docker Compose (recommended)

Requirements: Docker with Compose v2.

```bash
# 1. Create the Compose environment file.
cp .env.example .env

# 2. Generate a strong JWT secret and put it in .env.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Edit .env and set:
#    JWT_SECRET=<the generated value>
#    POSTGRES_PASSWORD=<a strong database password>

# 4. Build and start PostgreSQL + the API/UI.
docker compose up --build

# 5. Open the application.
#    UI:       http://localhost:8000/app/
#    API docs: http://localhost:8000/docs
#    Health:   http://localhost:8000/health
```

The `api` container waits for PostgreSQL to become healthy, runs
`alembic upgrade head`, and then starts Uvicorn. Uploaded files and database data
are stored in named volumes (`api_storage`, `db_data`). The frontend directory is
bind-mounted read-only, so UI edits do not require an image rebuild.

Stop the stack:

```bash
docker compose down          # keeps data
docker compose down -v       # ⚠ also deletes the database and uploaded files
```

### Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/). PostgreSQL is
optional; the default local configuration uses SQLite.

```bash
cd backend

# Install runtime + development dependencies.
uv sync --extra dev

# Create local settings from the canonical template.
cp ../.env.example .env

# Apply migrations once. In development the app also creates missing tables
# at startup, but running Alembic keeps the schema at the expected head.
uv run alembic upgrade head

# Start the API (serves the UI at /app).
uv run uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000/app/>.

For PostgreSQL locally, set `DATABASE_URL` in `backend/.env`, for example:

```dotenv
DATABASE_URL=postgresql+psycopg://syllabus:secret@localhost:5432/syllabus
```

Then run `uv run alembic upgrade head` before starting the API.

### Demo account

In development, the app seeds one professor and one example course
(`CS201 — Data Structures and Algorithms`) on first start, if the database has no
users.

| Field | Value |
| --- | --- |
| Email | `professor@example.edu` |
| Password | `professor123` |
| Course | `CS201` — Data Structures and Algorithms |

These credentials are defined by `DEMO_PROFESSOR_*` settings and are **never**
seeded when `APP_ENV=production`. Change or disable the seed before exposing a
development instance to anyone else.

## Configuration

### Compose environment

`docker-compose.yml` reads the root `.env` file.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `JWT_SECRET` | **Yes** | — | Signing key for access tokens. Compose refuses to start without it. |
| `POSTGRES_USER` | No | `syllabus` | Database user. |
| `POSTGRES_PASSWORD` | Recommended | `syllabus` | Database password; change it for anything non-local. |
| `POSTGRES_DB` | No | `syllabus` | Database name. |
| `APP_ENV` | No | `production` | Environment name. |
| `API_PORT` | No | `8000` | Host port for the API and UI. |
| `LLM_PROVIDER` | No | `none` | `none`, `openrouter`, `gemini`, or `ollama`. |
| `OPENROUTER_API_KEY` | If OpenRouter | empty | OpenRouter key. |
| `OPENROUTER_MODEL` | No | `google/gemini-2.0-flash-001` | OpenRouter model. |
| `GEMINI_API_KEY` | If Gemini | empty | Google Gemini key. |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model. |
| `OLLAMA_BASE_URL` | If Ollama | `http://host.docker.internal:11434` | Ollama endpoint reachable from the container. |
| `OLLAMA_MODEL` | No | `llama3.1:8b` | Ollama model. |
| `CORS_ORIGINS` | No | empty | Comma-separated origins when the UI is served elsewhere. |

> Compose reads the repository-root `.env` for interpolation and forwards the
> provider variables above. In production the Settings UI cannot write keys; it
> lets each teacher enable AI and choose the provider/model after the operator
> provisions the server key.

> The Compose file forwards a focused subset of settings. For finer control
> (`OPENROUTER_MODEL`, `OLLAMA_MODEL`, rate limits, duration limits, etc.), add
> entries under `services.api.environment` or run the backend locally with a
> complete `backend/.env`.

### Backend settings

All settings are read from environment variables and from `backend/.env`.
Defaults are suitable for local development. Full template:
[`backend/.env.example`](backend/.env.example).

#### Application

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `Adaptive Syllabus API` | OpenAPI title. |
| `APP_ENV` | `development` | `development`, `test`, or `production`. |
| `DEBUG` | `false` | Debug logging. Never enable in production. |
| `API_V1_PREFIX` | `/api/v1` | Mount prefix for the JSON API. |

#### Database

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///data/app.db` | SQLAlchemy URL. Use `postgresql+psycopg://...` in production. |
| `DB_ECHO` | `false` | Log every SQL statement. |

#### Authentication

| Variable | Default | Description |
| --- | --- | --- |
| `JWT_SECRET` | development placeholder | **Must be replaced** with a long random value. |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Access-token lifetime (8 hours). |

#### HTTP and uploads

| Variable | Default | Description |
| --- | --- | --- |
| `CORS_ORIGINS` | local Vite origins | Comma-separated list or JSON array; empty means no cross-origin access. |
| `TRUST_FORWARDED_FOR` | `false` | Trust `X-Forwarded-For` for per-IP limits; enable only behind a proxy that overwrites it. |
| `STORAGE_DIR` | `backend/storage` | Uploaded syllabus files. Never web-served. |
| `FRONTEND_DIR` | `../frontend` | Static UI directory; overridden to `/srv/frontend` in Docker. |
| `MAX_UPLOAD_MB` | `10` | Maximum upload size. |
| `MAX_EXTRACTED_CHARS` | `120000` | Text truncation limit after extraction. |

#### Extraction and duration defaults

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_PROVIDER` | `openrouter` | `none`, `openrouter`, `gemini`, or `ollama`. AI is off unless the selected provider is configured. |
| `LLM_TIMEOUT_SECONDS` | `60` | HTTP timeout for provider calls. |
| `OPENROUTER_API_KEY` | empty | Required when using OpenRouter. |
| `OPENROUTER_MODEL` | `google/gemini-2.0-flash-001` | OpenRouter model ID. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter-compatible endpoint. |
| `GEMINI_API_KEY` | empty | Required when using Gemini directly. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model ID. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint. |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name. |
| `DEFAULT_TOPIC_MINUTES` | `60` | Fallback topic duration when no hint is found. |
| `MIN_TOPIC_MINUTES` | `15` | Lower clamp for extracted topic durations. |
| `MAX_TOPIC_MINUTES` | `480` | Upper clamp for extracted topic durations. |

#### Rate limits

| Variable | Default | Description |
| --- | --- | --- |
| `RATE_LIMIT_AUTH_PER_MINUTE` | `20` | Per-identity limit for login/register. |
| `RATE_LIMIT_UPLOAD_PER_MINUTE` | `10` | Per-identity limit for syllabus upload. |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | `240` | Reserved default API limit. |

#### Development seed

| Variable | Default | Description |
| --- | --- | --- |
| `SEED_DEMO_DATA` | `false` | Seed the demo professor/course when no users exist. Ignored in production. |
| `DEMO_PROFESSOR_NAME` | `Dr. Demo Professor` | Seeded professor name. |
| `DEMO_PROFESSOR_EMAIL` | `professor@example.edu` | Seeded login email. |
| `DEMO_PROFESSOR_PASSWORD` | `professor123` | Seeded login password. |
| `DEMO_COURSE_NAME` | `Data Structures and Algorithms` | Seeded course name. |
| `DEMO_COURSE_CODE` | `CS201` | Seeded course code. |

### LLM providers

The LLM is **optional** and is used for initial syllabus structure extraction
and prompt-driven syllabus refinement. The deterministic rule-based extractor
is always available and is used when:

- `LLM_PROVIDER=none`, or the selected provider has no key configured;
- the provider is not configured or no API key is set;
- the provider times out, fails, or returns malformed/unvalidated output.

> [!NOTE]
> **API Key Location:** API keys are never returned by the API. In development
> they can be saved from Settings, which writes `backend/.env` with mode `0600`;
> in production that endpoint is disabled and keys must be provisioned through
> the environment. AI calls are opt-in per teacher, so a configured server key
> is used only after the teacher enables AI.
>
> With Docker, set the provider variables in the repository-root `.env` (Compose
> interpolation) and recreate the `api` service with
> `docker compose up -d api`. The image does not read the host `backend/.env`.
> The UI then shows the server-managed notice and only offers per-teacher
> enablement, provider, and model selection.

Configure OpenRouter:

```dotenv
# In backend/.env:
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.0-flash-001
```

Configure Gemini directly:

```dotenv
# In backend/.env:
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash
```

Configure Ollama (offline / local):

```dotenv
# In backend/.env:
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

Provider output is treated as untrusted: it is parsed against a strict Pydantic
schema, post-processed (clamping durations to integer minutes, resolving
prerequisites, and breaking cycles), and never used directly as a published
schedule without professor approval. The system prompt explicitly marks
document text as data and instructs the model not to follow instructions
inside it.

## Using the application

1. **Create an account and sign in.** Registration creates a professor account
   and returns an access token immediately.
2. **Create a course.** Set the semester dates, time zone, lecture duration, and
   weekly teaching windows. Holidays, exam days, events, and unavailable days
   can be added during creation or later in course settings.
3. **Upload or paste a syllabus.** Upload PDF, DOCX, or TXT files, or paste raw
   syllabus text directly into the "Paste text" tab. The response is an
   extraction preview, not confirmed course content.
4. **Review, refine with prompts, and confirm the structure.**
   - Use the **"✨ Refine with AI Prompt"** input to ask the AI to reorganize
     units, add new topics, split lectures, or adjust durations via natural language prompts.
   - Manually fine-tune titles, durations, difficulty, prerequisites, priorities,
     and splittability directly in the table.
   - Use **Confirm syllabus** below the reviewed table. Confirmation saves the
     structure and immediately prepares a timetable proposal. After confirmation,
     **Edit structure** lets you add units or topics and change durations;
     **Save structure** persists the full revised structure.
   - Only confirmed topics become schedulable.
5. **Review the proposal.** Confirmation prepares a pending revision. Inspect the
   lecture list, capacity summary, warnings, validation issues, and the
   before/after diff. If generation cannot fit the plan, the Schedule page keeps
   a **Generate schedule** action and explains what is missing.
6. **Approve or reject.** Approval publishes the revision (and supersedes the
   prior one); rejection leaves the previous timetable in place.
7. **Teach and record reality.** Mark a lecture completed, missed, or cancelled.
   For a completed lecture, you can record partial minutes per topic; the
   remainder stays pending.
8. **Recalculate.** The affected remaining work is replanned, delivered work is
   preserved, and a new revision awaits approval.
9. **Monitor progress.** The dashboard and progress views show coverage, at-risk
   topics, upcoming lectures, and the semester calendar.
10. **Restore if needed.** History shows every revision. An approved or
    superseded revision can be restored as a new approved revision; history is
    never rewritten.

The UI is available at `/app/` and uses hash routes such as
`#/courses/<course-id>/schedule`.

## Scheduling engine

The planner lives in
[`backend/app/domain/scheduling/`](backend/app/domain/scheduling/) and is pure
Python.

### Inputs

- Confirmed topics with estimated minutes, priority, sequence, and optional
  prerequisites.
- Delivered minutes from completed lectures.
- Weekly availability windows expanded into concrete slots.
- Calendar blocks (holidays, exams, events, unavailable days).
- Occupied intervals from completed, missed, and cancelled lectures.
- The course time zone and lecture duration.

### Algorithm

1. Build a dependency graph over topics with remaining effort. Completed
   prerequisites are satisfied; cycles are rejected with a clear error.
2. Topologically order ready topics using a deterministic priority queue
   (`priority`, unit sequence, topic sequence, id).
3. Walk lecture slots chronologically and repeatedly take the best ready topic:
   - if it fits, allocate it fully — this naturally combines short topics;
   - if it is splittable and does not fit, fill the slot and continue later;
   - if it is non-splittable and does not fit, defer it and try another ready
     topic that does.
4. If work remains after all slots, return a partial plan with a precise
   shortfall and warnings. The proposal is marked infeasible; it is never
   presented as a complete timetable.
5. Run an independent validator against the hard constraints before persisting
   the revision.
6. Diff the result against the base revision and store an immutable snapshot.

### Hard constraints

- Only configured semester dates and availability windows are used.
- Blocked dates and occupied intervals produce no new slots.
- A lecture never exceeds its capacity.
- A topic is not allocated twice in the same lecture.
- A topic does not start before its prerequisite is complete.
- Completed work is never rescheduled.
- The same lecture slot cannot overlap a fixed session.

Soft preferences are expressed through ordering and by deferring non-splittable
topics rather than fragmenting them.

### Recovery strategies

| Strategy | Behavior |
| --- | --- |
| `preserve_future` (default) | Keep future lectures in place when their exact slot still exists and all prerequisites are still satisfied by that point. Re-plan only what must move. |
| `redistribute_all` | Re-plan all remaining work from the current point, while retaining completed/missed/cancelled history and delivered minutes. |

`auto` is accepted internally and resolves to `preserve_future` or
`redistribute_all` based on the course's `preserve_future_lectures` setting. The
UI preselects the matching explicit strategy.

### Revision lifecycle

```text
                 generate / recalculate
                           │
                           ▼
                  pending_approval
                    │          │
              approve          reject
                    │          │
                    ▼          ▼
                 approved    rejected
                    │
        newer approval or rollback
                    │
                    ▼
                superseded
```

- Revisions are append-only snapshots. Prior revisions are never mutated.
- Only one proposal can be pending at a time. Generating again supersedes the
  previous pending proposal rather than stacking drafts.
- Approval can optionally publish (`publish: true` by default) and notifies the
  professor.
- A revision cannot be approved if a newer schedule was approved since it was
  created; this prevents two tabs or users from silently overwriting each other.
- Rollback restores a previous revision by creating a **new** approved revision,
  leaving the original history intact.
- Replacing the syllabus after an approved schedule exists requires
  `allow_schedule_regeneration: true`; otherwise the API returns
  `SYLLABUS_REPLACES_SCHEDULE`.

### Infeasible schedules

When there is not enough teaching time, the API still returns a proposal
containing:

- `unplaced_topics` with each topic's remaining minutes and reason;
- `capacity.additional_sessions_required`;
- validation issues (errors and warnings);
- a human-readable warning such as the shortfall in minutes and how many extra
  lectures would be needed.

This makes the failure actionable rather than hiding it behind an empty
timetable.

## Syllabus extraction

```text
upload bytes
   │
   ├─ validate size, extension, and magic bytes
   ├─ write file to STORAGE_DIR (auditable even if extraction fails)
   ├─ extract text
   │    ├─ PDF: PyMuPDF text layer
   │    ├─ DOCX: paragraphs + table rows
   │    └─ TXT: UTF-8 / UTF-16 / Latin-1 decoding
   ├─ optional LLM interpretation (validated, schema-bound)
   ├─ deterministic rule-based fallback on any provider failure
   ├─ post-process
   │    ├─ resolve prerequisites by key/title
   │    ├─ drop unknown references and flag topics for review
   │    ├─ break dependency cycles with warnings
   │    └─ clamp durations to MIN/MAX and round to 15-minute steps
   └─ persist preview document metadata (not course content)
```

The extraction preview includes units, topics, confidence, prerequisites,
statistics, provider name, whether an LLM was used, and warnings. Confirmation
is a separate call that writes the real syllabus structure and dependencies.

Document content is never executed, rendered, or trusted as instructions. The
extractor detects instruction-like text and warns that it was treated as data
only.

## API reference

Interactive documentation is generated automatically:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`

### Conventions

- Base path: `/api/v1`
- Authentication: `Authorization: Bearer <access_token>`
- Content type: `application/json` except syllabus upload (`multipart/form-data`)
- All timestamps are ISO 8601; scheduling uses timezone-aware datetimes.
- Paginated collections return:

  ```json
  {
    "items": [],
    "meta": { "total": 0, "limit": 50, "offset": 0, "has_more": false }
  }
  ```

- Errors always use one envelope:

  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "The request contains invalid values.",
      "details": { "problems": [] }
    }
  }
  ```

- Every response carries an `x-request-id` header; logs include the same ID.

### Endpoints

#### Authentication

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/auth/register` | Create a professor account and receive a token. |
| `POST` | `/auth/login` | Sign in and receive a token. |
| `GET` | `/auth/me` | Current account. |

#### Courses

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/courses` | Create a course with availability rules and exceptions. |
| `GET` | `/courses` | List the authenticated professor's courses (`status`, `search`, pagination). |
| `GET` | `/courses/{course_id}` | Course configuration and summary counts. |
| `PATCH` | `/courses/{course_id}` | Partial update; `version` guards concurrent edits. |
| `PUT` | `/courses/{course_id}/availability` | Replace all weekly teaching windows. |
| `POST` | `/courses/{course_id}/exceptions` | Add a holiday, exam, event, or unavailable day. |
| `DELETE` | `/courses/{course_id}/exceptions/{exception_id}` | Remove an exception. |

#### Syllabus

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/courses/{course_id}/syllabus/upload` | Upload and extract a syllabus (PDF/DOCX/TXT); returns a preview. |
| `POST` | `/courses/{course_id}/syllabus/text` | Extract syllabus structure directly from pasted text. |
| `GET` | `/courses/{course_id}/syllabus` | List uploaded documents. |
| `GET` | `/courses/{course_id}/syllabus/documents/{document_id}/preview` | Re-open an extraction preview. |
| `POST` | `/courses/{course_id}/syllabus/documents/{document_id}/refine` | Refine extraction preview structure using an AI prompt. |
| `POST` | `/courses/{course_id}/syllabus/confirm` | Confirm or replace the syllabus structure. |
| `GET` | `/courses/{course_id}/syllabus/structure` | Confirmed units, topics, and total minutes. |
| `PATCH` | `/courses/{course_id}/syllabus/topics/{topic_id}` | Edit a topic, duration, priority, splittability, or prerequisites. |
| `DELETE` | `/courses/{course_id}/syllabus/topics/{topic_id}` | Delete a topic. |

#### Schedule

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/courses/{course_id}/schedule` | Current timetable, optionally a specific `revision_id`. |
| `POST` | `/courses/{course_id}/schedule/generate` | Generate a timetable from the confirmed syllabus. |
| `POST` | `/courses/{course_id}/schedule/recalculate` | Recalculate remaining work as a new revision. |
| `GET` | `/courses/{course_id}/schedule/revisions` | Revision history. |
| `GET` | `/courses/{course_id}/schedule/revisions/{revision_id}` | One revision with lectures and changes. |
| `GET` | `/courses/{course_id}/schedule/revisions/{revision_id}/diff` | Before/after comparison. |
| `GET` | `/courses/{course_id}/schedule/revisions/{revision_id}/changes` | Line-level change ledger. |
| `POST` | `/courses/{course_id}/schedule/revisions/{revision_id}/approve` | Approve and optionally publish. |
| `POST` | `/courses/{course_id}/schedule/revisions/{revision_id}/reject` | Reject a proposal. |
| `POST` | `/courses/{course_id}/schedule/revisions/{revision_id}/rollback` | Restore an approved/superseded revision. |
| `GET` | `/courses/{course_id}/schedule/history` | Human-readable change history. |

#### Lectures

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/courses/{course_id}/lectures` | List lectures (`status`, date range, `revision_id`, pagination). |
| `GET` | `/courses/{course_id}/lectures/{lecture_id}` | Lecture details and per-topic progress. |
| `PATCH` | `/courses/{course_id}/lectures/{lecture_id}/status` | Mark scheduled/completed/missed/cancelled; optionally recalculate. |
| `GET` | `/courses/{course_id}/lectures/calendar/entries` | Calendar entries between two dates. |

#### Progress and notifications

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/courses/{course_id}/dashboard` | Coverage, lecture counts, at-risk topics, schedule state. |
| `GET` | `/courses/{course_id}/progress` | Per-topic and per-unit progress. |
| `GET` | `/courses/{course_id}/calendar` | Day summaries for day/week/semester views. |
| `GET` | `/notifications` | List notifications (`unread_only`, `limit`). |
| `PATCH` | `/notifications/{notification_id}` | Mark one notification read/unread. |
| `POST` | `/notifications/read-all` | Mark all notifications read. |

#### Meta

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness and non-secret configuration probe. |

### Examples

Register and capture a token:

```bash
BASE=http://localhost:8000/api/v1

TOKEN=$(curl -s -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada Lovelace","email":"ada@example.edu","password":"Sup3rSecret!"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Create a course:

```bash
COURSE_ID=$(curl -s -X POST "$BASE/courses" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Operating Systems",
    "course_code": "CS330",
    "department": "Computer Science",
    "semester": "Fall",
    "academic_year": "2026",
    "start_date": "2026-09-07",
    "end_date": "2026-12-20",
    "timezone": "UTC",
    "lecture_duration_minutes": 60,
    "total_weeks": 16,
    "availability_rules": [
      {"weekday": 0, "start_time": "09:00:00", "end_time": "11:00:00", "room": "Room 204"},
      {"weekday": 2, "start_time": "09:00:00", "end_time": "11:00:00", "room": "Room 204"}
    ],
    "exceptions": [
      {"date": "2026-10-12", "exception_type": "holiday", "description": "Fall break"}
    ]
  }' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
```

Upload a syllabus:

```bash
curl -s -X POST "$BASE/courses/$COURSE_ID/syllabus/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@syllabus.pdf"
```

Confirm the reviewed structure (shape abbreviated):

```bash
curl -s -X POST "$BASE/courses/$COURSE_ID/syllabus/confirm" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "replace_existing": true,
    "units": [
      {
        "key": "unit-1",
        "title": "Foundations",
        "sequence_number": 1,
        "topics": [
          {
            "key": "t1",
            "title": "Process lifecycle",
            "sequence_number": 1,
            "estimated_duration_minutes": 60,
            "difficulty": "medium",
            "prerequisite_keys": []
          }
        ]
      }
    ]
  }'
```

Generate, review, and approve:

```bash
SCHEDULE=$(curl -s -X POST "$BASE/courses/$COURSE_ID/schedule/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"strategy":"preserve_future"}')

REVISION_ID=$(echo "$SCHEDULE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"]["id"])')

curl -s "$BASE/courses/$COURSE_ID/schedule/revisions/$REVISION_ID/diff" \
  -H "Authorization: Bearer $TOKEN"

curl -s -X POST "$BASE/courses/$COURSE_ID/schedule/revisions/$REVISION_ID/approve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"publish": true, "note": "Approved for the semester."}'
```

Mark a lecture missed and request recovery:

```bash
curl -s -X PATCH "$BASE/courses/$COURSE_ID/lectures/$LECTURE_ID/status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "missed",
    "reason": "Campus closure",
    "recalculate": true,
    "strategy": "preserve_future"
  }'
```

Record partial delivery when completing a lecture:

```bash
curl -s -X PATCH "$BASE/courses/$COURSE_ID/lectures/$LECTURE_ID/status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "delivered_minutes": [
      {"topic_id": "TOPIC_UUID", "minutes": 30}
    ],
    "recalculate": true
  }'
```

## Data model

Key entities and relationships:

```text
User ──< Course ──< SyllabusUnit ──< Topic ──< TopicAllocation
  │         │                          │
  │         ├──< AvailabilityRule      └──< TopicDependency (topic → prerequisite)
  │         ├──< CalendarException
  │         ├──< SyllabusDocument
  │         ├──< ScheduleRevision ──< LectureSession ──< TopicAllocation
  │         │        │                     └──< ScheduleRevisionChange
  │         │        └──< Notification
  │         └──< AuditLog
  └──< Notification / AuditLog
```

- **`Course`** holds semester configuration, time zone, lecture duration, status,
  a `version` for optimistic concurrency, and course settings.
- **`SyllabusUnit` / `Topic`** hold the confirmed structure. Topics have duration,
  difficulty, priority, splittability, review flags, and dependency edges.
- **`SyllabusDocument`** records uploads, extraction status, metadata, and errors.
- **`ScheduleRevision`** is an immutable timetable snapshot with trigger type,
  approval status, explanation, warnings, metrics, and base revision.
- **`LectureSession`** belongs to a revision and carries scheduled/actual times,
  status, publication state, and notes.
- **`TopicAllocation`** records how many minutes of a topic a lecture teaches and
  what was actually delivered.
- **`ScheduleRevisionChange`** is the line-level change ledger (added, moved,
  removed) with reasons.
- **`Notification`** and **`AuditLog`** provide in-app messages and an audit trail.

Course-level behavior switches (`Course.settings`):

| Setting | Default | Effect |
| --- | --- | --- |
| `auto_publish_reschedules` | `false` | Publish recalculations immediately instead of waiting for approval. |
| `preserve_future_lectures` | `true` | Default recovery preference. |
| `combine_short_topics` | `true` | Allow several short topics in one lecture. |
| `min_allocation_minutes` | `5` | Minimum fragment size when splitting. |
| `notes` | empty | Free-form course notes. |

## Security

Implemented protections:

- **Password hashing** with Argon2 through `pwdlib`; passwords are never stored
  or returned in clear text.
- **JWT access tokens** with `exp` and `sub` required, explicit token type
  checking, and configurable expiry.
- **Course-level authorization** — every `/courses/{id}` request resolves the
  course and verifies the authenticated user owns it.
- **Rate limiting** on auth and upload endpoints (sliding window, per identity).
- **Upload validation** — size limits, extension/content-type checks, magic-byte
  checks for PDF/DOCX, binary-content checks for TXT, and sanitized filenames.
- **Storage isolation** — uploaded documents are written under `STORAGE_DIR` and
  are not served as static files.
- **Untrusted content handling** — document text is delimited as data, LLM output
  is schema-validated, and instruction-like text is reported but never obeyed.
- **Consistent error envelopes** that do not leak stack traces or secrets.
- **Health endpoint** reports configuration flags only, never credentials.
- **Audit logging** for authentication, course/syllabus changes, schedule
  generation/approval/rejection, and lecture status changes.
- **CORS** limited to configured origins; the bundled UI is same-origin and
  needs no cross-origin access.

Production checklist:

- Set a long, random `JWT_SECRET`; never use the development placeholder.
- Use PostgreSQL with a strong password, not SQLite.
- Set `APP_ENV=production` and `SEED_DEMO_DATA=false`.
- Terminate TLS at a reverse proxy. Set `TRUST_FORWARDED_FOR=true` only when
  that proxy overwrites `X-Forwarded-For`; otherwise per-IP limits would trust
  a spoofable header.
- Keep `.env` files out of version control and restrict filesystem permissions.
- Use a persistent volume for `STORAGE_DIR`.
- Replace the in-process rate limiter with a shared store (for example Redis) if
  you run multiple API instances.
- Back up the database and uploaded files together.
- Review the notification model: messages are in-app only; no email is sent.

## Testing and quality checks

The test suite uses a temporary SQLite database, disables the LLM, disables demo
seeding, and raises rate limits so tests stay fast and deterministic.

```bash
cd backend

# Install test dependencies.
uv sync --extra dev

# Run the full suite (279 tests).
uv run python -m pytest -q

# With coverage.
uv run python -m pytest --cov=app --cov-report=term-missing

# Focused suites.
uv run python -m pytest tests/test_planner.py -q
uv run python -m pytest tests/test_api_integration.py -q

# Static checks.
uv run ruff check app tests
uv run mypy app
```

> Run pytest as `python -m pytest` from `backend/` so the `app` package is on the
> import path.

Coverage includes:

- planner ordering, splitting, capacity, prerequisites, cycles, and diffs;
- calendar expansion, holidays, exams, time zones, and occupied intervals;
- PDF/DOCX/TXT extraction, direct text paste, and corrupt uploads;
- rule-based extraction, LLM provider adapters, prompt refinement, fallback behavior, and schema
  validation;
- auth, token validation, rate limiting, course isolation, and path traversal;
- the complete professor workflow: upload → prompt refine → confirm → generate → approve →
  teach → miss → recover → rollback;
- concurrency guards, stale revisions, validation errors, and notification
  behavior.

## Database migrations

Alembic lives in `backend/alembic/` and is configured by
`backend/alembic.ini`.

```bash
cd backend

# Apply all migrations.
uv run alembic upgrade head

# Inspect history.
uv run alembic history --verbose

# Create a migration after changing models.
uv run alembic revision --autogenerate -m "describe the change"

# Roll back one revision (development only; review destructive changes first).
uv run alembic downgrade -1
```

### Cleaning / resetting the database (development)

To reset the SQLite database to a completely clean state:

```bash
cd backend
rm -f data/app.db
uv run alembic upgrade head
```

This removes all existing rows and documents, re-applies all schema migrations cleanly to head, and upon starting the server will re-seed fresh demo data if `SEED_DEMO_DATA=true`.

Notes:

- In `development` and `test`, the app calls `Base.metadata.create_all()` at
  startup, but migrations remain the source of truth for existing databases.
- In `production`, startup does **not** create tables. The Compose command runs
  `alembic upgrade head` before Uvicorn starts. If you deploy without Compose, run
  the migration yourself before starting the app.
- Always back up the database before applying destructive migrations.

## Deployment notes

The included Docker image is a reasonable starting point:

- Python 3.12 slim base, non-root `appuser` (UID 10001).
- `uv sync --frozen --no-dev` for reproducible installs.
- curl installed for container health checks.
- Static frontend mounted read-only from `./frontend` in Compose.
- Persistent volumes for PostgreSQL data and uploaded files.
- Health check: `GET /health` every 30 seconds.
- JSON logs with request IDs on stdout.

For a production deployment:

1. Put the API behind a TLS-terminating reverse proxy (Caddy, Nginx, Traefik).
2. Set `APP_ENV=production`, `DEBUG=false`, and a strong `JWT_SECRET`.
3. Use managed PostgreSQL or a backed-up PostgreSQL volume.
4. Pin and scan the image; rebuild when dependencies change.
5. Configure log collection and alerting on 5xx responses and health failures.
6. Add a shared rate-limit backend if scaling beyond one instance.
7. Decide whether automatic publishing is acceptable per course. The default is
   manual approval.
8. Test restore and rollback before the semester starts.

## Troubleshooting

### `docker compose up` refuses to start with a JWT error

Compose requires `JWT_SECRET`. Create `.env` from `.env.example` and set a long
random value:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### The UI returns 404 at `/app/`

- Confirm `frontend/index.html` exists.
- Locally, `FRONTEND_DIR` defaults to `../frontend` relative to `backend/`.
  Override it if you run from another directory.
- In Docker, the Compose volume must mount `./frontend` at `/srv/frontend`.

### The extraction warning says no LLM provider is configured

That is expected with the default `LLM_PROVIDER=none`. The deterministic
rule-based extractor is used. Configure OpenRouter or Ollama if you want LLM
assistance.

### `uv run pytest` cannot import `app`

Run it through the interpreter module instead:

```bash
cd backend
uv run python -m pytest -q
```

### Login works but every API call returns 401

The token may be expired (`ACCESS_TOKEN_EXPIRE_MINUTES`) or signed with a
different `JWT_SECRET`. Sign in again after changing configuration.

### A schedule change is not visible

A recalculation creates a `pending_approval` revision. The current timetable
does not change until it is approved and published. Check the dashboard and
history views for the pending proposal.

### A revision cannot be approved

The API rejects stale approvals with `STALE_REVISION_APPROVAL` or
`REVISION_SUPERSEDED`. Reload the course, review the current revision, and
recalculate if necessary.

### Resetting local data

SQLite development data lives in `backend/data/app.db` (or the path in
`DATABASE_URL`). Stop the API before deleting it. For Compose, `down -v` deletes
the database and upload volumes — use it only when you intend to lose data.

## Design decisions

- **The scheduler is not an LLM.** Language models are good at messy document
  structure and bad at guaranteeing hard constraints, reproducibility, and
  auditability. Scheduling is pure, deterministic Python.
- **Proposals require approval.** Missed lectures and configuration changes are
  disruptive; the professor sees a diff before students see a change.
- **Revisions are immutable.** History, rollback, and “what changed and why” are
  first-class features, not logs reconstructed after the fact.
- **Extraction and confirmation are separate.** An upload can be wrong; nothing
  becomes schedulable until a human confirms it.
- **Optimistic concurrency everywhere it matters.** Course `version`, base
  revision checks, and revision approval guards prevent silent overwrites.
- **No frontend build chain.** The UI is intentionally boring to deploy: static
  files served by the API.
- **Graceful degradation.** Missing LLM, provider failure, or
  insufficient capacity all produce actionable warnings instead of crashes.
- **Local-first defaults.** SQLite and rule-based extraction work with no
  external services; PostgreSQL and LLM are opt-in.

## Current limitations and possible next steps

Current scope and known limitations:

- Professor-facing only; there is no student portal or public timetable page.
- Notifications are in-app only; no email, push, SMS, or calendar export.
- Automatic publishing is all-or-nothing per course; there is no per-change
  policy.
- The rate limiter is in-process, so it is not shared across replicas.
- Extraction is synchronous; very large documents hold the request open.
- Scanned/image-only PDFs are not supported: extraction reads the embedded text
  layer only and does not run OCR.
- No drag-and-drop manual timetable editing (changes go through recalculation).
- No SSO/SAML, multi-tenant organizations, or admin console.
- No iCal/ICS export or LMS integration.
- SQLite is for development; use PostgreSQL for concurrent production use.

Natural next steps:

- iCal/ICS export and a read-only student timetable.
- Email reminders and configurable notification channels.
- Redis-backed rate limiting and background extraction jobs.
- Manual drag-and-drop lecture moves with automatic validation.
- LMS integrations (Canvas, Moodle, Blackboard) for syllabus import and calendar
  export.
- Organization/tenant support and SSO.
- More granular approval policies and change subscriptions.

## Contributing

Before opening a pull request:

1. Keep the scheduling domain free of FastAPI, SQLAlchemy, and network imports.
2. Add or update tests for behavior changes — especially planner constraints,
   recovery semantics, and authorization.
3. Run `uv run python -m pytest -q`, `uv run ruff check app tests`, and
   `uv run mypy app` from `backend/`.
4. Update this README and `backend/.env.example` when configuration or behavior
   changes.
5. Never commit `.env` files, secrets, uploaded documents, or local databases.

## License

No license file is included in this repository. Add one before distributing or
accepting external contributions; until then, the default copyright rules apply.
