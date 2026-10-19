# Build Status — LecturePlan AI

Single source of truth for phase progress. Update after every gate.

## Baseline — 2026-10-19

| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend && .venv/bin/python -m pytest -q` | **125 passed** |
| Lint | `.venv/bin/ruff check app tests` | **clean** |
| Type check | `.venv/bin/mypy app` | **clean (55 files)** |
| Python | `.venv/bin/python --version` | 3.12.15 |
| Node / npm | `node --version`, `npm --version` | v22.22.3 / 12.0.2 |

### Baseline notes

- The five failing integration tests were caused by `reveal_type(...)` debug
  calls in `backend/app/services/progress.py`. They were removed.
- Lint fixes: unused `TYPE_CHECKING` import in `app/services/scheduling.py`;
  missing `ScheduleRevision` import in `app/api/routes/schedule.py`.
- Type fixes: annotated `PlannerSlot.date() -> date`; rewrote a generator in
  `_minutes_remaining_in_topics` that mypy mis-resolved.
- The frontend is the legacy vanilla JS app (`frontend/index.html`, `js/`,
  `styles.css`). It is not React/Vite/TypeScript. The rebuild starts in
  Phase 11.
- `docker-compose.yml` and `psycopg` still exist; they are removed in Phase 13
  under the SQLite-only decision.

## Phase ledger

| Phase | Scope | Status | Gate result |
|---|---|---|---|
| 0 | Baseline stabilization and build contract | **DONE** | 125 tests, ruff, mypy green; Git initialized; contract written |
| 1 | Schema, migrations, configuration contracts | PENDING | — |
| 2 | Authentication, profile, settings | PENDING | — |
| 3 | Subject and syllabus management | PENDING | — |
| 4 | PDF import, normalization, preview | PENDING | — |
| 5 | AI-assisted analysis and providers | PENDING | — |
| 6 | Teaching-hour allocation engine | PENDING | — |
| 7 | Availability and deterministic scheduling | PENDING | — |
| 8 | Lecture lifecycle and rescheduling | PENDING | — |
| 9 | Progress and dashboard | PENDING | — |
| 10 | Weekly email summaries | PENDING | — |
| 11 | React foundation | PENDING | — |
| 12 | React feature slices | PENDING | — |
| 13 | E2E acceptance, docs, cleanup | PENDING | — |

## Known limitations (tracked)

- Password reset is not implemented; document in Phase 13.
- Free OpenRouter model availability can change; the app must always fall back
  to manual mode.
- Automatic weekly email requires the backend process to be running; recommend
  one worker for SQLite.
- Historical demo database migration is out of scope; sample data is reseeded.
