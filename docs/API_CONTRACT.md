# API Contract — LecturePlan AI

Base path: `/api/v1`. OpenAPI is served at `/openapi.json` and `/docs`.

The UI calls this entity **Subject**; the API keeps the implementation name
**Course**. `subjectsService` in the React app maps Subject fields to the
`Course` payload. There is no parallel `/subjects` implementation.

## Conventions

- JSON request and response bodies.
- All routes require authentication except `register`, `login`, and `health`.
- Current authentication: `Authorization: Bearer <token>`. Phase 2 adds an
  HttpOnly cookie while retaining Bearer support for tests and API clients.
- Ownership failures return `404` so another teacher's records are not
  disclosed.
- List responses use `{ items, total, limit, offset }` (`Page[T]`).
- Errors use a consistent envelope:

```json
{
  "error": {
    "code": "COURSE_NOT_FOUND",
    "message": "Course not found.",
    "details": {},
    "request_id": "..."
  }
}
```

Common statuses: `400`, `401`, `403`, `404`, `409`, `413`, `422`, `429`, `500`.

## Implemented endpoints (frozen as of Phase 1)

### Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | name, email, password, role |
| POST | `/auth/login` | returns access token + user |
| GET | `/auth/me` | current profile |

### Subjects (`courses`)
| Method | Path | Notes |
|---|---|---|
| POST | `/courses` | create; availability and exceptions accepted inline |
| GET | `/courses` | paginated list, optional `status`, `search` |
| GET | `/courses/{course_id}` | detail with rules, exceptions, counts |
| PATCH | `/courses/{course_id}` | partial update; `version` guards concurrency |
| PUT | `/courses/{course_id}/availability` | replace weekly windows |
| POST | `/courses/{course_id}/exceptions` | add holiday/exam/unavailable date |
| DELETE | `/courses/{course_id}/exceptions/{exception_id}` | remove exception |

Subject fields include `course_code` (optional), `target_completion_date`,
`total_available_minutes`, `timezone`, `lecture_duration_minutes`,
`total_weeks`, and `settings`.

### Syllabus
| Method | Path | Notes |
|---|---|---|
| POST | `/courses/{course_id}/syllabus/upload` | PDF/DOCX/TXT |
| GET | `/courses/{course_id}/syllabus` | documents |
| GET | `/courses/{course_id}/syllabus/documents/{document_id}/preview` | editable extraction |
| POST | `/courses/{course_id}/syllabus/confirm` | persist reviewed preview |
| GET | `/courses/{course_id}/syllabus/structure` | confirmed units/topics |
| PATCH | `/courses/{course_id}/syllabus/topics/{topic_id}` | edit topic |
| DELETE | `/courses/{course_id}/syllabus/topics/{topic_id}` | delete/guard history |

### Schedule
| Method | Path | Notes |
|---|---|---|
| GET | `/courses/{course_id}/schedule` | current timetable; optional `revision_id` |
| POST | `/courses/{course_id}/schedule/generate` | creates a revision |
| POST | `/courses/{course_id}/schedule/recalculate` | creates a recovery revision |
| GET | `/courses/{course_id}/schedule/revisions` | paginated history |
| GET | `/courses/{course_id}/schedule/revisions/{revision_id}` | snapshot |
| GET | `/courses/{course_id}/schedule/revisions/{revision_id}/diff` | changes |
| GET | `/courses/{course_id}/schedule/revisions/{revision_id}/changes` | change rows |
| POST | `/courses/{course_id}/schedule/revisions/{revision_id}/approve` | publish |
| POST | `/courses/{course_id}/schedule/revisions/{revision_id}/reject` | reject draft |
| POST | `/courses/{course_id}/schedule/revisions/{revision_id}/rollback` | restore a prior revision |
| GET | `/courses/{course_id}/schedule/history` | audit trail |

### Lectures
| Method | Path | Notes |
|---|---|---|
| GET | `/courses/{course_id}/lectures` | paginated, current revision |
| GET | `/courses/{course_id}/lectures/{lecture_id}` | detail + per-topic progress |
| GET | `/courses/{course_id}/lectures/calendar/entries` | calendar rows |
| PATCH | `/courses/{course_id}/lectures/{lecture_id}/status` | completed/missed/cancelled |

`PATCH .../status` accepts `status`, optional `delivered_minutes`
(`[{topic_id, minutes}]`), `reason`, `recalculate`, `strategy`, and
`base_revision_id`. A recalculation produces a **pending** revision, never an
automatic publish.

### Progress and calendar
| Method | Path | Notes |
|---|---|---|
| GET | `/courses/{course_id}/dashboard` | subject dashboard |
| GET | `/courses/{course_id}/progress` | unit/topic progress |
| GET | `/courses/{course_id}/calendar` | date-range day summaries |

### Notifications
| Method | Path | Notes |
|---|---|---|
| GET | `/notifications` | paginated |
| PATCH | `/notifications/{notification_id}` | mark read |
| POST | `/notifications/read-all` | mark all read |

## Planned additions (later phases)

These are contracts, not yet implemented:

- Auth/profile: `POST /auth/logout`, `PATCH /auth/profile`,
  `POST /auth/change-password`; cookie login.
- Settings: `GET/PUT /settings`, `POST /settings/test-email`,
  `POST /settings/test-ai`, `POST /settings/weekly-summary/send-test`.
- Syllabus: `PUT /courses/{id}/syllabus` bulk save, unit CRUD, reorder,
  `POST .../analyze`, `GET .../analysis`, `POST .../analysis/approve`.
- Allocation: `POST /courses/{id}/allocation/preview`,
  `POST /courses/{id}/allocation/approve`.
- Lectures: `POST /courses/{id}/lectures/{lecture_id}/complete`,
  `.../miss`, `.../reschedule`, `POST /courses/{id}/lectures/extra`.
- Global views: `GET /lectures`, `GET /calendar`, `GET /dashboard`.
- Email: weekly summary send/test endpoints.

## Payload rules

- Durations are integer minutes everywhere. No `hours` or float fields.
- `delivered_minutes` must not exceed the allocation's planned minutes; the
  service caps and warns rather than storing an impossible value.
- Revision approval is explicit. `auto_publish_reschedules` defaults to false.
- All date-time fields are ISO 8601 with UTC offsets.
- Secrets are write-only environment configuration; API responses expose only
  booleans such as `openrouter_configured` and `smtp_configured`.
