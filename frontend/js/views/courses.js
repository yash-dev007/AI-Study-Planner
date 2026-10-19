/** Course list and the new-course wizard. */

import { ApiError, api } from "../api.js";
import {
  badge,
  el,
  emptyState,
  errorState,
  field,
  input,
  mount,
  notice,
  select,
  stat,
  table,
  textarea,
  toast,
} from "../dom.js";
import { WEEKDAY_OPTIONS, dateLabel, minutesLabel, todayIso } from "../format.js";

const STATUS_BADGE = { draft: "", active: "ok", archived: "" };

function timeValue(value) {
  return value ? String(value).slice(0, 5) : "";
}

function withSeconds(value) {
  if (!value) return "";
  return value.length === 5 ? `${value}:00` : value;
}

export async function render(ctx) {
  const search = input({ type: "search", placeholder: "Search by name or code…" });
  const listHost = el("div", {}, el("div", { class: "loading-state", text: "Loading courses…" }));

  const load = async () => {
    mount(listHost, el("div", { class: "loading-state", text: "Loading courses…" }));
    try {
      const page = await api.listCourses({ search: search.value || undefined, limit: 50 });
      if (!page.items.length) {
        mount(
          listHost,
          emptyState(
            search.value ? "No course matches that search." : "You have no courses yet.",
            "Create a course",
            () => ctx.navigate("#/courses/new"),
          ),
        );
        return;
      }
      mount(
        listHost,
        table(
          [
            { label: "Code" },
            { label: "Course" },
            { label: "Term" },
            { label: "Status" },
            { label: "Updated" },
            { label: "" },
          ],
          page.items.map((course) => [
            el("span", { class: "mono", text: course.course_code }),
            el(
              "div",
              {},
              el("strong", { text: course.name }),
              el("div", { class: "muted", text: course.department || "" }),
            ),
            `${course.semester || "—"} ${course.academic_year || ""}`.trim(),
            badge(course.status, STATUS_BADGE[course.status] ?? ""),
            dateLabel(course.updated_at, course.timezone),
            el(
              "div",
              { class: "row-actions" },
              el("button", {
                class: "btn btn--small",
                type: "button",
                text: "Open",
                onClick: () => ctx.navigate(`#/courses/${course.id}`),
              }),
              el("button", {
                class: "btn btn--secondary btn--small",
                type: "button",
                text: "Settings",
                onClick: () => ctx.navigate(`#/courses/${course.id}/settings`),
              }),
            ),
          ]),
        ),
      );
    } catch (error) {
      mount(listHost, errorState(error));
    }
  };

  search.addEventListener("input", () => {
    window.clearTimeout(search._timer);
    search._timer = window.setTimeout(load, 250);
  });

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "My courses" }),
        el("p", { text: "Upload a syllabus, generate a timetable, and keep it current." }),
      ),
      el("button", {
        class: "btn",
        type: "button",
        text: "New course",
        onClick: () => ctx.navigate("#/courses/new"),
      }),
    ),
    el("div", { class: "card card--flat" }, el("div", { class: "form-row" }, field("Search", search))),
    listHost,
  );
  await load();
}

/* ------------------------------------------------------------------ wizard */

export async function renderWizard(ctx) {
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const state = {
    step: 1,
    availability: [
      { weekday: 0, start_time: "09:00", end_time: "11:00", room: "" },
      { weekday: 2, start_time: "09:00", end_time: "11:00", room: "" },
    ],
    exceptions: [],
    settings: {
      auto_publish_reschedules: false,
      preserve_future_lectures: true,
      combine_short_topics: true,
      min_allocation_minutes: 15,
    },
  };

  const details = {
    name: input({ required: true, placeholder: "Operating Systems" }),
    course_code: input({ required: true, placeholder: "CS301" }),
    department: input({ placeholder: "Computer Science" }),
    semester: input({ placeholder: "Fall" }),
    academic_year: input({ placeholder: "2026" }),
    timezone: input({ value: browserZone }),
    start_date: input({ type: "date", value: todayIso() }),
    end_date: input({ type: "date" }),
    lecture_duration_minutes: input({ type: "number", value: 60, min: 15, max: 300 }),
    total_weeks: input({ type: "number", value: 15, min: 1, max: 52 }),
  };

  const errorBox = el("div", { hidden: true });
  const body = el("div");
  const steps = el("ol", { class: "wizard-steps" });

  function renderSteps() {
    mount(
      steps,
      ["Course details", "Weekly teaching times", "Holidays and settings"].map((label, index) =>
        el("li", {
          text: `${index + 1}. ${label}`,
          "aria-current": state.step === index + 1 ? "step" : null,
        }),
      ),
    );
  }

  function availabilityRows() {
    const host = el("div", { class: "stack" });
    const renderRows = () => {
      mount(
        host,
        state.availability.map((rule, index) =>
          el(
            "div",
            { class: "inline-form" },
            select(WEEKDAY_OPTIONS, {
              value: rule.weekday,
              onChange: (event) => {
                state.availability[index].weekday = Number(event.target.value);
              },
            }),
            input({
              type: "time",
              value: rule.start_time,
              onChange: (event) => {
                state.availability[index].start_time = event.target.value;
              },
            }),
            input({
              type: "time",
              value: rule.end_time,
              onChange: (event) => {
                state.availability[index].end_time = event.target.value;
              },
            }),
            input({
              placeholder: "Room (optional)",
              value: rule.room,
              onChange: (event) => {
                state.availability[index].room = event.target.value;
              },
            }),
            el("button", {
              class: "btn btn--ghost btn--small",
              type: "button",
              text: "Remove",
              onClick: () => {
                state.availability.splice(index, 1);
                renderRows();
              },
            }),
          ),
        ),
      );
    };
    renderRows();
    return host;
  }

  function exceptionRows() {
    const host = el("div", { class: "stack" });
    const renderRows = () => {
      mount(
        host,
        state.exceptions.length
          ? state.exceptions.map((exception, index) =>
              el(
                "div",
                { class: "inline-form" },
                input({
                  type: "date",
                  value: exception.date,
                  onChange: (event) => {
                    state.exceptions[index].date = event.target.value;
                  },
                }),
                select(
                  [
                    { value: "holiday", label: "Holiday" },
                    { value: "exam", label: "Exam" },
                    { value: "event", label: "Event" },
                    { value: "unavailable", label: "Unavailable" },
                  ],
                  {
                    value: exception.exception_type,
                    onChange: (event) => {
                      state.exceptions[index].exception_type = event.target.value;
                    },
                  },
                ),
                input({
                  placeholder: "Description",
                  value: exception.description,
                  onChange: (event) => {
                    state.exceptions[index].description = event.target.value;
                  },
                }),
                el("button", {
                  class: "btn btn--ghost btn--small",
                  type: "button",
                  text: "Remove",
                  onClick: () => {
                    state.exceptions.splice(index, 1);
                    renderRows();
                  },
                }),
              ),
            )
          : el("p", { class: "muted", text: "No holidays or exam days yet." }),
      );
    };
    renderRows();
    return host;
  }

  function stepOne() {
    return el(
      "div",
      {},
      note(
        "These values define when lectures can happen. You can change them later; the schedule is then recalculated for your approval.",
      ),
      el(
        "div",
        { class: "form-row" },
        field("Course name", details.name),
        field("Course code", details.course_code),
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
        field("Timezone", details.timezone, "Used to display lecture times."),
      ),
      el(
        "div",
        { class: "form-row" },
        field("Lecture length (minutes)", details.lecture_duration_minutes),
        field("Total weeks", details.total_weeks),
      ),
    );
  }

  function stepTwo() {
    return el(
      "div",
      {},
      note(
        "Each window is a block of time on a weekday when you can teach. The planner splits it into lectures of the chosen length.",
      ),
      availabilityRows(),
      el(
        "div",
        { class: "form-actions" },
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "Add another window",
          onClick: () => {
            state.availability.push({ weekday: 4, start_time: "09:00", end_time: "11:00", room: "" });
            renderStepsAndBody();
          },
        }),
      ),
    );
  }

  function stepThree() {
    return el(
      "div",
      {},
      el("h2", { text: "Holidays, exams, and other non-teaching days" }),
      exceptionRows(),
      el(
        "div",
        { class: "form-actions" },
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "Add a date",
          onClick: () => {
            state.exceptions.push({
              date: todayIso(),
              exception_type: "holiday",
              description: "",
              blocks_teaching: true,
            });
            renderStepsAndBody();
          },
        }),
      ),
      el("h2", { text: "Planning settings", style: { marginTop: "18px" } }),
      el(
        "div",
        { class: "grid grid--three" },
        settingToggle(
          "Publish reschedules automatically",
          "auto_publish_reschedules",
          "Otherwise every change waits for your approval.",
        ),
        settingToggle(
          "Keep future lectures in place",
          "preserve_future_lectures",
          "Only lectures that must move are re-planned.",
        ),
        settingToggle(
          "Combine short topics",
          "combine_short_topics",
          "Two short topics may share one lecture.",
        ),
      ),
      field(
        "Minimum minutes per topic part",
        numberInput("min_allocation_minutes", 5, 120),
        "Used when a topic is split across lectures.",
      ),
    );
  }

  function settingToggle(label, key, hint) {
    const control = el("input", { type: "checkbox" });
    control.checked = Boolean(state.settings[key]);
    control.addEventListener("change", () => {
      state.settings[key] = control.checked;
    });
    return field(label, el("label", { class: "field field--check" }, control), hint);
  }

  function numberInput(key, min, max) {
    const control = input({ type: "number", value: state.settings[key], min, max });
    control.addEventListener("change", () => {
      state.settings[key] = Number(control.value);
    });
    return control;
  }

  function note(text) {
    return el("p", { class: "muted", text });
  }

  function renderStepsAndBody() {
    renderSteps();
    mount(body, state.step === 1 ? stepOne() : state.step === 2 ? stepTwo() : stepThree());
    renderActions();
  }

  function validate() {
    const problems = [];
    if (!details.name.value.trim()) problems.push("Course name is required.");
    if (!details.course_code.value.trim()) problems.push("Course code is required.");
    if (!details.start_date.value) problems.push("Semester start date is required.");
    if (!details.end_date.value) problems.push("Semester end date is required.");
    if (
      details.start_date.value &&
      details.end_date.value &&
      details.end_date.value <= details.start_date.value
    ) {
      problems.push("The semester must end after it starts.");
    }
    if (!state.availability.length) problems.push("Add at least one weekly teaching window.");
    for (const rule of state.availability) {
      if (!rule.start_time || !rule.end_time) problems.push("Every teaching window needs a start and end time.");
      else if (rule.end_time <= rule.start_time) problems.push("A teaching window must end after it starts.");
    }
    return problems;
  }

  async function submit() {
    const problems = validate();
    errorBox.hidden = true;
    if (problems.length) {
      if (problems.some((problem) => problem.includes("Semester") || problem.includes("Course"))) {
        state.step = 1;
        renderStepsAndBody();
      } else if (problems.some((problem) => problem.includes("window"))) {
        state.step = 2;
        renderStepsAndBody();
      }
      errorBox.hidden = false;
      mount(errorBox, notice("danger", "Fix these first", el("ul", {}, problems.map((item) => el("li", { text: item })))));
      return;
    }

    const payload = {
      name: details.name.value.trim(),
      course_code: details.course_code.value.trim(),
      department: details.department.value.trim(),
      semester: details.semester.value.trim(),
      academic_year: details.academic_year.value.trim(),
      timezone: details.timezone.value.trim() || "UTC",
      start_date: details.start_date.value,
      end_date: details.end_date.value,
      lecture_duration_minutes: Number(details.lecture_duration_minutes.value) || 60,
      total_weeks: Number(details.total_weeks.value) || 15,
      availability_rules: state.availability.map((rule) => ({
        weekday: Number(rule.weekday),
        start_time: withSeconds(rule.start_time),
        end_time: withSeconds(rule.end_time),
        room: rule.room || "",
        is_enabled: true,
      })),
      exceptions: state.exceptions
        .filter((exception) => exception.date)
        .map((exception) => ({
          date: exception.date,
          exception_type: exception.exception_type,
          description: exception.description || "",
          blocks_teaching: exception.exception_type !== "event",
        })),
      settings: state.settings,
    };

    try {
      const course = await api.createCourse(payload);
      toast("Course created. Next: upload the syllabus.", "ok");
      ctx.navigate(`#/courses/${course.id}/syllabus`);
    } catch (error) {
      errorBox.hidden = false;
      mount(errorBox, errorState(error));
    }
  }

  const actions = el("div", { class: "form-actions" });

  function renderActions() {
    mount(
      actions,
      state.step > 1
        ? el("button", {
            class: "btn btn--secondary",
            type: "button",
            text: "Back",
            onClick: () => {
              state.step -= 1;
              renderStepsAndBody();
            },
          })
        : el("button", {
            class: "btn btn--ghost",
            type: "button",
            text: "Cancel",
            onClick: () => ctx.navigate("#/courses"),
          }),
      state.step < 3
        ? el("button", {
            class: "btn",
            type: "button",
            text: "Continue",
            onClick: () => {
              const problems = validate();
              if (problems.length && state.step === 1) {
                errorBox.hidden = false;
                mount(errorBox, notice("danger", "Fix these first", el("ul", {}, problems.map((item) => el("li", { text: item })))));
                return;
              }
              errorBox.hidden = true;
              state.step += 1;
              renderStepsAndBody();
            },
          })
        : el("button", {
            class: "btn",
            type: "button",
            text: "Create course",
            onClick: submit,
          }),
    );
  }

  renderStepsAndBody();

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", { text: "New course" }), el("p", { text: "Three steps, then upload the syllabus." })),
    ),
    el(
      "div",
      { class: "card" },
      steps,
      body,
      errorBox,
      actions,
    ),
    el(
      "div",
      { class: "grid grid--three" },
      stat({ label: "Lectures per week", value: weeklySlots(state, details) }),
      stat({ label: "Lecture length", value: `${details.lecture_duration_minutes.value} min` }),
      stat({ label: "Semester days", value: semesterDays(details) }),
    ),
  );
}

function weeklySlots(state, details) {
  const duration = Number(details.lecture_duration_minutes.value) || 60;
  let slots = 0;
  for (const rule of state.availability) {
    const [startHour, startMinute] = String(rule.start_time || "0:0").split(":").map(Number);
    const [endHour, endMinute] = String(rule.end_time || "0:0").split(":").map(Number);
    const minutes = endHour * 60 + endMinute - (startHour * 60 + startMinute);
    if (minutes > 0) slots += Math.floor(minutes / duration);
  }
  return slots;
}

function semesterDays(details) {
  if (!details.start_date.value || !details.end_date.value) return "—";
  const start = new Date(`${details.start_date.value}T00:00:00Z`);
  const end = new Date(`${details.end_date.value}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return "—";
  return `${Math.round((end - start) / 86400000) + 1} days`;
}
