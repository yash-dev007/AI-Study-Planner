/** Coverage analytics: per-topic and per-unit progress, plus week and semester calendars. */

import { api } from "../api.js";
import {
  badge,
  bulletList,
  el,
  emptyState,
  errorState,
  field,
  mount,
  notice,
  progressBar,
  select,
  stat,
  table,
} from "../dom.js";
import {
  addDays,
  dateLabel,
  daysBetween,
  isoDate,
  minutesLabel,
  percentLabel,
  shortDateOnly,
  todayIso,
  weekdayIndex,
} from "../format.js";

const STATE_BADGE = {
  completed: "ok",
  in_progress: "accent",
  pending: "",
  at_risk: "warn",
  unplaced: "danger",
  not_scheduled: "",
};

export async function render(ctx) {
  const courseId = ctx.course.id;
  const tz = ctx.timezone;
  const progress = await api.progress(courseId);
  const summary = progress.summary;

  const calendarHost = el("div", {}, el("div", { class: "loading-state", text: "Loading calendar…" }));
  const viewSelect = select(
    [
      { value: "week", label: "Week" },
      { value: "semester", label: "Whole semester" },
    ],
    { value: "week" },
  );
  let anchor = todayIso(tz);

  const loadCalendar = async () => {
    mount(calendarHost, el("div", { class: "loading-state", text: "Loading calendar…" }));
    try {
      const query = { view: viewSelect.value };
      if (viewSelect.value === "week") {
        const weekStart = addDays(anchor, -weekdayIndex(anchor));
        query.range_start = weekStart;
        query.range_end = addDays(weekStart, 6);
      }
      const calendar = await api.calendar(courseId, query);
      mount(calendarHost, renderCalendarGrid(calendar, tz));
    } catch (error) {
      mount(calendarHost, errorState(error));
    }
  };

  const nav = el(
    "div",
    { class: "row" },
    el("button", {
      class: "btn btn--secondary btn--small",
      type: "button",
      text: "← Previous",
      onClick: () => {
        anchor = addDays(anchor, viewSelect.value === "week" ? -7 : -28);
        loadCalendar();
      },
    }),
    el("button", {
      class: "btn btn--secondary btn--small",
      type: "button",
      text: "Today",
      onClick: () => {
        anchor = todayIso(tz);
        loadCalendar();
      },
    }),
    el("button", {
      class: "btn btn--secondary btn--small",
      type: "button",
      text: "Next →",
      onClick: () => {
        anchor = addDays(anchor, viewSelect.value === "week" ? 7 : 28);
        loadCalendar();
      },
    }),
    el("div", { style: { minWidth: "160px" } }, viewSelect),
  );
  viewSelect.addEventListener("change", loadCalendar);

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Progress and coverage" }),
        el("p", {
          text: `What has been taught, what is still pending, and how the calendar is being used. As of ${dateLabel(
            summary.as_of,
            tz,
          )}.`,
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
      { class: "grid grid--stats" },
      stat({
        label: "Coverage",
        value: percentLabel(summary.coverage_percent),
        hint: `${minutesLabel(summary.delivered_minutes)} delivered`,
        kind: summary.coverage_percent >= 99 ? "ok" : "",
      }),
      stat({
        label: "Remaining",
        value: minutesLabel(summary.remaining_minutes),
        hint: `${summary.sessions_required_remaining} lecture(s) needed`,
      }),
      stat({
        label: "Topics",
        value: summary.total_topics,
        hint: `${summary.completed_topics} done · ${summary.in_progress_topics} started · ${summary.pending_topics} pending`,
      }),
      stat({
        label: "Unplaced topics",
        value: summary.unplaced_topics,
        kind: summary.unplaced_topics ? "danger" : "ok",
        hint: summary.unplaced_topics ? "Add teaching time or shorten topics" : "Everything is placed",
      }),
      stat({
        label: "Estimated completion",
        value: summary.estimated_completion ? dateLabel(summary.estimated_completion, tz) : "—",
        hint: summary.estimated_completion && summary.semester_end
          ? `${Math.max(0, daysBetween(summary.semester_end, isoDate(summary.estimated_completion, tz)))} day(s) before the semester ends`
          : "",
      }),
      stat({
        label: "Missed lectures",
        value: summary.missed_lectures,
        kind: summary.missed_lectures ? "warn" : "ok",
        hint: `${summary.rescheduled_lectures} rescheduled`,
      }),
    ),

    summary.warnings?.length ? notice("warn", "Notes", bulletList(summary.warnings)) : null,

    el(
      "div",
      { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "Calendar" }), nav),
      calendarHost,
    ),

    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Units" }),
        el("span", { class: "muted", text: `${progress.units.length} unit(s)` }),
      ),
      progress.units.length
        ? el(
            "ul",
            { class: "meter-list" },
            progress.units.map((unit) =>
              el(
                "li",
                {},
                el(
                  "div",
                  { class: "meter-list__row" },
                  el("span", { text: `${unit.sequence_number}. ${unit.title}` }),
                  el("span", {
                    class: "muted",
                    text: `${percentLabel(unit.coverage_percent)} · ${unit.completed_topics}/${unit.topic_count} topics · ${minutesLabel(
                      unit.remaining_minutes,
                    )} left`,
                  }),
                ),
                progressBar(
                  unit.coverage_percent,
                  unit.coverage_percent >= 99 ? "ok" : unit.at_risk_topics ? "warn" : "",
                ),
              ),
            ),
          )
        : emptyState("No units yet. Confirm a syllabus first."),
    ),

    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Topics" }),
        el("span", { class: "muted", text: "Every topic, in teaching order" }),
      ),
      table(
        [
          { label: "Unit" },
          { label: "#", numeric: true },
          { label: "Topic" },
          { label: "Progress" },
          { label: "Delivered", numeric: true },
          { label: "Remaining", numeric: true },
          { label: "Lectures", numeric: true },
          { label: "State" },
          { label: "Note" },
        ],
        progress.topics.map((topic) => [
          `${topic.unit_sequence}. ${topic.unit_title}`,
          String(topic.sequence_number),
          el(
            "div",
            {},
            el("strong", { text: topic.title }),
            topic.needs_review ? badge("needs review", "warn") : null,
          ),
          el(
            "div",
            { style: { minWidth: "120px" } },
            progressBar(topic.progress_percent, topic.state === "completed" ? "ok" : topic.state === "at_risk" ? "warn" : ""),
            el("div", { class: "muted", text: percentLabel(topic.progress_percent) }),
          ),
          minutesLabel(topic.delivered_minutes),
          minutesLabel(topic.remaining_minutes),
          String(topic.sessions_planned),
          badge(topic.state.replace("_", " "), STATE_BADGE[topic.state] ?? ""),
          topic.blocked_by?.length
            ? `Blocked by ${topic.blocked_by.join(", ")}`
            : topic.reason || (topic.last_scheduled ? `Last on ${dateLabel(topic.last_scheduled, tz)}` : "—"),
        ]),
        { empty: "No topics yet." },
      ),
    ),
  );

  await loadCalendar();
}

function renderCalendarGrid(calendar, tz) {
  const days = calendar.days || [];
  if (!days.length) return emptyState("No calendar data in this range.");
  const leading = weekdayIndex(days[0].date);
  const cells = [];
  for (let index = 0; index < leading; index += 1) {
    cells.push(el("div", { class: "calendar__day calendar__day--empty" }));
  }
  for (const day of days) {
    const kind = day.is_blocked
      ? "calendar__day--blocked"
      : day.lectures
        ? day.statuses?.includes("completed")
          ? "calendar__day--done"
          : "calendar__day--lecture"
        : "";
    cells.push(
      el(
        "div",
        { class: `calendar__day ${kind}`, title: day.block_reason || "" },
        el("div", { class: "calendar__date", text: shortDateOnly(day.date) }),
        day.is_blocked ? el("div", { class: "calendar__note", text: day.block_reason || "No teaching" }) : null,
        day.lectures
          ? el("div", {
              class: "calendar__note",
              text: `${day.lectures} lecture(s) · ${minutesLabel(day.minutes)}`,
            })
          : null,
      ),
    );
  }
  return el(
    "div",
    {},
    el(
      "div",
      { class: "calendar" },
      ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label) =>
        el("div", { class: "calendar__head", text: label }),
      ),
      cells,
    ),
    el("p", {
      class: "muted",
      text: `${calendar.range_start} → ${calendar.range_end} · blue = lecture day, green = completed lecture, amber = blocked day.`,
    }),
  );
}
