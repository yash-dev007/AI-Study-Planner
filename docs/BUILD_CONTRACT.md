# Build Contract — LecturePlan AI

This document is binding for every contributor and every coding agent. Read it
before changing code. A phase is not complete if it violates any invariant.

## Execution rules

1. Build one phase at a time, in the order defined in `docs/BUILD_STATUS.md`.
2. Run the relevant test suite at every gate. Never start the next phase on a
   red build.
3. Record the gate result in `docs/BUILD_STATUS.md` with the exact command and
   outcome.
4. Prefer fixing the existing backend over rewriting it. The scheduler core is
   verified and is the most expensive part of the project to reproduce.
5. Do not print, log, or commit the contents of `.env`.

## Invariants

### I1 — Integer minutes everywhere
All durations in the database, API, services, and algorithms are `int` minutes.
Never store or compute float hours. Conversion to hours happens only in
frontend display helpers. Name every duration with its unit, e.g.
`estimated_duration_minutes`, `allocated_minutes`, `actual_minutes`.

### I2 — Allocation junction is the source of truth
A lecture can teach multiple topics. `topic_allocations` holds one row per
`(lecture_session_id, topic_id)` with `planned_minutes`, `actual_minutes`, and
completion status. No duplicate pairs. Allocations must not exceed the
lecture's available duration.

### I3 — SQLite safety
Every connection enables `PRAGMA foreign_keys=ON` and WAL mode, with a
`busy_timeout`. Multi-record mutations run in one transaction. Tests must prove
foreign-key enforcement and rollback behavior.

### I4 — History is immutable
Completed, missed, cancelled, and rescheduled lecture records are never deleted
or overwritten by recalculation. A new schedule is always a new revision. The
previously approved revision remains queryable. Correcting a closed lecture
requires an explicit administrative action.

### I5 — Deterministic rescheduling state machine
AI never calculates dates, hours, statuses, or progress. Recalculation always:
(1) validates and locks the revision/lecture, (2) records delivery or
non-delivery, (3) recomputes remaining minutes from the allocation ledger,
(4) builds a new future draft that requires teacher approval. Same inputs and
base revision produce the same output.

### I6 — No double counting
Completed minutes come from allocation `actual_minutes` only. Missed lectures
contribute zero. Partial completion contributes only actual minutes. A topic's
completed minutes never exceed its approved planned minutes. Topic completed
minutes are derived by the progress service, never stored as a second counter.

### I7 — Ownership on every query
Every subject, unit, topic, lecture, allocation, revision, email setting, and
progress query is scoped to the authenticated teacher. Never trust a
client-supplied ID without a backend ownership join. Cross-user tests are
mandatory. Ownership failures return `404`.

### I8 — AI assists, never controls
AI may suggest normalized names, difficulty, priority, minutes, prerequisites,
and explanations. AI output is validated before use and stored separately from
teacher-approved values. Scheduling requires approved values. AI failure leaves
every manual workflow usable.

### I9 — Phase-gate execution
One phase at a time. Full relevant suite green at every gate. Gate results are
recorded in `docs/BUILD_STATUS.md`. A weaker executor must not be asked to
build everything at once.

### I10 — No fake persistence
Every button that changes data calls the backend. Dashboard and progress values
come from database records. Sample data is seeded through a script, never
hardcoded into UI components.

## Product decisions

- **Subject vs Course:** the UI, docs, and React types say **Subject**. The
  backend keeps `Course`, `/api/v1/courses`, and `course_id` internally. The
  React service layer maps Subject to the course payload. No parallel
  implementation.
- **Frontend:** React + Vite + TypeScript + Tailwind + FullCalendar. The legacy
  vanilla frontend lives in `frontend/legacy/` only until React parity is
  proven, then it is deleted.
- **Auth:** login sets an HttpOnly, SameSite=Lax cookie. Bearer tokens remain
  accepted for tests and API clients. Logout clears the cookie. Password change
  increments `token_version` and invalidates old tokens.
- **Deployment:** SQLite, Python, and Node only. PostgreSQL and Docker are not
  part of the required path and are removed in Phase 13.
- **AI:** OpenRouter or Ollama, optional. Provider/model preference per user;
  keys remain environment variables and are never returned by the API.
- **Email:** SMTP through environment configuration, optional, with an
  in-process scheduler and idempotent delivery records.
- **Time:** store instants in UTC; generate date-based slots in the subject's
  timezone; document DST behavior.

## Gate commands

Backend (from `backend/`):

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Frontend (from `frontend/`, once scaffolded):

```bash
npm run typecheck
npm run lint
npm run test -- --run
npm run build
```

Acceptance (from `frontend/`):

```bash
npx playwright test
```

## Definition of done for a phase

- The phase's gate commands pass.
- New behavior has unit or integration tests.
- Ownership and secret-exposure paths are tested when the phase touches them.
- `docs/BUILD_STATUS.md` records the date, commands, and result.
- No invariant above is weakened to make a test pass.
