/** Syllabus: upload, review the extraction, confirm it, and edit the structure. */

import { ApiError, api } from "../api.js";
import {
  badge,
  checkbox,
  clear,
  confirmDialog,
  el,
  emptyState,
  errorState,
  field,
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
import { minutesLabel, sanitizeText } from "../format.js";
import { defaultStrategy } from "../strategies.js";

const DOCUMENT_BADGE = {
  uploaded: "",
  extracting: "accent",
  extracted: "ok",
  needs_review: "warn",
  failed: "danger",
};

const DIFFICULTY_OPTIONS = [
  { value: "easy", label: "Easy" },
  { value: "medium", label: "Medium" },
  { value: "hard", label: "Hard" },
];

export async function render(ctx) {
  const courseId = ctx.course.id;
  const documentsHost = el("div", { class: "stack" });
  const previewHost = el("div", {});
  const structureHost = el("div", {}, el("div", { class: "loading-state", text: "Loading syllabus…" }));

  async function loadDocuments() {
    mount(documentsHost, el("div", { class: "loading-state", text: "Loading documents…" }));
    try {
      const documents = await api.listDocuments(courseId);
      if (!documents.length) {
        mount(documentsHost, el("p", { class: "muted", text: "No syllabus uploaded yet." }));
        return;
      }
      mount(
        documentsHost,
        table(
          [
            { label: "File" },
            { label: "Status" },
            { label: "Uploaded" },
            { label: "" },
          ],
          documents.map((document) => [
            el(
              "div",
              {},
              el("strong", { text: document.filename }),
              el("div", {
                class: "muted",
                text: `${Math.max(1, Math.round(document.size_bytes / 1024))} KB · ${sanitizeText(
                  document.content_type || "unknown type",
                )}`,
              }),
              document.error_message
                ? el("div", { class: "muted", text: document.error_message })
                : null,
            ),
            badge(document.processing_status.replace("_", " "), DOCUMENT_BADGE[document.processing_status] ?? ""),
            document.created_at ? new Date(document.created_at).toLocaleString() : "—",
            document.processing_status === "failed"
              ? el("span", { class: "muted", text: "Fix the file and upload again" })
              : el("button", {
                  class: "btn btn--secondary btn--small",
                  type: "button",
                  text: "Review extraction",
                  onClick: () => openPreview(document.id),
                }),
          ]),
        ),
      );
    } catch (error) {
      mount(documentsHost, errorState(error));
    }
  }

  async function openPreview(documentId) {
    mount(previewHost, el("div", { class: "loading-state", text: "Loading extraction…" }));
    try {
      const preview = await api.extractionPreview(courseId, documentId);
      renderPreview(preview);
    } catch (error) {
      mount(previewHost, errorState(error));
    }
  }

  function renderPreview(preview) {
    // Editable working copy: the professor changes it before anything is stored.
    const units = preview.units.map((unit) => ({
      key: unit.key,
      title: unit.title,
      description: unit.description || "",
      sequence_number: unit.sequence_number,
      topics: unit.topics.map((topic) => ({ ...topic })),
    }));

    const host = el("div", {});
    const tableHost = el("div", {});

    const keyOptions = () =>
      units.flatMap((unit) =>
        unit.topics.map((topic) => ({ value: topic.key, label: `${topic.key} · ${topic.title}` })),
      );

    function prerequisiteLabel(topic) {
      const keys = topic.prerequisite_keys || [];
      if (!keys.length) return "None";
      if (keys.length <= 3) return keys.join(", ");
      return `${keys.length} selected`;
    }

    // Building a checkbox per topic for every row is quadratic: a large
    // extraction (hundreds of topics) would freeze the review screen. The
    // picker is therefore created on demand and filtered as the user types.
    function editPrerequisites(topic) {
      const selected = new Set(topic.prerequisite_keys || []);
      const search = input({ placeholder: "Filter topics…" });
      const list = el("div", {
        class: "checkbox-grid",
        style: { maxHeight: "50vh", overflow: "auto", display: "block" },
      });
      const options = keyOptions().filter((option) => option.value !== topic.key);

      const drawList = () => {
        const query = search.value.trim().toLowerCase();
        const matches = options.filter((option) => !query || option.label.toLowerCase().includes(query));
        if (!matches.length) {
          mount(list, el("p", { class: "muted", text: "No matching topics." }));
          return;
        }
        mount(
          list,
          matches.map((option) => {
            const control = el("input", { type: "checkbox" });
            control.checked = selected.has(option.value);
            control.addEventListener("change", () => {
              if (control.checked) selected.add(option.value);
              else selected.delete(option.value);
            });
            return el(
              "label",
              { class: "field field--check", title: option.label, style: { marginBottom: "4px" } },
              control,
              el("span", { text: option.label }),
            );
          }),
        );
      };

      search.addEventListener("input", drawList);
      drawList();

      openModal({
        title: "Prerequisites",
        body: el(
          "div",
          {},
          el("p", {
            class: "muted",
            text: "Select the topics that must be taught before this one.",
          }),
          field("Filter", search),
          list,
        ),
        actions: [
          {
            label: "Apply",
            onClick: () => {
              topic.prerequisite_keys = [...selected];
              draw();
            },
          },
          { label: "Cancel", kind: "secondary" },
        ],
      });
    }

    const draw = () => {
      if (!units.length) {
        mount(tableHost, emptyState("The extraction produced no units. Add one manually."));
        return;
      }
      mount(
        tableHost,
        el(
          "div",
          { class: "stack" },
          units.map((unit, unitIndex) =>
            el(
              "div",
              { class: "card card--flat" },
              el(
                "div",
                { class: "form-row" },
                field(
                  `Unit ${unitIndex + 1} title`,
                  input({
                    value: unit.title,
                    onChange: (event) => {
                      unit.title = event.target.value;
                    },
                  }),
                ),
                field(
                  "Unit description",
                  input({
                    value: unit.description,
                    onChange: (event) => {
                      unit.description = event.target.value;
                    },
                  }),
                ),
              ),
              el(
                "div",
                { class: "table-wrap" },
                el(
                  "table",
                  { class: "table topic-editor" },
                  el(
                    "thead",
                    {},
                    el(
                      "tr",
                      {},
                      ["Topic", "Minutes", "Difficulty", "Priority", "Splittable", "Prerequisites", ""].map(
                        (label) => el("th", { text: label, scope: "col" }),
                      ),
                    ),
                  ),
                  el(
                    "tbody",
                    {},
                    unit.topics.map((topic, topicIndex) =>
                      el(
                        "tr",
                        {},
                        el(
                          "td",
                          {},
                          input({
                            value: topic.title,
                            onChange: (event) => {
                              topic.title = event.target.value;
                            },
                          }),
                          topic.needs_review
                            ? badge("check this", "warn")
                            : topic.confidence < 0.6
                              ? badge("low confidence", "warn")
                              : null,
                        ),
                        el(
                          "td",
                          { style: { width: "96px" } },
                          input({
                            type: "number",
                            min: 5,
                            max: 600,
                            value: topic.estimated_duration_minutes,
                            onChange: (event) => {
                              topic.estimated_duration_minutes = Number(event.target.value);
                            },
                          }),
                        ),
                        el(
                          "td",
                          {},
                          select(DIFFICULTY_OPTIONS, {
                            value: topic.difficulty,
                            onChange: (event) => {
                              topic.difficulty = event.target.value;
                            },
                          }),
                        ),
                        el(
                          "td",
                          { style: { width: "80px" } },
                          input({
                            type: "number",
                            min: 1,
                            max: 5,
                            value: topic.priority ?? 3,
                            onChange: (event) => {
                              topic.priority = Number(event.target.value);
                            },
                          }),
                        ),
                        el("td", {}, checkbox("", {
                          checked: topic.is_splittable !== false,
                          onChange: (event) => {
                            topic.is_splittable = event.target.checked;
                          },
                        })),
                        el(
                          "td",
                          {},
                          el(
                            "div",
                            { class: "row" },
                            el("span", { class: "muted", text: prerequisiteLabel(topic) }),
                            el("button", {
                              class: "btn btn--ghost btn--small",
                              type: "button",
                              text: "Edit",
                              onClick: () => editPrerequisites(topic),
                            }),
                          ),
                        ),
                        el(
                          "td",
                          {},
                          el("button", {
                            class: "btn btn--ghost btn--small",
                            type: "button",
                            text: "Remove",
                            onClick: () => {
                              unit.topics.splice(topicIndex, 1);
                              if (!unit.topics.length) units.splice(units.indexOf(unit), 1);
                              draw();
                            },
                          }),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              el(
                "div",
                { class: "form-actions" },
                el("button", {
                  class: "btn btn--secondary btn--small",
                  type: "button",
                  text: "Add topic",
                  onClick: () => {
                    unit.topics.push({
                      key: `t${Date.now().toString(36).slice(-4)}${unit.topics.length}`,
                      title: "New topic",
                      description: "",
                      learning_objectives: [],
                      estimated_duration_minutes: ctx.course.lecture_duration_minutes || 60,
                      difficulty: "medium",
                      confidence: 1,
                      needs_review: false,
                      prerequisite_keys: [],
                      sequence_number: unit.topics.length + 1,
                    });
                    draw();
                  },
                }),
                el("button", {
                  class: "btn btn--ghost btn--small",
                  type: "button",
                  text: "Remove unit",
                  onClick: () => {
                    units.splice(unitIndex, 1);
                    draw();
                  },
                }),
              ),
            ),
          ),
        ),
      );
    };

    draw();

    const stats = preview.stats || {};
    mount(
      host,
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card__head" },
          el(
            "div",
            {},
            el("h2", { text: `Review: ${preview.document.filename}` }),
            el("p", {
              class: "muted",
              text: preview.used_llm
                ? `Structured with ${preview.provider}. Check every topic before confirming.`
                : "Structured with the deterministic rule-based extractor. Check every topic before confirming.",
            }),
          ),
          preview.requires_review ? badge("needs review", "warn") : badge("looks clean", "ok"),
        ),
        preview.warnings?.length
          ? notice("warn", "Extraction notes", el("ul", {}, preview.warnings.map((warning) => el("li", { text: warning }))))
          : null,
        el(
          "div",
          { class: "grid grid--stats" },
          stat({ label: "Units", value: stats.unit_count ?? units.length }),
          stat({ label: "Topics", value: stats.topic_count ?? 0 }),
          stat({
            label: "Teaching time",
            value: minutesLabel(stats.total_minutes ?? 0),
            hint: `Lecture slot: ${ctx.course.lecture_duration_minutes} min`,
          }),
          stat({
            label: "Needs checking",
            value: stats.needs_review_count ?? 0,
            kind: stats.needs_review_count ? "warn" : "ok",
          }),
          stat({ label: "Prerequisites", value: stats.prerequisite_count ?? 0 }),
        ),
        preview.text_excerpt
          ? el(
              "details",
              {},
              el("summary", { text: "Extracted text excerpt" }),
              el("pre", { class: "mono", style: { whiteSpace: "pre-wrap" }, text: preview.text_excerpt }),
            )
          : null,
        notice(
          "info",
          "Nothing is scheduled yet",
          el("p", {
            text: "Confirming stores this structure as the course syllabus. A schedule is only generated when you ask for one.",
          }),
        ),
        el(
          "div",
          { class: "form-actions" },
          el("button", {
            class: "btn btn--secondary",
            type: "button",
            text: "Add unit",
            onClick: () => {
              units.push({
                key: `u${Date.now().toString(36).slice(-4)}`,
                title: "New unit",
                description: "",
                sequence_number: units.length + 1,
                topics: [],
              });
              draw();
            },
          }),
          el("button", {
            class: "btn",
            type: "button",
            text: "Confirm syllabus",
            onClick: () => confirm(units),
          }),
          el("button", {
            class: "btn btn--ghost",
            type: "button",
            text: "Discard",
            onClick: () => clear(previewHost),
          }),
        ),
      ),
      tableHost,
    );
    mount(previewHost, host);
  }

  async function confirm(units) {
    const payload = {
      units: units
        .filter((unit) => unit.topics.length)
        .map((unit, index) => ({
          key: unit.key || `u${index + 1}`,
          title: unit.title?.trim() || `Unit ${index + 1}`,
          description: unit.description || "",
          sequence_number: index + 1,
          topics: unit.topics.map((topic, topicIndex) => ({
            key: topic.key,
            title: topic.title?.trim() || `Topic ${topicIndex + 1}`,
            description: topic.description || "",
            learning_objectives: topic.learning_objectives || [],
            estimated_duration_minutes: Number(topic.estimated_duration_minutes) || 60,
            difficulty: topic.difficulty || "medium",
            confidence: topic.confidence ?? 0.8,
            needs_review: Boolean(topic.needs_review),
            prerequisite_keys: topic.prerequisite_keys || [],
            sequence_number: topicIndex + 1,
          })),
        })),
      replace_existing: true,
      allow_schedule_regeneration: false,
    };

    if (!payload.units.length) {
      toast("Add at least one unit with one topic.", "error");
      return;
    }

    const send = async () => api.confirmSyllabus(courseId, payload);

    try {
      try {
        await send();
      } catch (error) {
        if (error instanceof ApiError && error.code === "SYLLABUS_REPLACES_SCHEDULE") {
          const proceed = await confirmDialog({
            title: "Replace the syllabus and invalidate the schedule?",
            message:
              "This course already has an approved timetable built from the current syllabus. Confirming a new structure means the timetable must be regenerated.",
            confirmLabel: "Replace and regenerate later",
            danger: true,
          });
          if (!proceed) return;
          payload.allow_schedule_regeneration = true;
          await send();
        } else {
          throw error;
        }
      }
      toast("Syllabus confirmed.", "ok");
      clear(previewHost);
      await Promise.all([loadDocuments(), loadStructure()]);
      ctx.navigate(`#/courses/${courseId}/schedule`);
    } catch (error) {
      mount(previewHost, el("div", {}, errorState(error)));
      toast(error.message, "error");
    }
  }

  async function loadStructure() {
    mount(structureHost, el("div", { class: "loading-state", text: "Loading structure…" }));
    try {
      const structure = await api.syllabusStructure(courseId);
      if (!structure.units.length) {
        mount(
          structureHost,
          emptyState(
            "No confirmed syllabus yet.",
            "Upload a syllabus",
            () => document.getElementById("syllabus-file")?.click(),
          ),
        );
        return;
      }

      const allTopics = structure.units.flatMap((unit) => unit.topics);
      const topicTitles = new Map(allTopics.map((topic) => [topic.id, topic.title]));

      mount(
        structureHost,
        el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "card__head" },
            el(
              "div",
              {},
              el("h2", { text: "Confirmed structure" }),
              el("p", {
                class: "muted",
                text: `${structure.units.length} unit(s) · ${structure.topic_count} topic(s) · ${minutesLabel(
                  structure.total_minutes,
                )} of teaching`,
              }),
            ),
            el(
              "div",
              { class: "row" },
              ctx.course.has_approved_schedule
                ? el("button", {
                    class: "btn btn--secondary btn--small",
                    type: "button",
                    text: "Recalculate schedule",
                    onClick: () => recalculate(),
                  })
                : null,
              el("button", {
                class: "btn btn--secondary btn--small",
                type: "button",
                text: "Generate schedule",
                onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
              }),
            ),
          ),
          structure.warnings?.length
            ? notice("warn", "Notes", el("ul", {}, structure.warnings.map((item) => el("li", { text: item }))))
            : null,
          el(
            "div",
            { class: "stack" },
            structure.units.map((unit) =>
              el(
                "div",
                {},
                el("h3", { text: `${unit.sequence_number}. ${unit.title}` }),
                unit.description ? el("p", { class: "muted", text: unit.description }) : null,
                table(
                  [
                    { label: "#" },
                    { label: "Topic" },
                    { label: "Minutes", numeric: true },
                    { label: "Difficulty" },
                    { label: "Prerequisites" },
                    { label: "" },
                  ],
                  unit.topics.map((topic) => [
                    String(topic.sequence_number),
                    el(
                      "div",
                      {},
                      el("strong", { text: topic.title }),
                      topic.needs_review ? badge("needs review", "warn") : null,
                      topic.learning_objectives?.length
                        ? el("div", { class: "muted", text: topic.learning_objectives.join(" · ") })
                        : null,
                    ),
                    `${topic.estimated_duration_minutes}`,
                    topic.difficulty,
                    topic.prerequisite_ids.length
                      ? topic.prerequisite_ids
                          .map((id) => topicTitles.get(id) || "unknown")
                          .join(", ")
                      : "—",
                    el(
                      "div",
                      { class: "row-actions" },
                      el("button", {
                        class: "btn btn--secondary btn--small",
                        type: "button",
                        text: "Edit",
                        onClick: () => editTopic(topic, allTopics),
                      }),
                      el("button", {
                        class: "btn btn--ghost btn--small",
                        type: "button",
                        text: "Delete",
                        onClick: () => removeTopic(topic),
                      }),
                    ),
                  ]),
                  { empty: "This unit has no topics." },
                ),
              ),
            ),
          ),
        ),
      );
    } catch (error) {
      mount(structureHost, errorState(error));
    }
  }

  function editTopic(topic, allTopics) {
    const title = input({ value: topic.title });
    const description = textarea({ value: topic.description || "", rows: 2 });
    const minutes = input({
      type: "number",
      min: 5,
      max: 600,
      value: topic.estimated_duration_minutes,
    });
    const difficulty = select(DIFFICULTY_OPTIONS, { value: topic.difficulty });
    const sequence = input({ type: "number", min: 1, value: topic.sequence_number });
    const priority = input({ type: "number", min: 1, max: 5, value: topic.priority });
    const objectives = textarea({
      value: (topic.learning_objectives || []).join("\n"),
      rows: 2,
    });
    const splittable = el("input", { type: "checkbox" });
    splittable.checked = topic.is_splittable;
    const review = el("input", { type: "checkbox" });
    review.checked = topic.needs_review;

    const prerequisiteControls = new Map();
    const prerequisiteGrid = el(
      "div",
      { class: "checkbox-grid" },
      allTopics
        .filter((candidate) => candidate.id !== topic.id)
        .map((candidate) => {
          const control = el("input", { type: "checkbox" });
          control.checked = topic.prerequisite_ids.includes(candidate.id);
          prerequisiteControls.set(candidate.id, control);
          return el(
            "label",
            { class: "field field--check" },
            control,
            el("span", { text: candidate.title }),
          );
        }),
    );

    const errorBox = el("div", { hidden: true });
    const dialog = openModal({
      title: "Edit topic",
      body: el(
        "div",
        {},
        field("Title", title),
        el(
          "div",
          { class: "form-row" },
          field("Minutes", minutes, "Increase it or mark the topic splittable when it needs two lectures."),
          field("Difficulty", difficulty),
          field("Order", sequence),
          field("Priority", priority, "1 is planned first."),
        ),
        field("Description", description),
        field("Learning objectives (one per line)", objectives),
        el(
          "div",
          { class: "form-row" },
          el("label", { class: "field field--check" }, splittable, el("span", { text: "Can be split across lectures" })),
          el("label", { class: "field field--check" }, review, el("span", { text: "Still needs review" })),
        ),
        field("Prerequisites (taught first)", prerequisiteGrid),
        errorBox,
      ),
      actions: [
        { label: "Cancel", kind: "secondary" },
        {
          label: "Save topic",
          onClick: async () => {
            try {
              await api.updateTopic(courseId, topic.id, {
                title: title.value.trim(),
                description: description.value,
                learning_objectives: objectives.value
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean),
                estimated_duration_minutes: Number(minutes.value),
                difficulty: difficulty.value,
                sequence_number: Number(sequence.value),
                priority: Number(priority.value),
                is_splittable: splittable.checked,
                needs_review: review.checked,
                prerequisite_ids: [...prerequisiteControls.entries()]
                  .filter(([, control]) => control.checked)
                  .map(([id]) => id),
              });
              toast("Topic updated. Recalculate the schedule to apply it.", "ok");
              await loadStructure();
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

  async function removeTopic(topic) {
    const proceed = await confirmDialog({
      title: "Delete this topic?",
      message: `"${topic.title}" will be removed from the syllabus. Topics already taught keep their history, but the remaining plan must be recalculated.`,
      confirmLabel: "Delete topic",
      danger: true,
    });
    if (!proceed) return;
    try {
      await api.deleteTopic(courseId, topic.id);
      toast("Topic deleted.", "ok");
      await loadStructure();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function recalculate() {
    const proceed = await confirmDialog({
      title: "Recalculate the remaining schedule?",
      message:
        "The remaining lectures are re-planned from the current syllabus and availability. Completed lectures are never changed, and the result waits for your approval.",
      confirmLabel: "Recalculate",
    });
    if (!proceed) return;
    try {
      await api.recalculate(courseId, {
        strategy: defaultStrategy(ctx.course),
        trigger: "course_config_changed",
        reason: "Syllabus structure edited",
      });
      toast("A new proposal is ready for review.", "ok");
      ctx.navigate(`#/courses/${courseId}/schedule`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  const fileInput = input({
    type: "file",
    accept: ".pdf,.docx,.txt,application/pdf,text/plain",
    id: "syllabus-file",
  });
  const uploadError = el("div", { hidden: true });
  const uploadForm = el(
    "form",
    { class: "stack" },
    field(
      "Syllabus file",
      fileInput,
      "PDF, DOCX, or TXT up to 10 MB. Scanned PDFs need OCR to be available on the server.",
    ),
    uploadError,
    el("button", { class: "btn", type: "submit", text: "Upload and extract" }),
  );

  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    uploadError.hidden = true;
    const file = fileInput.files?.[0];
    if (!file) {
      uploadError.hidden = false;
      mount(uploadError, notice("warn", "Choose a file first."));
      return;
    }
    const button = uploadForm.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Extracting…";
    try {
      const preview = await api.uploadSyllabus(courseId, file);
      toast(`Extracted ${preview.stats.topic_count} topic(s) from ${preview.document.filename}.`, "ok");
      uploadForm.reset();
      renderPreview(preview);
      await loadDocuments();
    } catch (error) {
      uploadError.hidden = false;
      mount(
        uploadError,
        notice(
          "danger",
          "The upload could not be processed.",
          el("p", { text: `${error.message}${error.code ? ` (${error.code})` : ""}` }),
        ),
      );
    } finally {
      button.disabled = false;
      button.textContent = "Upload and extract";
    }
  });

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Syllabus" }),
        el("p", {
          text: "Upload the syllabus, check what was extracted, and confirm it before any schedule is built.",
        }),
      ),
    ),
    el(
      "div",
      { class: "grid grid--two" },
      el("div", { class: "card" }, el("h2", { text: "Upload" }), uploadForm),
      el("div", { class: "card" }, el("h2", { text: "Documents" }), documentsHost),
    ),
    previewHost,
    structureHost,
  );

  await Promise.all([loadDocuments(), loadStructure()]);
}
