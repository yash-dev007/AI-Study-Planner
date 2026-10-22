/** Dashboard: coverage, lectures, warnings, at-risk topics, semester calendar. */

import { api } from "../api.js";
import {
  badge,
  bulletList,
  el,
  emptyState,
  errorState,
  mount,
  notice,
  progressBar,
  stat,
  table,
} from "../dom.js";
import {
  dateLabel,
  dateOnlyLabel,
  minutesLabel,
  percentLabel,
  relativeDays,
  shortDateOnly,
  timeOfDay,
  weekdayIndex,
  weekdayName,
} from "../format.js";

const STATE_BADGE = {
  completed: "ok",
  in_progress: "accent",
  pending: "",
  at_risk: "warn",
  unplaced: "danger",
  unstarted: "",
};

export async function render(ctx) {
  const tz = ctx.timezone;
  const courseId = ctx.course.id;
  const [dashboard, calendar] = await Promise.all([
    api.dashboard(courseId),
    api
      .calendar(courseId, { view: "semester" })
      .catch(() => null),
  ]);

  const schedule = dashboard.schedule || {};
  const onTrackBadge = dashboard.on_track
    ? badge("On track", "ok")
    : badge("Needs attention", "warn");

  const head = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      {},
      el("h1", { text: ctx.course.name }),
      el(
        "p",
        {},
        `${ctx.course.course_code} · ${ctx.course.semester || ""} ${ctx.course.academic_year || ""}`.trim(),
        " · ",
        onTrackBadge,
      ),
    ),
    el(
      "div",
      { class: "row" },
      el("button", {
        class: "btn btn--secondary",
        type: "button",
        text: "Syllabus",
        onClick: () => ctx.navigate(`#/courses/${courseId}/syllabus`),
      }),
      el("button", {
        class: "btn",
        type: "button",
        text: schedule.has_pending_revision ? "Review pending change" : "Timetable",
        onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
      }),
    ),
  );

  const blocks = [head];

  if (schedule.has_pending_revision) {
    blocks.push(
      notice(
        "warn",
        `Revision ${schedule.pending_revision_number} is waiting for your approval`,
        el("p", {
          text: "The published timetable has not changed. Review the comparison, then approve or reject it.",
        }),
        el("button", {
          class: "btn btn--small",
          type: "button",
          text: "Review the change",
          onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
        }),
      ),
    );
  }

  if (dashboard.warnings?.length) {
    blocks.push(notice("warn", "What needs your attention", bulletList(dashboard.warnings)));
  }

  const topicsHost = el("div", { class: "grid grid--stats" });
  if (!dashboard.total_topics) {
    blocks.push(
      emptyState(
        "No confirmed syllabus yet, so there is nothing to schedule.",
        "Upload a syllabus",
        () => ctx.navigate(`#/courses/${courseId}/syllabus`),
      ),
    );
  } else {
    mount(
      topicsHost,
      stat({
        label: "Topics",
        value: dashboard.total_topics,
        hint: `${dashboard.completed_topics} completed · ${dashboard.in_progress_topics} in progress`,
      }),
      stat({
        label: "Coverage",
        value: percentLabel(dashboard.coverage_percent),
        hint: `${minutesLabel(dashboard.delivered_minutes)} of ${minutesLabel(
          dashboard.total_estimated_minutes,
        )} delivered`,
        kind: dashboard.coverage_percent >= 99 ? "ok" : "",
      }),
      stat({
        label: "Remaining workload",
        value: minutesLabel(dashboard.remaining_minutes),
        hint: `${dashboard.sessions_required_remaining} lecture(s) needed`,
      }),
      stat({
        label: "At-risk topics",
        value: dashboard.at_risk_topics + dashboard.unplaced_topics,
        hint: `${dashboard.unplaced_topics} could not be placed`,
        kind: dashboard.at_risk_topics + dashboard.unplaced_topics > 0 ? "warn" : "ok",
      }),
      stat({
        label: "Lectures",
        value: `${dashboard.completed_lectures}/${dashboard.planned_lectures}`,
        hint: `${dashboard.missed_lectures} missed · ${dashboard.cancelled_lectures} cancelled · ${
          dashboard.rescheduled_lectures
        } rescheduled`,
      }),
      stat({
        label: "Free slots left",
        value: dashboard.capacity_sessions_remaining,
        hint:
          dashboard.additional_sessions_required > 0
            ? `${dashboard.additional_sessions_required} extra session(s) needed`
            : "Enough room for the remaining plan",
        kind: dashboard.additional_sessions_required > 0 ? "danger" : "",
      }),
      stat({
        label: "Estimated completion",
        value: dashboard.estimated_completion
          ? dateLabel(dashboard.estimated_completion, tz)
          : "—",
        hint: dashboard.estimated_completion
          ? relativeDays(dashboard.estimated_completion, tz)
          : "No lectures planned yet",
      }),
      stat({
        label: "Semester ends",
        value: dashboard.semester_end ? dateOnlyLabel(dashboard.semester_end) : "—",
        hint: `${dashboard.days_remaining} day(s) remaining`,
      }),
    );
    blocks.push(el("div", { class: "grid grid--two" }, topicsHost));
  }

  if (dashboard.next_lectures?.length) {
    blocks.push(
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card__head" },
          el("h2", { text: "Next lectures" }),
          el("button", {
            class: "btn btn--ghost btn--small",
            type: "button",
            text: "All lectures",
            onClick: () => ctx.navigate(`#/courses/${courseId}/lectures`),
          }),
        ),
        el(
          "ul",
          { class: "lecture-list" },
          dashboard.next_lectures.map((lecture) =>
            el(
              "li",
              { class: `lecture-item lecture-item--${lecture.status}` },
              el(
                "div",
                {},
                el("strong", {
                  text: `${dateLabel(lecture.scheduled_start, tz)} · ${timeOfDay(
                    lecture.scheduled_start,
                    tz,
                  )}–${timeOfDay(lecture.scheduled_end, tz)}`,
                }),
                el("div", { class: "muted", text: lecture.topics.join(", ") || "No topics" }),
                el("div", {
                  class: "timeline__meta",
                  text: `Lecture ${lecture.sequence} · ${minutesLabel(lecture.minutes)} planned`,
                }),
              ),
              el(
                "div",
                { class: "row" },
                badge(lecture.status, lecture.status === "scheduled" ? "accent" : ""),
                el("button", {
                  class: "btn btn--secondary btn--small",
                  type: "button",
                  text: "Open",
                  onClick: () => ctx.navigate(`#/courses/${courseId}/lectures/${lecture.lecture_id}`),
                }),
              ),
            ),
          ),
        ),
      ),
    );
  }

  if (dashboard.units?.length) {
    blocks.push(
      el(
        "div",
        { class: "card" },
        el("div", { class: "card__head" }, el("h2", { text: "Unit coverage" })),
        el(
          "ul",
          { class: "meter-list" },
          dashboard.units.map((unit) =>
            el(
              "li",
              {},
              el(
                "div",
                { class: "meter-list__row" },
                el("span", { text: `${unit.sequence_number}. ${unit.title}` }),
                el("span", {
                  class: "muted",
                  text: `${percentLabel(unit.coverage_percent)} · ${unit.completed_topics}/${unit.topic_count} topics`,
                }),
              ),
              progressBar(
                unit.coverage_percent,
                unit.coverage_percent >= 99 ? "ok" : unit.at_risk_topics ? "warn" : "",
              ),
            ),
          ),
        ),
      ),
    );
  }

  if (dashboard.at_risk?.length) {
    blocks.push(
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card__head" },
          el("h2", { text: "Topics at risk" }),
          el("button", {
            class: "btn btn--ghost btn--small",
            type: "button",
            text: "Coverage details",
            onClick: () => ctx.navigate(`#/courses/${courseId}/progress`),
          }),
        ),
        table(
          [
            { label: "Topic" },
            { label: "Unit" },
            { label: "", numeric: true },
            { label: "State" },
            { label: "Why" },
          ],
          dashboard.at_risk.map((topic) => [
            el("strong", { text: topic.title }),
            topic.unit_title,
            percentLabel(topic.progress_percent),
            badge(topic.state.replace("_", " "), STATE_BADGE[topic.state] ?? ""),
            topic.reason || (topic.blocked_by?.length ? `Blocked by ${topic.blocked_by.join(", ")}` : "—"),
          ]),
          { empty: "Nothing at risk." },
        ),
      ),
    );
  }

  if (calendar?.days?.length) {
    blocks.push(renderCalendar(calendar, tz));
  }

  mount(ctx.root, blocks);
}

export function renderCalendar(calendar, tz) {
  const days = calendar.days;
  const first = days[0]?.date;
  const leading = first ? weekdayIndex(first) : 0;

  const cells = [];
  for (let index = 0; index < leading; index += 1) {
    cells.push(el("div", { class: "calendar__day calendar__day--empty" }));
  }
  for (const day of days) {
    const lectureDay = day.lectures > 0;
    const kind = day.is_blocked
      ? "calendar__day--blocked"
      : lectureDay
        ? day.statuses?.includes("completed")
          ? "calendar__day--done"
          : "calendar__day--lecture"
        : "";
    cells.push(
      el(
        "div",
        { class: `calendar__day ${kind}`, title: day.block_reason || "" },
        el("div", { class: "calendar__date", text: shortDateOnly(day.date) }),
        day.is_blocked
          ? el("div", { class: "calendar__note", text: day.block_reason || "No teaching" })
          : null,
        lectureDay
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
    { class: "card" },
    el(
      "div",
      { class: "card__head" },
      el("h2", { text: "Semester calendar" }),
      el("span", {
        class: "muted",
        text: `${calendar.range_start} → ${calendar.range_end}`,
      }),
    ),
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
      text: "Blue days have lectures, amber days are blocked (holiday, exam, or unavailable).",
    }),
  );
}
