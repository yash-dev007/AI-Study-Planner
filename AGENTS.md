# Agent Instructions — LecturePlan AI

Before any change, read `docs/BUILD_CONTRACT.md` and `docs/BUILD_STATUS.md`.
They define binding invariants, decisions, gate commands, and current phase.

Rules:

- Build one phase at a time. Never start a phase on a red build.
- Keep all durations as integer minutes in the database, API, and algorithms.
- `topic_allocations` is the only source of completed minutes.
- Never delete or rewrite completed, missed, cancelled, or rescheduled history.
- Scope every query to the authenticated teacher; return `404` on ownership
  failure.
- AI is optional and advisory. Every manual workflow must work with AI disabled.
- Backend `Course` / `/api/v1/courses` is exposed as **Subject** in the UI.
- Run and record the gate commands from `docs/BUILD_CONTRACT.md` after each
  phase.
- Never read, print, or commit `.env`.
