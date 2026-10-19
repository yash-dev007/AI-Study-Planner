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
| 1 | Schema, migrations, configuration contracts | **DONE** — 131 tests, migration up/down, schema drift test | — |
| 2 | Authentication, profile, settings | **DONE** | 167 tests, ruff, mypy green |
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

## Phase 1 gate — 2026-10-19

- Added `user_settings`, `topic_suggestions`, `email_deliveries`.
- Extended `users` (department, college, timezone, token version), `courses`
  (optional code, target completion date, total available minutes),
  `syllabus_units` (unit cap, archive), `topics` (original name, analysis
  status, approval, archive, duration lock), and `lecture_sessions` (source,
  reschedule provenance, idempotency key).
- Renamed allocation columns to `planned_minutes` / `actual_minutes` and added
  database checks: planned > 0, actual ≥ 0, actual ≤ planned.
- Added `PRAGMA busy_timeout=5000`; FK cascade and rollback tests added.
- Migration `b61a99d968ce` round-trips (`upgrade head` → `downgrade base`) and a
  drift test proves migrated columns equal model-declared columns.
- Contracts frozen in `docs/DATA_MODEL.md` and `docs/API_CONTRACT.md`.
- Deviation recorded: `difficulty` stays a three-level label mapped to a
  numeric allocation score (easy 2, medium 3, hard 4) instead of a new 1–5
  column; `end_date` stays required and `target_completion_date` is the
  optional goal date.

## Phase 2 gate — 2026-10-19

- Register and login set an HttpOnly, `SameSite=Lax` session cookie (Secure in
  production). Bearer tokens remain accepted and take precedence over a stale
  cookie, so API clients and tests are unaffected.
- `POST /auth/logout` clears the cookie. `PATCH /auth/profile` updates name,
  department, college, and timezone. `POST /auth/change-password` verifies the
  current password, increments `token_version` (revoking every prior token with
  `401 TOKEN_REVOKED`), and rotates the cookie in the same response.
- `app/core/csrf.py` guards cookie-authenticated unsafe requests: `Origin`,
  falling back to `Referer` or `Sec-Fetch-Site: same-origin`, must match the
  request host or a configured CORS origin. Foreign, missing, and `null`
  origins get `403 CSRF_REJECTED`. Login and registration are exempt because
  they establish the session; Bearer requests are exempt by construction.
  The guard is registered before `CORSMiddleware` so CORS headers decorate its
  responses.
- `GET/PUT /settings` persist per-teacher lecture defaults, teaching days,
  weekly email preference, and AI preference. Responses expose only
  configuration booleans (`openrouter_configured`, `ollama_configured`,
  `smtp_configured`); keys and passwords never leave the server.
- 36 new tests across `tests/test_auth_profile.py` and `tests/test_settings.py`,
  covering cookie flags, CSRF accept/reject paths, password rotation, token
  revocation, duplicate email, secret non-disclosure, defaults, validation, and
  cross-user isolation. `test_unauthorized_course_access_is_forbidden` now
  clears the cookie jar before asserting anonymous access, because registration
  legitimately establishes a session.
- Gate: `.venv/bin/python -m pytest` → **167 passed**;
  `.venv/bin/ruff check app tests` → clean;
  `.venv/bin/mypy app` → clean (59 files).
- Deferred by scope: `POST /settings/test-email` and weekly-summary sending
  belong to Phase 10; `POST /settings/test-ai` belongs to Phase 5.

## Known limitations (tracked)

- Password reset is not implemented; document in Phase 13.
- Free OpenRouter model availability can change; the app must always fall back
  to manual mode.
- Automatic weekly email requires the backend process to be running; recommend
  one worker for SQLite.
- Historical demo database migration is out of scope; sample data is reseeded.
