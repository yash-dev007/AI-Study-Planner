/** Timetable: generate, review a proposed change, approve or reject it. */

import { api } from "../api.js";
import {
  badge,
  bulletList,
  el,
  emptyState,
  errorState,
  field,
  formDialog,
  mount,
  notice,
  openModal,
  select,
  stat,
  table,
  textarea,
  toast,
} from "../dom.js";
import { STRATEGY_OPTIONS, defaultStrategy } from "../strategies.js";
import { dateLabel, dateOnlyLabel, dateTimeLabel, minutesLabel, percentLabel, timeOfDay } from "../format.js";

const STATUS_BADGE = { scheduled: "accent", completed: "ok", missed: "danger", cancelled: "" };

export async function render(ctx) {
  const courseId = ctx.course.id;
  const tz = ctx.timezone;
  const schedule = await api.getSchedule(courseId);
  const revision = schedule.revision;

  const blocks = [
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Schedule" }),
        el("p", {
          text: revision
            ? `Revision ${revision.revision_number} · ${revision.approval_status.replace("_", " ")}${
                revision.published_at ? " · published" : ""
              }`
            : "No timetable yet.",
        }),
      ),
      el(
        "div",
        { class: "row" },
        el("button", {
          class: "btn btn--secondary",
          type: "button",
          text: "Lecture list",
          onClick: () => ctx.navigate(`#/courses/${courseId}/lectures`),
        }),
        el("button", {
          class: "btn btn--secondary",
          type: "button",
          text: "History",
          onClick: () => ctx.navigate(`#/courses/${courseId}/history`),
        }),
        revision
          ? el("button", {
              class: "btn",
              type: "button",
              text: "Recalculate",
              onClick: () => recalculate(),
            })
          : el("button", {
              class: "btn",
              type: "button",
              text: "Generate schedule",
              onClick: () => generate(),
            }),
      ),
    ),
  ];

  if (!revision) {
    blocks.push(
      emptyState(
        "This course has no timetable yet. Generate one from the confirmed syllabus.",
        "Generate schedule",
        () => generate(),
      ),
    );
    mount(ctx.root, blocks);
    return;
  }

  const capacity = schedule.capacity || {};
  blocks.push(
    el(
      "div",
      { class: "grid grid--stats" },
      stat({
        label: "Lectures",
        value: schedule.lectures.length,
        hint: `${capacity.sessions_used ?? 0} of ${capacity.sessions_available ?? 0} slots used`,
      }),
      stat({
        label: "Scheduled",
        value: minutesLabel(capacity.scheduled_minutes ?? 0),
        hint: `${minutesLabel(capacity.completed_minutes ?? 0)} already delivered`,
      }),
      stat({
        label: "Coverage",
        value: percentLabel(capacity.coverage_percent ?? 0),
        hint: `Projected ${percentLabel(capacity.projected_coverage_percent ?? 0)}`,
        kind: (capacity.coverage_percent ?? 0) >= 99 ? "ok" : "",
      }),
      stat({
        label: "Expected completion",
        value: capacity.expected_completion ? dateLabel(capacity.expected_completion, tz) : "—",
      }),
      stat({
        label: "Extra sessions needed",
        value: capacity.additional_sessions_required ?? 0,
        kind: (capacity.additional_sessions_required ?? 0) > 0 ? "danger" : "ok",
        hint: capacity.is_feasible ? "The remaining plan fits" : "The plan does not fit the calendar",
      }),
    ),
  );

  if (revision.approval_status === "pending_approval") {
    blocks.push(await renderProposal(ctx, courseId, revision, schedule));
  } else if (revision.approval_status === "approved") {
    blocks.push(
      notice(
        "ok",
        `Revision ${revision.revision_number} is the current timetable`,
        el("p", {
          text: revision.published_at
            ? `Published ${dateTimeLabel(revision.published_at, tz)}.`
            : "Approved; notifications were sent only after your approval.",
        }),
      ),
    );
  }

  if (revision.warnings?.length) {
    blocks.push(notice("warn", "Planner notes", bulletList(revision.warnings)));
  }

  if (schedule.unplaced_topics?.length) {
    blocks.push(
      notice(
        "danger",
        `${schedule.unplaced_topics.length} topic(s) could not be placed`,
        el("p", {
          text: "Add teaching windows, extend the semester, or reduce topic durations. Nothing was dropped silently.",
        }),
        table(
          [
            { label: "Topic" },
            { label: "Remaining", numeric: true },
            { label: "Why" },
          ],
          schedule.unplaced_topics.map((topic) => [
            el("strong", { text: topic.title }),
            minutesLabel(topic.remaining_minutes),
            topic.reason,
          ]),
        ),
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "Open availability settings",
          onClick: () => ctx.navigate(`#/courses/${courseId}/settings`),
        }),
      ),
    );
  }

  if (schedule.validation_issues?.length) {
    blocks.push(
      notice(
        "warn",
        "Validation notes",
        el("ul", {}, schedule.validation_issues.map((issue) => el("li", { text: `${issue.code}: ${issue.message}` }))),
      ),
    );
  }

  blocks.push(
    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Lecture plan" }),
        el("span", { class: "muted", text: `Revision ${revision.revision_number}` }),
      ),
      table(
        [
          { label: "#", numeric: true },
          { label: "When" },
          { label: "Topics" },
          { label: "Status" },
          { label: "" },
        ],
        schedule.lectures.map((lecture) => [
          String(lecture.sequence),
          el(
            "div",
            {},
            el("strong", { text: dateLabel(lecture.scheduled_start, tz) }),
            el("div", {
              class: "muted",
              text: `${timeOfDay(lecture.scheduled_start, tz)}–${timeOfDay(lecture.scheduled_end, tz)}`,
            }),
          ),
          el(
            "div",
            {},
            lecture.allocations.length
              ? lecture.allocations.map((allocation) =>
                  el("div", {
                    text: `${allocation.topic_title} · ${minutesLabel(allocation.planned_minutes)}${
                      allocation.completion_status !== "planned"
                        ? ` (${allocation.completion_status.replace("_", " ")})`
                        : ""
                    }`,
                  }),
                )
              : el("span", { class: "muted", text: "No topics" }),
          ),
          badge(lecture.status, STATUS_BADGE[lecture.status] ?? ""),
          el("button", {
            class: "btn btn--secondary btn--small",
            type: "button",
            text: "Open",
            onClick: () => ctx.navigate(`#/courses/${courseId}/lectures/${lecture.id}`),
          }),
        ]),
        { empty: "No lectures in this revision." },
      ),
    ),
  );

  mount(ctx.root, blocks);

  async function generate() {
    const strategy = select(STRATEGY_OPTIONS, { value: defaultStrategy(ctx.course) });
    const errorBox = el("div", { hidden: true });
    const dialog = openModal({
      title: "Generate the timetable",
      body: el(
        "div",
        {},
        el("p", {
          text: "Topics are placed in your teaching windows, holidays and exams are respected, and prerequisites stay in order.",
        }),
        field("Planning strategy", strategy),
        errorBox,
      ),
      actions: [
        { label: "Cancel", kind: "secondary" },
        {
          label: "Generate",
          onClick: async () => {
            try {
              const result = await api.generateSchedule(courseId, {
                strategy: strategy.value,
              });
              toast(result.message || "Schedule generated.", "ok");
              await render(ctx);
            } catch (error) {
              errorBox.hidden = false;
              mount(errorBox, errorState(error));
              return false;
            }
          },
        },
      ],
    });
    return dialog;
  }

  async function recalculate() {
    const strategy = select(STRATEGY_OPTIONS, { value: defaultStrategy(ctx.course) });
    const reason = textarea({
      value: "",
      rows: 2,
      placeholder: "e.g. Two lectures were lost to a campus closure",
    });
    const errorBox = el("div", { hidden: true });
    openModal({
      title: "Recalculate the remaining schedule",
      body: el(
        "div",
        {},
        notice(
          "info",
          "What is kept",
          el("p", {
            text: "Completed, missed, and cancelled lectures never change. Topic parts already delivered are not repeated.",
          }),
        ),
        field("Strategy", strategy),
        field("Reason (shown in the history)", reason),
        errorBox,
      ),
      actions: [
        { label: "Cancel", kind: "secondary" },
        {
          label: "Recalculate",
          onClick: async () => {
            try {
              const result = await api.recalculate(courseId, {
                strategy: strategy.value,
                trigger: "course_config_changed",
                reason: reason.value || "Manual recalculation",
              });
              toast(result.message || "A new proposal is ready.", "ok");
              await render(ctx);
            } catch (error) {
              errorBox.hidden = false;
              mount(errorBox, errorState(error));
              return false;
            }
          },
        },
      ],
    });
  }
}

async function renderProposal(ctx, courseId, revision, schedule) {
  const host = el("div", { class: "card" });
  mount(host, el("div", { class: "loading-state", text: "Loading the comparison…" }));

  let diff = null;
  try {
    diff = await api.revisionDiff(courseId, revision.id);
  } catch (error) {
    mount(host, errorState(error));
    return host;
  }

  const changes = diff.diff?.changes || [];
  const changesHost = changes.length
    ? table(
        [
          { label: "Change" },
          { label: "Topic" },
          { label: "Before" },
          { label: "After" },
          { label: "Why" },
        ],
        changes.map((change) => [
          badge(change.change_type.replace("_", " "), "accent"),
          change.topic_title || "—",
          change.previous_date
            ? `${dateLabel(change.previous_date, ctx.timezone)} · ${minutesLabel(change.previous_minutes ?? 0)}`
            : "—",
          change.new_date
            ? `${dateLabel(change.new_date, ctx.timezone)} · ${minutesLabel(change.new_minutes ?? 0)}`
            : "—",
          change.change_reason,
        ]),
      )
    : el("p", { class: "muted", text: "No lecture dates change." });

  const note = textarea({ placeholder: "Optional note for the history", rows: 2 });
  const publish = el("input", { type: "checkbox" });
  publish.checked = true;

  const approve = async () => {
    try {
      await api.approveRevision(courseId, revision.id, {
        note: note.value || "",
        publish: publish.checked,
      });
      toast("Revision approved and published.", "ok");
      await ctx.refreshNotificationBadge();
      await render(ctx);
    } catch (error) {
      toast(error.message, "error");
    }
  };

  const reject = async () => {
    const values = await formDialog({
      title: "Reject this proposal",
      message: "The current timetable stays in place. The reason is kept in the history.",
      fields: [{ name: "note", label: "Reason", type: "textarea" }],
      confirmLabel: "Reject proposal",
      danger: true,
    });
    if (!values) return;
    try {
      await api.rejectRevision(courseId, revision.id, { note: values.note || "" });
      toast("Proposal rejected.", "info");
      await render(ctx);
    } catch (error) {
      toast(error.message, "error");
    }
  };

  mount(
    host,
    el(
      "div",
      { class: "card__head" },
      el(
        "div",
        {},
        el("h2", { text: `Proposed change · revision ${revision.revision_number}` }),
        el("p", { class: "muted", text: diff.diff?.summary || revision.change_summary }),
      ),
      badge("awaiting approval", "warn"),
    ),
    el(
      "div",
      { class: "grid grid--stats" },
      stat({ label: "Added", value: diff.diff?.added_count ?? 0 }),
      stat({ label: "Moved", value: diff.diff?.moved_count ?? 0 }),
      stat({ label: "Removed", value: diff.diff?.removed_count ?? 0 }),
      stat({
        label: "Trigger",
        value: revision.trigger_type.replaceAll("_", " "),
        hint: dateTimeLabel(revision.created_at, ctx.timezone),
      }),
    ),
    notice(
      "info",
      "Nothing is published yet",
      el("p", {
        text: "Students and notifications only see this change after you approve it.",
      }),
    ),
    changesHost,
    el("div", { class: "form-row" }, field("Note", note)),
    el(
      "label",
      { class: "field field--check" },
      publish,
      el("span", { text: "Send the approval notification once published" }),
    ),
    el(
      "div",
      { class: "form-actions" },
      el("button", { class: "btn", type: "button", text: "Approve", onClick: approve }),
      el("button", { class: "btn btn--secondary", type: "button", text: "Reject", onClick: reject }),
      el("button", {
        class: "btn btn--ghost",
        type: "button",
        text: "See all changes",
        onClick: async () => {
          const ledger = await api.revisionChanges(courseId, revision.id);
          showLedger(ctx, revision, ledger);
        },
      }),
    ),
  );
  return host;
}

function showLedger(ctx, revision, ledger) {
  const entries = Array.isArray(ledger) ? ledger : ledger.items || [];
  openModal({
    title: `Change ledger · revision ${revision.revision_number}`,
    body: entries.length
      ? el(
          "div",
          { class: "stack" },
          entries.map((entry) =>
            el(
              "div",
              { class: "notice notice--info" },
              el("div", { class: "notice__title", text: `${entry.change_type.replace("_", " ")}` }),
              el("div", {
                text: `${entry.topic_id ? `Topic ${entry.topic_id.slice(0, 8)} · ` : ""}${
                  entry.previous_date ? `${dateOnlyLabel(entry.previous_date)} → ` : ""
                }${entry.new_date ? dateOnlyLabel(entry.new_date) : ""}`,
              }),
              el("div", { class: "timeline__meta", text: entry.change_reason }),
            ),
          ),
        )
      : el("p", { class: "muted", text: "No line-level changes were recorded." }),
    actions: [{ label: "Close", kind: "secondary" }],
  });
}
