# Data Model — LecturePlan AI

All durations are **integer minutes**. No table stores float hours. Timestamps
are UTC; date-based scheduling uses the course timezone.

Backend model names keep `Course`; the product/UI name is **Subject**. There is
exactly one implementation: `courses`, `/api/v1/courses`, and `course_id`.

## Entity map

```
User ──< Course ──< SyllabusUnit ──< Topic ──< TopicAllocation
  │         ├──< AvailabilityRule          │
  │         ├──< CalendarException         │
  │         ├──< SyllabusDocument          │
  │         ├──< ScheduleRevision ──< LectureSession ──< TopicAllocation
  │         │        └──< ScheduleRevisionChange
  │         └──< Notification
  ├──< UserSettings (1:1)
  ├──< EmailDelivery
  └──< AuditLog
Topic ──< TopicDependency >── Topic
Topic ── TopicSuggestion (1:1, latest AI proposal)
```

## Tables

### `users`
`id` UUID PK · `name` · `email` unique · `password_hash` · `role` ·
`is_active` · `department` · `college_name` · `timezone` · `token_version` ·
`last_login_at` · timestamps.

`token_version` is incremented on password change; tokens carrying an older
version are rejected.

### `user_settings`
`id` · `user_id` unique FK → users (CASCADE) ·
`default_lecture_minutes > 0` · `default_teaching_days` JSON (weekday integers
0–6) · `weekly_email_enabled` · `email_weekday` 0–6 · `email_hour_local` 0–23 ·
`ai_enabled` · `preferred_ai_provider` (`none`/`openrouter`/`ollama`) ·
`preferred_ai_model` · timestamps.

No API key or SMTP password is ever stored here.

### `courses` (UI: Subjects)
`id` · `name` · `course_code` nullable · `professor_id` FK → users (CASCADE) ·
`department` · `semester` · `academic_year` · `start_date` · `end_date`
(semester end, scheduling horizon) · `target_completion_date` nullable
(must fall inside the semester) · `total_available_minutes` nullable, > 0 ·
`timezone` · `status` · `lecture_duration_minutes` · `total_weeks` · `version`
(`CHECK end_date >= start_date`) · `settings` JSON · timestamps.

`total_available_minutes` is an optional teacher-declared teaching budget. When
absent, capacity is derived from availability rules and the semester window.

### `syllabus_documents`
Uploaded file metadata, `extracted_text`, extraction metadata, status, and
error. The original file lives under `STORAGE_DIR`, outside the static web
root.

### `syllabus_units`
`id` · `course_id` FK (CASCADE) · `title` · `description` · `sequence_number`
(unique per course) · `planned_minutes` nullable unit cap · `archived_at` ·
timestamps.

Archived units stay readable for history but are excluded from new schedules.

### `topics`
`id` · `unit_id` FK (CASCADE) · `original_name` (extracted) · `title`
(teacher-approved display name) · `description` · `learning_objectives` JSON ·
`estimated_duration_minutes > 0` · `difficulty` (`easy`/`medium`/`hard`) ·
`sequence_number` (unique per unit) · `priority` 1–5 · `is_splittable` ·
`extraction_confidence` · `needs_review` · `analysis_status` ·
`analysis_approved_at` · `archived_at` · `duration_locked` · timestamps.

`difficulty` is mapped to a numeric score for allocation: easy = 2,
medium = 3, hard = 4. This keeps the verified extraction pipeline while giving
the allocator a numeric weight. The label and score are never stored twice.

Completed minutes are **not** a column here. They are derived from
`topic_allocations.actual_minutes` by the progress service.

### `topic_dependencies`
`topic_id` · `prerequisite_topic_id` · `dependency_type`, unique pair, no
self-dependency.

### `topic_suggestions`
One latest AI proposal per topic: suggested title, difficulty, priority,
minutes, prerequisite keys JSON, explanation, confidence (0–1), provider,
model, raw payload, timestamps. Unique `topic_id` (CASCADE).

Approving a suggestion copies values onto `Topic`; the suggestion row remains an
audit record.

### `availability_rules`
`course_id` FK · `weekday` 0–6 · `start_time` / `end_time` (`end > start`) ·
`room` · `effective_from` / `effective_until` · `is_enabled`, unique
`(course_id, weekday, start_time, end_time)`.

### `calendar_exceptions`
`course_id` FK · `date` · `exception_type` · `description` · `blocks_teaching`,
unique `(course_id, date, exception_type)`.

### `schedule_revisions` (append-only)
`course_id` · `revision_number` unique per course · `trigger_type` ·
`trigger_lecture_id` (audit reference, no FK to avoid a cycle) ·
`change_summary` · `explanation` JSON · `warnings` JSON · `metrics` JSON ·
`approval_status` · `created_by` · `approved_by` · `approved_at` ·
`published_at` · `base_revision_id` · timestamps.

At most one revision is the published base for a course. New work always creates
a new revision; prior revisions remain queryable.

### `lecture_sessions`
`id` · `course_id` FK · `revision_id` FK (CASCADE) · `sequence` unique per
revision · `scheduled_start` / `scheduled_end` (`end > start`) · `status`
(`SCHEDULED`/`COMPLETED`/`MISSED`/`CANCELLED`/`RESCHEDULED`) · `actual_start` /
`actual_end` · `cancellation_reason` · `notes` · `is_published` · `source`
(`PLANNED`/`EXTRA`/`RESCHEDULED`) · `rescheduled_from_id` self-FK (SET NULL) ·
`idempotency_key` unique per course · `carry_over_from_session_id` self-FK ·
timestamps.

Closed sessions (`COMPLETED`, `MISSED`, `CANCELLED`, `RESCHEDULED`) are never
deleted or rewritten by recalculation. Correcting one requires an explicit
administrative action.

### `topic_allocations` — the single progress ledger
`id` · `lecture_session_id` FK (CASCADE) · `topic_id` FK (CASCADE) ·
`planned_minutes > 0` · `actual_minutes` null or `0 ≤ actual ≤ planned` ·
`completion_status` (`PLANNED`/`IN_PROGRESS`/`COMPLETED`/`SKIPPED`) ·
`sequence_in_session`, unique `(lecture_session_id, topic_id)` · timestamps.

Rules:

- A lecture may teach several topics; one row per pair.
- The sum of planned minutes in a session must not exceed the session duration
  (enforced in the service layer).
- Completed minutes come only from `actual_minutes` on completed/partial
  sessions. A missed lecture contributes zero.
- A topic's completed total is capped at its approved planned minutes.

### `schedule_revision_changes`
Audit rows describing moved/added/removed lectures: revision, lecture, topic,
change type, previous/new date, previous/new minutes, reason.

### `notifications`, `audit_logs`
Existing user-facing notifications and append-only audit records.

### `email_deliveries`
`user_id` FK (CASCADE) · `period_start` · `period_end` · `kind` (`weekly_summary`
or test) · `status` (`PENDING`/`SENT`/`FAILED`/`SKIPPED`) · `sent_at` · `error` ·
timestamps, unique `(user_id, period_start, period_end, kind)`.

The unique key makes the in-process scheduler idempotent.

## Enum storage

SQLAlchemy `Enum(native_enum=False)` stores the **member name** (uppercase) in
SQLite, while APIs serialize the **value** (lowercase). New enum columns use a
matching uppercase `server_default`, e.g. `source = 'PLANNED'`,
`analysis_status = 'NOT_ANALYZED'`.

## Migration policy

- `alembic upgrade head` is the only way to create a non-test database.
- Tests use `Base.metadata.create_all`; `tests/test_migrations.py` proves the
  migrated schema has exactly the same tables and columns as the models.
- SQLite DDL is not transactional. If a migration fails locally, delete the
  local database and rerun; never hand-edit `alembic_version`.
