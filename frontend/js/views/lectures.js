/** Lecture list, lecture detail, and status updates (completed / missed / cancelled). */

import { api } from "../api.js";
import {
  badge,
  bulletList,
  el,
  emptyState,
  errorState,
  field,
  formDialog,
  input,
  mount,
  notice,
  openModal,
  progressBar,
  select,
  stat,
  table,
  textarea,
  toast,
} from "../dom.js";
import { dateLabel, dateTimeLabel, minutesLabel, percentLabel, timeOfDay } from "../format.js";
import { STRATEGY_OPTIONS, defaultStrategy } from "../strategies.js";

const STATUS_BADGE = { scheduled: "accent", completed: "ok", missed: "danger", cancelled: "" };
const STATUS_FILTERS = [
  { value: "", label: "All statuses" },
  { value: "scheduled", label: "Scheduled" },
  { value: "completed", label: "Completed" },
  { value: "missed", label: "Missed" },
  { value: "cancelled", label: "Cancelled" },
];

export async function render(ctx) {
  const courseId = ctx.course.id;
  const tz = ctx.timezone;

  const statusFilter = select(STATUS_FILTERS, { value: "" });
  const fromDate = input({ type: "date" });
  const toDate = input({ type: "date" });
  const listHost = el("div", {}, el("div", { class: "loading-state", text: "Loading lectures…" }));

  const load = async () => {
    mount(listHost, el("div", { class: "loading-state", text: "Loading lectures…" }));
    try {
      const page = await api.listLectures(courseId, {
        status: statusFilter.value || undefined,
        from_date: fromDate.value || undefined,
        to_date: toDate.value || undefined,
        limit: 200,
      });
      if (!page.items.length) {
        mount(
          listHost,
          emptyState(
            "No lectures match this filter.",
            "Open the timetable",
            () => ctx.navigate(`#/courses/${courseId}/schedule`),
          ),
        );
        return;
      }
      const counts = page.items.reduce((accumulator, lecture) => {
        accumulator[lecture.status] = (accumulator[lecture.status] || 0) + 1;
        return accumulator;
      }, {});
      mount(
        listHost,
        el(
          "div",
          { class: "grid grid--stats", style: { marginBottom: "14px" } },
          stat({ label: "Lectures shown", value: page.items.length, hint: `of ${page.meta.total} total` }),
          stat({ label: "Completed", value: counts.completed || 0, kind: "ok" }),
          stat({ label: "Missed", value: counts.missed || 0, kind: counts.missed ? "danger" : "" }),
          stat({ label: "Cancelled", value: counts.cancelled || 0 }),
        ),
        table(
          [
            { label: "#", numeric: true },
            { label: "When" },
            { label: "Topics" },
            { label: "Planned", numeric: true },
            { label: "Status" },
            { label: "" },
          ],
          page.items.map((lecture) => {
            const planned = lecture.allocations.reduce(
              (total, allocation) => total + allocation.allocated_duration_minutes,
              0,
            );
            const delivered = lecture.allocations.reduce(
              (total, allocation) => total + (allocation.actual_duration_minutes || 0),
              0,
            );
            return [
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
                        text: `${allocation.topic_title} · ${minutesLabel(allocation.allocated_duration_minutes)}`,
                      }),
                    )
                  : el("span", { class: "muted", text: "No topics" }),
              ),
              delivered ? `${delivered}/${planned}` : `${planned}`,
              badge(lecture.status, STATUS_BADGE[lecture.status] ?? ""),
              el("button", {
                class: "btn btn--secondary btn--small",
                type: "button",
                text: "Open",
                onClick: () => ctx.navigate(`#/courses/${courseId}/lectures/${lecture.id}`),
              }),
            ];
          }),
          { empty: "No lectures yet." },
        ),
      );
    } catch (error) {
      mount(listHost, errorState(error));
    }
  };

  statusFilter.addEventListener("change", load);
  fromDate.addEventListener("change", load);
  toDate.addEventListener("change", load);

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Lectures" }),
        el("p", {
          text: "Mark what actually happened. The remaining plan is recalculated and waits for your approval.",
        }),
      ),
      el("button", {
        class: "btn btn--secondary",
        type: "button",
        text: "Open the timetable",
        onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
      }),
    ),
    el(
      "div",
      { class: "card card--flat" },
      el(
        "div",
        { class: "form-row" },
        field("Status", statusFilter),
        field("From", fromDate),
        field("To", toDate),
      ),
    ),
    listHost,
  );

  await load();
}

export async function renderDetail(ctx) {
  const courseId = ctx.course.id;
  const tz = ctx.timezone;
  const detail = await api.getLecture(courseId, ctx.params.lectureId);
  const lecture = detail.lecture;
  const planned = lecture.allocations.reduce(
    (total, allocation) => total + allocation.allocated_duration_minutes,
    0,
  );
  const delivered = lecture.allocations.reduce(
    (total, allocation) => total + (allocation.actual_duration_minutes || 0),
    0,
  );

  const topicProgress = detail.topic_progress || [];
  const progressById = new Map(topicProgress.map((item) => [String(item.topic_id), item]));

  const canEdit = detail.revision_status === "approved" || detail.revision_status === "pending_approval";

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: `Lecture ${lecture.sequence}` }),
        el("p", {
          text: `${dateTimeLabel(lecture.scheduled_start, tz)} → ${timeOfDay(lecture.scheduled_end, tz)} · ${
            ctx.course.name
          }`,
        }),
      ),
      el(
        "div",
        { class: "row" },
        badge(lecture.status, STATUS_BADGE[lecture.status] ?? ""),
        el("button", {
          class: "btn btn--secondary",
          type: "button",
          text: "All lectures",
          onClick: () => ctx.navigate(`#/courses/${courseId}/lectures`),
        }),
      ),
    ),

    el(
      "div",
      { class: "grid grid--stats" },
      stat({ label: "Status", value: lecture.status }),
      stat({ label: "Planned", value: minutesLabel(planned), hint: `Slot length ${minutesLabel(detail.max_minutes)}` }),
      stat({ label: "Delivered", value: minutesLabel(delivered) }),
      stat({ label: "Revision", value: detail.revision_number ?? "—", hint: detail.revision_status || "" }),
    ),

    lecture.status !== "scheduled"
      ? notice(
          lecture.status === "completed" ? "ok" : "warn",
          "",
          el("p", {
            text:
              lecture.cancellation_reason ||
              detail.last_change_reason ||
              "This lecture was updated. The reason is kept in the change history.",
          }),
        )
      : null,

    el(
      "div",
      { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "Topics in this lecture" })),
      table(
        [
          { label: "Topic" },
          { label: "Planned here", numeric: true },
          { label: "Delivered here", numeric: true },
          { label: "Topic total", numeric: true },
          { label: "Remaining", numeric: true },
          { label: "State" },
        ],
        lecture.allocations.map((allocation) => {
          const progress = progressById.get(String(allocation.topic_id)) || {};
          return [
            el("strong", { text: allocation.topic_title }),
            minutesLabel(allocation.allocated_duration_minutes),
            allocation.actual_duration_minutes ? minutesLabel(allocation.actual_duration_minutes) : "—",
            `${minutesLabel(progress.delivered_minutes_total || 0)} / ${minutesLabel(
              progress.estimated_minutes || allocation.allocated_duration_minutes,
            )}`,
            minutesLabel(progress.remaining_minutes ?? 0),
            badge(
              allocation.completion_status.replace("_", " "),
              allocation.completion_status === "completed"
                ? "ok"
                : allocation.completion_status === "in_progress"
                  ? "accent"
                  : allocation.completion_status === "skipped"
                    ? "danger"
                    : "",
            ),
          ];
        }),
        { empty: "This lecture has no planned topics." },
      ),
      el(
        "div",
        { class: "form-actions" },
        canEdit
          ? el("button", {
              class: "btn",
              type: "button",
              text: "Record what happened",
              onClick: () => openStatusDialog(ctx, detail),
            })
          : el("span", {
              class: "muted",
              text: "This lecture belongs to a superseded revision and can no longer be edited.",
            }),
        canEdit
          ? el("button", {
              class: "btn btn--secondary",
              type: "button",
              text: "Recalculate the remaining plan",
              onClick: () => recalculate(ctx),
            })
          : null,
      ),
    ),

    detail.last_change
      ? el("p", {
          class: "muted",
          text: `Last schedule change: ${dateTimeLabel(detail.last_change, tz)} · ${detail.last_change_reason}`,
        })
      : null,
  );
}

async function openStatusDialog(ctx, detail) {
  const courseId = ctx.course.id;
  const lecture = detail.lecture;
  const tz = ctx.timezone;

  const status = select(
    [
      { value: "completed", label: "Completed — topics were taught" },
      { value: "missed", label: "Missed — the lecture did not happen" },
      { value: "cancelled", label: "Cancelled — no teaching time" },
      { value: "scheduled", label: "Scheduled — reopen it" },
    ],
    { value: lecture.status === "scheduled" ? "completed" : lecture.status },
  );

  const minuteInputs = new Map();
  const rows = lecture.allocations.map((allocation) => {
    const control = input({
      type: "number",
      min: 0,
      max: allocation.allocated_duration_minutes,
      value: allocation.allocated_duration_minutes,
    });
    minuteInputs.set(String(allocation.topic_id), control);
    return el(
      "tr",
      {},
      el(
        "td",
        {},
        el("strong", { text: allocation.topic_title }),
        el("div", {
          class: "muted",
          text: `${minutesLabel(allocation.allocated_duration_minutes)} planned here`,
        }),
      ),
      el("td", { style: { width: "120px" } }, control),
    );
  });

  const completedBlock = el(
    "div",
    {},
    notice(
      "info",
      "How much did you actually cover?",
      el("p", {
        text: "Reduce the minutes for a topic you only partly taught. The rest stays pending and is placed in a later lecture.",
      }),
    ),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "table" },
        el("thead", {}, el("tr", {}, el("th", { text: "Topic" }), el("th", { text: "Minutes taught" }))),
        el("tbody", {}, rows),
      ),
    ),
  );

  const reason = textarea({ rows: 2, placeholder: "Why? e.g. public holiday, faculty meeting" });
  const strategy = select(STRATEGY_OPTIONS, { value: defaultStrategy(ctx.course) });
  const recalculateBox = el("input", { type: "checkbox" });
  recalculateBox.checked = lecture.status !== "completed";
  const errorBox = el("div", { hidden: true });

  const adapt = () => {
    completedBlock.hidden = status.value !== "completed";
    const needsRecalculation = status.value === "missed" || status.value === "cancelled";
    recalculateBox.checked = needsRecalculation || recalculateBox.checked;
    recalculateBox.disabled = false;
  };
  status.addEventListener("change", adapt);
  adapt();

  openModal({
    title: `Lecture ${lecture.sequence} · ${dateLabel(lecture.scheduled_start, tz)}`,
    body: el(
      "div",
      {},
      field("What happened?", status),
      completedBlock,
      field("Reason", reason, "Stored in the lecture record and the change history."),
      el(
        "label",
        { class: "field field--check" },
        recalculateBox,
        el("span", {
          text: "Recalculate the remaining lectures (a proposal is created for your approval)",
        }),
      ),
      field("Strategy", strategy),
      errorBox,
    ),
    actions: [
      { label: "Cancel", kind: "secondary" },
      {
        label: "Save and recalculate",
        onClick: async () => {
          const payload = {
            status: status.value,
            reason: reason.value || "",
            recalculate: recalculateBox.checked,
            strategy: strategy.value,
            base_revision_id: lecture.revision_id,
            delivered_minutes:
              status.value === "completed"
                ? lecture.allocations
                    .map((allocation) => ({
                      topic_id: allocation.topic_id,
                      minutes: Number(minuteInputs.get(String(allocation.topic_id)).value) || 0,
                    }))
                    .filter((item) => item.minutes > 0)
                : null,
          };
          try {
            const result = await api.setLectureStatus(courseId, lecture.id, payload);
            toast(result.message, result.requires_approval ? "info" : "ok", 9000);
            if (result.requires_approval) {
              ctx.navigate(`#/courses/${courseId}/schedule`);
            } else {
              await renderDetail(ctx);
            }
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

async function recalculate(ctx) {
  const courseId = ctx.course.id;
  const reason = textarea({ rows: 2, placeholder: "e.g. I swapped the order of two lectures" });
  const strategy = select(STRATEGY_OPTIONS, { value: defaultStrategy(ctx.course) });
  const errorBox = el("div", { hidden: true });
  openModal({
    title: "Recalculate the remaining lectures",
    body: el(
      "div",
      {},
      notice(
        "info",
        "Safe to run",
        el("p", {
          text: "Delivered topic parts stay delivered, missed lectures are replaced by new ones, and the result waits for your approval.",
        }),
      ),
      field("Strategy", strategy),
      field("Reason", reason),
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
              trigger: "manual_edit",
              reason: reason.value || "Manual recalculation",
            });
            toast(result.message || "A new proposal is ready.", "ok");
            ctx.navigate(`#/courses/${courseId}/schedule`);
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
