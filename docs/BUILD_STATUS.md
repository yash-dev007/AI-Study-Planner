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

## Full-project audit gate — 2026-10-20

Five parallel audit teams reviewed security, scheduling, data integrity, AI and
document ingestion, and the frontend. Every fix is pinned by a regression test;
the new suites are `test_audit_security.py` (29), `test_audit_scheduler.py` (19),
`test_audit_data.py` (10), `test_audit_ai.py` (37), and
`test_audit_integration.py` (11).

- Security: professor-only registration (`403 ROLE_NOT_ALLOWED`); CSRF bypass via
  malformed `Authorization` closed; spoofable `X-Forwarded-For` no longer trusted
  by default; production refuses the default/weak JWT secret and unsupported
  algorithms; malformed `ver` claims return `401`; rate-limiter state is
  bounded; change-password is throttled; DB log parameters are redacted; login
  timing is equalized; logout clears cookies with the same attributes; IPv6
  origins normalize correctly.
- Scheduler: DST spring-forward can no longer double-book one instant; repeated
  completion reports no longer inflate delivered minutes; calendar days,
  dashboard counters, at-risk dates, and lecture filters use the course
  timezone; dashboard free capacity is computed from real slots and occupied
  intervals instead of counting whole blocked days; projected-completion
  warnings use the course calendar day.
- Data: replacing a syllabus can no longer delete topics whose allocations carry
  recorded history (`409 SYLLABUS_HAS_HISTORY`); partial structure saves keep
  omitted rows on positive, contiguous sequence numbers; topic reordering no
  longer collides with the unique constraint; explicit `null` on NOT NULL
  fields is a `422`; cross-course syllabus ids return `404`; archived units are
  excluded from new plans; prerequisite lookup is one query; course
  `target_completion_date` is validated on update.
- AI/ingestion: Gemini keys travel in a header and are redacted from errors; env
  writes are skipped under `APP_ENV=test` and atomic with mode `0600`; control
  characters in provider metadata are rejected; DOCX zip bombs and oversized
  PDFs are refused; BOM handling covers UTF-32; provider schemas are strict and
  bounded; fenced JSON is parsed; warnings are deterministic and
  provider-authored warnings are not surfaced.
- Frontend: sessions use the HttpOnly cookie (no bearer token in localStorage);
  route gating hydrates from `GET /auth/me`; logout calls the API; the UI reads
  `planned_minutes` / `actual_minutes`; unsupported extraction columns and the
  misleading "Publish immediately" control were removed; modal double-submit,
  focus trap, skip-link routing, timer leak, and ARIA labels were fixed.
- Integration: `LLM_PROVIDER=none` disables AI even with a key present; ownership
  failures return `404`; `/health` no longer leaks `storage_dir`; course
  timezones are validated; `auto_publish` was removed from generate/recalculate
  so approval remains explicit; AI is opt-in per teacher; uploads are size
  checked while reading; refine payloads are bounded; prerequisite validation is
  order-independent; production refuses UI writes of server AI keys
  (`403 AI_CONFIG_UI_DISABLED`); only `backend/.env` is loaded, so a root
  Compose `.env` can no longer override keys saved from the Settings UI. OCR was
  already absent from the working tree
  and is now removed from docs, Compose, the Dockerfile, and `pyproject.toml`.
- Gate: `.venv/bin/python -m pytest` → **279 passed**;
  `.venv/bin/ruff check app tests` → clean;
  `.venv/bin/python -m mypy app` → clean (59 files; the `.venv/bin/mypy` shim
  has a stale mount-path shebang, so the module form is the working command);
  `alembic upgrade head` → `downgrade base` on a temporary SQLite database →
  clean; every `frontend/js/**/*.js` module parses with
  `node --input-type=module --check`.
- Reported but not changed (need a product decision): `DELETE /courses/{id}`
  still cascade-deletes closed history; `duration_locked` is not enforced; a
  lecture can be completed before its scheduled date; revision-change dates in
  history use UTC instants; `idempotency_key` is stored but unused; and
  `POST /settings/ai-config` remains a development-only, server-wide key writer.
  There is also no admin provisioning path after registration was restricted to
  professors.

## Reported runtime fixes — 2026-10-20

- **Delete returned `405`:** the running Docker `api` image predated the new
  `DELETE /courses/{id}` route. Rebuilding `api` registered the route; a live
  delete returned `200` and the OpenAPI schema now lists `delete`, `get`,
  `patch`. No database volume was reset.
- **AI save failed in production:** this is intentional. Production keys are
  environment-only, so `POST /settings/ai-config` returns
  `403 AI_CONFIG_UI_DISABLED`. `GET /settings` now exposes
  `ai_config_writable`; when it is `false` the UI hides the key form and offers
  per-teacher enablement, provider, and model through `PUT /settings`. The
  syllabus page shows whether AI is unavailable, available but disabled, or
  enabled for the teacher instead of claiming AI is active.
- **Docker AI configuration:** Compose now forwards `OPENROUTER_MODEL`,
  `GEMINI_API_KEY`, `GEMINI_MODEL`, and `OLLAMA_MODEL`. Operators set
  `LLM_PROVIDER` and the matching key in the repository-root `.env`, then run
  `docker compose up -d api`. The container does not read host `backend/.env`.
- **Syllabus structure could not be saved:** the extraction preview now repeats
  the `Confirm syllabus` action below the topic table so it is reachable after a
  long review. The confirmed-structure view has an `Edit structure` mode with
  add-unit/add-topic controls and a real `Save structure` button backed by
  `PUT /courses/{id}/syllabus`; deletes still go through the history-guarded
  `DELETE` endpoints. No fake local-only save was added.
- **Confirming the syllabus left Schedule empty:** confirmation persisted the
  structure but did not create a timetable, and the empty Schedule state linked
  back to the syllabus instead of generating. Confirmation now generates a
  pending timetable proposal with the course's default strategy before
  navigating to Schedule. If generation cannot run, the error is surfaced and
  the Schedule empty state's primary action is `Generate schedule`.
- Gate: `.venv/bin/python -m pytest` → **279 passed**;
  `.venv/bin/ruff check app tests` → clean;
  `.venv/bin/python -m mypy app` → clean (59 files); every frontend module
  parses; `docker compose config --quiet` → valid; rebuilt container `/health`
  → `200` in production with AI disabled. Live `GET /settings` for a fresh
  professor reported `ai_config_writable=false`, and the running OpenAPI schema
  includes `ai_config_writable` plus `PUT /courses/{course_id}/syllabus`.
- New regression assertions cover the production `ai_config_writable=false`
  response and the ability to opt in through `PUT /settings` without handling
  the provider secret.

## Known limitations (tracked)

- Password reset is not implemented; document in Phase 13.
- Free OpenRouter model availability can change; the app must always fall back
  to manual mode.
- Automatic weekly email requires the backend process to be running; recommend
  one worker for SQLite.
- Historical demo database migration is out of scope; sample data is reseeded.
