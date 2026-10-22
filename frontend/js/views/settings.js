/** Course configuration: details, planning switches, weekly windows, and exceptions. */

import { ApiError, api } from "../api.js";
import {
  badge,
  confirmDialog,
  el,
  errorState,
  field,
  formDialog,
  input,
  mount,
  notice,
  openModal,
  select,
  stat,
  table,
  textarea,
  toast,
} from "../dom.js";
import { WEEKDAY_OPTIONS, dateOnlyLabel, todayIso, weekdayName } from "../format.js";
import { defaultStrategy } from "../strategies.js";
import { buildAiConfigCard } from "./ai_config.js";

const EXCEPTION_OPTIONS = [
  { value: "holiday", label: "Holiday" },
  { value: "exam", label: "Exam" },
  { value: "event", label: "Event" },
  { value: "unavailable", label: "Unavailable" },
];

function timeValue(value) {
  return value ? String(value).slice(0, 5) : "";
}

function withSeconds(value) {
  if (!value) return "";
  return value.length === 5 ? `${value}:00` : value;
}

export async function render(ctx) {
  const courseId = ctx.course.id;
  const course = await api.getCourse(courseId);
  const tz = course.timezone;
  const settings = course.settings || {};

  let userSettings = {};
  try {
    userSettings = await api.getSettings();
  } catch {
    userSettings = {};
  }

  /* ------------------------------------------------------------ course details */

  const details = {
    name: input({ value: course.name }),
    course_code: input({ value: course.course_code }),
    department: input({ value: course.department }),
    semester: input({ value: course.semester }),
    academic_year: input({ value: course.academic_year }),
    timezone: input({ value: course.timezone }),
    start_date: input({ type: "date", value: course.start_date }),
    end_date: input({ type: "date", value: course.end_date }),
    lecture_duration_minutes: input({
      type: "number",
      min: 15,
      max: 300,
      value: course.lecture_duration_minutes,
    }),
    total_weeks: input({ type: "number", min: 1, max: 52, value: course.total_weeks }),
    status: select(
      [
        { value: "draft", label: "Draft" },
        { value: "active", label: "Active" },
        { value: "archived", label: "Archived" },
      ],
      { value: course.status },
    ),
  };

  const switches = {
    auto_publish_reschedules: el("input", { type: "checkbox" }),
    preserve_future_lectures: el("input", { type: "checkbox" }),
    combine_short_topics: el("input", { type: "checkbox" }),
  };
  switches.auto_publish_reschedules.checked = Boolean(settings.auto_publish_reschedules);
  switches.preserve_future_lectures.checked = settings.preserve_future_lectures !== false;
  switches.combine_short_topics.checked = settings.combine_short_topics !== false;
  const minAllocation = input({
    type: "number",
    min: 5,
    max: 60,
    value: settings.min_allocation_minutes ?? 15,
  });
  const notes = textarea({ value: settings.notes || "", rows: 3 });

  const detailsError = el("div", { hidden: true });
  const detailsForm = el(
    "form",
    { class: "stack", novalidate: true },
    el(
      "div",
      { class: "form-row" },
      field("Course name", details.name),
      field("Course code", details.course_code),
      field("Status", details.status),
    ),
    el(
      "div",
      { class: "form-row" },
      field("Department", details.department),
      field("Semester", details.semester),
      field("Academic year", details.academic_year),
    ),
    el(
      "div",
      { class: "form-row" },
      field("Semester starts", details.start_date),
      field("Semester ends", details.end_date),
      field("Timezone", details.timezone),
    ),
    el(
      "div",
      { class: "form-row" },
      field("Lecture length (minutes)", details.lecture_duration_minutes),
      field("Total weeks", details.total_weeks),
    ),
    el("h3", { text: "Planning behaviour" }),
    el(
      "label",
      { class: "field field--check" },
      switches.auto_publish_reschedules,
      el("span", {
        text: "Publish reschedules automatically (no approval step). Leave off to review every change.",
      }),
    ),
    el(
      "label",
      { class: "field field--check" },
      switches.preserve_future_lectures,
      el("span", { text: "Keep future lectures in place and only move what must move." }),
    ),
    el(
      "label",
      { class: "field field--check" },
      switches.combine_short_topics,
      el("span", { text: "Let short topics share one lecture." }),
    ),
    field("Minimum minutes per topic part", minAllocation, "Used when a topic is split across lectures."),
    field("Notes", notes, "Private notes for this course."),
    detailsError,
    el(
      "div",
      { class: "form-actions" },
      el("button", { class: "btn", type: "submit", text: "Save configuration" }),
      el("span", { class: "muted", text: `Configuration version ${course.version}` }),
    ),
  );

  detailsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    detailsError.hidden = true;
    const button = detailsForm.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      const updated = await api.updateCourse(courseId, {
        name: details.name.value.trim(),
        course_code: details.course_code.value.trim(),
        department: details.department.value.trim(),
        semester: details.semester.value.trim(),
        academic_year: details.academic_year.value.trim(),
        timezone: details.timezone.value.trim() || "UTC",
        start_date: details.start_date.value,
        end_date: details.end_date.value,
        lecture_duration_minutes: Number(details.lecture_duration_minutes.value),
        total_weeks: Number(details.total_weeks.value),
        status: details.status.value,
        version: course.version,
        settings: {
          auto_publish_reschedules: switches.auto_publish_reschedules.checked,
          preserve_future_lectures: switches.preserve_future_lectures.checked,
          combine_short_topics: switches.combine_short_topics.checked,
          min_allocation_minutes: Number(minAllocation.value) || 15,
          notes: notes.value || "",
        },
      });
      toast("Configuration saved.", "ok");
      if (course.has_approved_schedule) {
        await offerRecalculation(ctx, "The course configuration changed.");
      } else {
        await render(ctx);
      }
      void updated;
    } catch (error) {
      detailsError.hidden = false;
      mount(
        detailsError,
        error instanceof ApiError && error.status === 409
          ? notice(
              "warn",
              "Someone changed this course while you were editing",
              el("p", {
                text: "Reload the page to get the latest version, then apply your change again. Nothing was overwritten.",
              }),
            )
          : errorState(error),
      );
    } finally {
      button.disabled = false;
      button.textContent = "Save configuration";
    }
  });

  /* -------------------------------------------------------------- availability */

  const rules = (course.availability_rules || []).map((rule) => ({
    weekday: rule.weekday,
    start_time: timeValue(rule.start_time),
    end_time: timeValue(rule.end_time),
    room: rule.room || "",
    effective_from: rule.effective_from || "",
    effective_until: rule.effective_until || "",
    is_enabled: rule.is_enabled !== false,
  }));

  const rulesHost = el("div", { class: "stack" });
  const drawRules = () => {
    mount(
      rulesHost,
      rules.length
        ? rules.map((rule, index) =>
            el(
              "div",
              { class: "inline-form" },
              select(WEEKDAY_OPTIONS, {
                value: rule.weekday,
                "aria-label": "Teaching day",
                onChange: (event) => {
                  rule.weekday = Number(event.target.value);
                },
              }),
              input({
                type: "time",
                value: rule.start_time,
                "aria-label": "Start time",
                onChange: (event) => {
                  rule.start_time = event.target.value;
                },
              }),
              input({
                type: "time",
                value: rule.end_time,
                "aria-label": "End time",
                onChange: (event) => {
                  rule.end_time = event.target.value;
                },
              }),
              input({
                placeholder: "Room",
                value: rule.room,
                "aria-label": "Room",
                onChange: (event) => {
                  rule.room = event.target.value;
                },
              }),
              el(
                "label",
                { class: "field field--check" },
                el("input", {
                  type: "checkbox",
                  checked: rule.is_enabled,
                  onChange: (event) => {
                    rule.is_enabled = event.target.checked;
                  },
                }),
                el("span", { text: "Enabled" }),
              ),
              el("button", {
                class: "btn btn--ghost btn--small",
                type: "button",
                text: "Remove",
                onClick: () => {
                  rules.splice(index, 1);
                  drawRules();
                },
              }),
            ),
          )
        : el("p", { class: "muted", text: "No teaching windows. Without at least one, nothing can be scheduled." }),
    );
  };
  drawRules();

  const availabilityError = el("div", { hidden: true });
  const saveAvailability = async () => {
    availabilityError.hidden = true;
    try {
      await api.setAvailability(
        courseId,
        rules.map((rule) => ({
          weekday: Number(rule.weekday),
          start_time: withSeconds(rule.start_time),
          end_time: withSeconds(rule.end_time),
          room: rule.room || "",
          effective_from: rule.effective_from || null,
          effective_until: rule.effective_until || null,
          is_enabled: rule.is_enabled !== false,
        })),
      );
      toast("Teaching windows saved.", "ok");
      if (course.has_approved_schedule) {
        await offerRecalculation(ctx, "The weekly teaching windows changed.");
      } else {
        await render(ctx);
      }
    } catch (error) {
      availabilityError.hidden = false;
      mount(availabilityError, errorState(error));
    }
  };

  /* ---------------------------------------------------------------- exceptions */

  const exceptionsHost = el("div", {});
  const drawExceptions = () => {
    const exceptions = course.exceptions || [];
    mount(
      exceptionsHost,
      table(
        [
          { label: "Date" },
          { label: "Type" },
          { label: "Description" },
          { label: "Blocks teaching" },
          { label: "" },
        ],
        exceptions.map((exception) => [
          dateOnlyLabel(exception.date),
          badge(exception.exception_type, exception.exception_type === "exam" ? "warn" : ""),
          exception.description || "—",
          exception.blocks_teaching ? "Yes" : "No",
          el("button", {
            class: "btn btn--ghost btn--small",
            type: "button",
            text: "Delete",
            onClick: async () => {
              const proceed = await confirmDialog({
                title: "Remove this date?",
                message: "Lectures may be planned on that day again after a recalculation.",
                confirmLabel: "Remove",
                danger: true,
              });
              if (!proceed) return;
              try {
                await api.deleteException(courseId, exception.id);
                toast("Calendar entry removed.", "ok");
                await render(ctx);
              } catch (error) {
                toast(error.message, "error");
              }
            },
          }),
        ]),
        { empty: "No holidays or exam days recorded." },
      ),
    );
  };
  drawExceptions();

  const addException = async () => {
    const values = await formDialog({
      title: "Add a non-teaching day",
      fields: [
        { name: "date", label: "Date", type: "date", value: todayIso(tz) },
        { name: "exception_type", label: "Type", type: "select", options: EXCEPTION_OPTIONS, value: "holiday" },
        { name: "description", label: "Description", type: "text" },
      ],
      confirmLabel: "Add date",
    });
    if (!values) return;
    try {
      await api.addException(courseId, {
        date: values.date,
        exception_type: values.exception_type,
        description: values.description || "",
        blocks_teaching: values.exception_type !== "event",
      });
      toast("Calendar entry added.", "ok");
      if (course.has_approved_schedule) {
        await offerRecalculation(ctx, "A non-teaching day was added.");
      } else {
        await render(ctx);
      }
    } catch (error) {
      toast(error.message, "error");
    }
  };

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Course settings" }),
        el("p", {
          text: "Anything that changes when lectures can happen needs a recalculation, which always waits for your approval.",
        }),
      ),
      el(
        "div",
        { class: "row" },
        el("button", {
          class: "btn btn--secondary",
          type: "button",
          text: "Dashboard",
          onClick: () => ctx.navigate(`#/courses/${courseId}`),
        }),
        course.has_approved_schedule
          ? el("button", {
              class: "btn",
              type: "button",
              text: "Recalculate now",
              onClick: () => recalibrate(ctx),
            })
          : null,
      ),
    ),

    course.has_approved_schedule
      ? notice(
          "info",
          "This course has an approved timetable",
          el("p", {
            text: "Saving changes to dates, teaching windows, or settings does not silently rewrite it. You are asked whether to create a new proposal.",
          }),
        )
      : null,

    el(
      "div",
      { class: "grid grid--stats" },
      stat({ label: "Weekly windows", value: course.availability_rules.length }),
      stat({ label: "Slots per week", value: course.lecture_slots_per_week }),
      stat({ label: "Exceptions", value: (course.exceptions || []).length }),
      stat({ label: "Units / topics", value: `${course.unit_count} / ${course.topic_count}` }),
    ),

    el("div", { class: "card" }, el("div", { class: "card__head" }, el("h2", { text: "Details and behaviour" })), detailsForm),
    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "🤖 AI Configuration" }),
        el("span", { class: "muted", text: "Choose the provider, model, and whether your syllabus uploads use AI." }),
      ),
      buildAiConfigCard(userSettings, () => render(ctx)),
    ),

    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Weekly teaching windows" }),
        el("span", { class: "muted", text: "Each window is split into lectures of the configured length." }),
      ),
      rulesHost,
      availabilityError,
      el(
        "div",
        { class: "form-actions" },
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "Add a window",
          onClick: () => {
            rules.push({
              weekday: 0,
              start_time: "09:00",
              end_time: "11:00",
              room: "",
              effective_from: "",
              effective_until: "",
              is_enabled: true,
            });
            drawRules();
          },
        }),
        el("button", { class: "btn", type: "button", text: "Save windows", onClick: saveAvailability }),
      ),
    ),

    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Holidays, exams, and unavailable days" }),
        el("button", { class: "btn btn--secondary btn--small", type: "button", text: "Add a date", onClick: addException }),
      ),
      exceptionsHost,
    ),

    el(
      "div",
      { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "Weekly pattern" })),
      el(
        "ul",
        { class: "pill-list" },
        (course.availability_rules || []).map((rule) =>
          el("li", {}, badge(
            `${weekdayName(rule.weekday)} ${timeValue(rule.start_time)}–${timeValue(rule.end_time)}${
              rule.room ? ` · ${rule.room}` : ""
            }`,
            rule.is_enabled === false ? "" : "accent",
          )),
        ),
      ),
    ),

    el(
      "div",
      { class: "card", style: { borderColor: "var(--danger-soft, #fca5a5)" } },
      el("div", { class: "card__head" }, el("h2", { style: { color: "var(--danger)" }, text: "Danger zone" })),
      el(
        "p",
        { class: "muted" },
        "Permanently delete this subject and all associated syllabus documents, scheduled lectures, and teaching progress records. This action cannot be reversed.",
      ),
      el(
        "div",
        { class: "form-actions", style: { marginTop: "1rem" } },
        el("button", {
          class: "btn btn--danger",
          type: "button",
          text: "Delete this subject",
          onClick: async () => {
            const confirmed = await confirmDialog({
              title: `Delete subject "${course.name}"?`,
              message: `Are you sure you want to permanently delete "${course.name}"? All units, topics, timetable slots, and lecture history will be deleted immediately.`,
              confirmLabel: "Delete subject",
            });
            if (!confirmed) return;
            try {
              await api.deleteCourse(courseId);
              toast(`Subject "${course.name}" was deleted.`, "ok");
              ctx.navigate("#/courses");
            } catch (err) {
              toast(err.message || "Failed to delete subject", "error");
            }
          },
        }),
      ),
    ),
  );
}

async function offerRecalculation(ctx, reason) {
  const proceed = await confirmDialog({
    title: "Create a new schedule proposal?",
    message: `${reason} The remaining lectures can be re-planned now. Completed lectures are never changed and nothing is published before you approve it.`,
    confirmLabel: "Recalculate",
  });
  if (!proceed) {
    await render(ctx);
    return;
  }
  await recalibrate(ctx, reason);
}

async function recalibrate(ctx, reason = "Course configuration changed") {
  const courseId = ctx.course.id;
  try {
    const result = await api.recalculate(courseId, {
      strategy: defaultStrategy(ctx.course),
      trigger: "course_config_changed",
      reason,
    });
    toast(result.message || "A new proposal is ready for review.", "ok");
    ctx.navigate(`#/courses/${courseId}/schedule`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      openModal({
        title: "Nothing was recalculated",
        body: notice(
          "warn",
          error.code,
          el("p", { text: error.message }),
          el("p", {
            text: "Typical causes: no confirmed syllabus, no teaching windows left, or the semester has already ended.",
          }),
        ),
        actions: [{ label: "Close", kind: "secondary" }],
      });
      return;
    }
    toast(error.message, "error");
  }
}

export async function renderUserSettings(ctx) {
  let userSettings = {};
  try {
    userSettings = await api.getSettings();
  } catch {
    userSettings = {};
  }
  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "AI & Teaching Settings" }),
        el("p", {
          text: "Choose whether syllabus uploads use AI, and which provider and model to prefer.",
        }),
      ),
    ),
    el(
      "div",
      { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "🤖 AI Extraction Configuration" })),
      buildAiConfigCard(userSettings, () => renderUserSettings(ctx)),
    ),
  );
}
