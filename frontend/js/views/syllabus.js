/** Syllabus: upload, review the extraction, confirm it, and edit the structure. */

import { ApiError, api } from "../api.js";
import {
  badge,
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
import { dateTimeLabel, minutesLabel, sanitizeText } from "../format.js";
import { defaultStrategy } from "../strategies.js";
import { openAiConfigModal } from "./ai_config.js";

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

function createProgressWidget(initialTitle = "Extracting syllabus…", isFile = true) {
  const statusLabel = el("strong", { text: initialTitle });
  const percentLabel = el("span", { class: "progress-box__percent", text: "0%" });
  const fill = el("div", { class: "progress-bar__fill", style: { width: "0%" } });
  const bar = el("div", { class: "progress-bar", role: "progressbar" }, fill);
  const detailLabel = el("div", { class: "muted", style: { fontSize: "0.82rem", marginTop: "4px" }, text: "Preparing…" });

  const stepLabels = isFile
    ? ["Upload", "Read pages", "AI analysis", "Structure"]
    : ["Submit", "Parse text", "AI analysis", "Structure"];

  const stepElements = stepLabels.map((label, idx) =>
    el("span", { class: `progress-box__step${idx === 0 ? " progress-box__step--active" : ""}`, text: `1. ${label}` })
  );
  const stepsContainer = el("div", { class: "progress-box__steps" }, ...stepElements);

  const container = el(
    "div",
    { class: "progress-box", hidden: true },
    el("div", { class: "progress-box__header" }, statusLabel, percentLabel),
    bar,
    detailLabel,
    stepsContainer,
  );

  let timer = null;
  let currentPercent = 0;

  function updateSteps(stepNumber) {
    stepElements.forEach((s, idx) => {
      s.className = "progress-box__step";
      const baseName = stepLabels[idx];
      if (idx + 1 < stepNumber) {
        s.classList.add("progress-box__step--done");
        s.textContent = `✓ ${baseName}`;
      } else if (idx + 1 === stepNumber) {
        s.classList.add("progress-box__step--active");
        s.textContent = `▶ ${baseName}`;
      } else {
        s.textContent = `${idx + 1}. ${baseName}`;
      }
    });
  }

  function setProgress(pct, statusText, detailText, kind = "") {
    currentPercent = Math.max(0, Math.min(100, Math.round(pct)));
    percentLabel.textContent = `${currentPercent}%`;
    fill.style.width = `${currentPercent}%`;
    fill.className = `progress-bar__fill${kind ? ` progress-bar__fill--${kind}` : ""}`;
    if (statusText) statusLabel.textContent = statusText;
    if (detailText) detailLabel.textContent = detailText;

    if (currentPercent < 25) {
      updateSteps(1);
    } else if (currentPercent < 50) {
      updateSteps(2);
    } else if (currentPercent < 85) {
      updateSteps(3);
    } else if (currentPercent < 100) {
      updateSteps(4);
    } else {
      updateSteps(5);
    }
  }

  function startSimulatedProgress({ startFrom = 25, isText = false } = {}) {
    stop();
    container.hidden = false;
    currentPercent = startFrom;
    setProgress(
      currentPercent,
      isText ? "Analyzing syllabus text…" : "Extracting document pages…",
      isText ? "Parsing text structure and headings…" : "Reading PDF/document content and layout…",
    );

    timer = window.setInterval(() => {
      if (currentPercent < 50) {
        currentPercent += 3;
        setProgress(
          currentPercent,
          isText ? "Parsing text outline…" : "Reading document text & pages…",
          "Extracting curriculum headings and section layout…",
        );
      } else if (currentPercent < 75) {
        currentPercent += 2;
        setProgress(
          currentPercent,
          "AI analyzing curriculum…",
          "Identifying units, topics, estimated durations, and difficulty…",
        );
      } else if (currentPercent < 90) {
        currentPercent += 1;
        setProgress(
          currentPercent,
          "Structuring syllabus hierarchy…",
          "Validating prerequisite dependencies and topic sequences…",
        );
      } else if (currentPercent < 96) {
        if (Math.random() > 0.4) {
          currentPercent += 1;
          setProgress(
            currentPercent,
            "Finalizing extraction preview…",
            "Preparing structured review table…",
          );
        }
        // The real result is 100% only once the server answers; park here
        // instead of leaving an interval running for the life of the page.
        if (currentPercent >= 96) stop();
      }
    }, 350);
  }

  function complete(message = "Extraction complete!") {
    stop();
    setProgress(100, message, "Extracted curriculum successfully. Ready for review.", "ok");
    updateSteps(5);
  }

  function fail(errorMessage = "Extraction failed.") {
    stop();
    setProgress(currentPercent, "Extraction failed", errorMessage, "danger");
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  function reset() {
    stop();
    container.hidden = true;
    setProgress(0, initialTitle, "");
    updateSteps(1);
  }

  return {
    container,
    setProgress,
    startSimulatedProgress,
    complete,
    fail,
    reset,
    getPercent: () => currentPercent,
  };
}

export async function render(ctx) {
  const courseId = ctx.course.id;
  const documentsHost = el("div", { class: "stack" });
  const previewHost = el("div", {});
  const structureHost = el("div", {}, el("div", { class: "loading-state", text: "Loading syllabus…" }));
  // Updated from GET /settings below. Defaults to the development UI shape so
  // previews rendered before settings load never reference an uninitialised value.
  let canWriteKeys = true;

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
            badge(
              document.processing_status === "extracting"
                ? "extracting in background…"
                : document.processing_status.replace("_", " "),
              DOCUMENT_BADGE[document.processing_status] ?? "",
            ),
            document.created_at ? dateTimeLabel(document.created_at, ctx.timezone) : "—",
            document.processing_status === "failed"
              ? el("span", { class: "muted", text: "Fix the file and upload again" })
              : document.processing_status === "extracting"
              ? el("span", { class: "muted", text: "Processing…" })
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

    const confirmButton = () =>
      el("button", {
        class: "btn",
        type: "button",
        text: "Confirm syllabus",
        onClick: () => confirm(units),
      });
    const discardButton = () =>
      el("button", {
        class: "btn btn--ghost",
        type: "button",
        text: "Discard",
        onClick: () => clear(previewHost),
      });

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
                      ["Topic", "Minutes", "Difficulty", "Prerequisites", ""].map(
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

    const promptInput = textarea({
      placeholder: "e.g. Split Unit 1 into two units, change lecture durations to 45 mins, add a topic on Docker to Unit 3…",
      rows: 2,
    });
    const promptError = el("div", { hidden: true });
    const refineProgress = createProgressWidget("Refining syllabus with AI…", false);
    const promptButton = el("button", {
      class: "btn btn--secondary",
      type: "button",
      text: "✨ Refine with AI Prompt",
    });

    promptButton.addEventListener("click", async () => {
      const instruction = promptInput.value.trim();
      if (!instruction) {
        toast("Please type an instruction prompt first.", "error");
        return;
      }
      promptError.hidden = true;
      refineProgress.reset();
      refineProgress.container.hidden = false;
      refineProgress.startSimulatedProgress({ startFrom: 15, isText: true });
      promptButton.disabled = true;
      promptButton.textContent = "Refining with AI… 15%";
      const btnSync = window.setInterval(() => {
        promptButton.textContent = `Refining with AI… ${refineProgress.getPercent()}%`;
      }, 200);

      try {
        const updatedPreview = await api.refinePreview(courseId, preview.document.id, {
          prompt: instruction,
          current_units: units,
        });
        window.clearInterval(btnSync);
        refineProgress.complete("Syllabus updated based on your prompt!");
        promptButton.textContent = "Refinement complete! (100%)";
        toast("Syllabus updated based on your prompt!", "ok");
        await new Promise((r) => setTimeout(r, 450));
        refineProgress.container.hidden = true;
        renderPreview(updatedPreview);
      } catch (error) {
        window.clearInterval(btnSync);
        refineProgress.fail(error.message);
        promptError.hidden = false;
        mount(
          promptError,
          notice(
            "warn",
            "AI refinement was not applied",
            el("p", { text: error.message }),
            el("button", {
              class: "btn btn--small",
              type: "button",
              style: { marginTop: "0.5rem" },
              text: "⚙️ AI settings",
              onClick: () => openAiConfigModal(),
            }),
          ),
        );
      } finally {
        window.clearInterval(btnSync);
        promptButton.disabled = false;
        promptButton.textContent = "✨ Refine with AI Prompt";
      }
    });

    const aiRefineSection = el(
      "div",
      { class: "card", style: { margin: "1rem 0" } },
      el("div", { class: "card__head" }, el("h3", { text: "🤖 Edit syllabus using AI prompts" })),
      el("p", {
        class: "muted",
        text: "Instruct AI to reorganize units, add new topics, split lectures, or adjust durations. Your current edits are preserved.",
      }),
      promptInput,
      refineProgress.container,
      promptError,
      el("div", { class: "form-actions", style: { marginTop: "0.5rem" } }, promptButton),
    );

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
        !preview.used_llm
          ? notice(
              "warn",
              "Extracted with Rule-Based Fallback (AI Not Used)",
              el("p", {
                text: canWriteKeys
                  ? "No AI API key was active during this extraction. Add a provider key in AI settings to extract topics, durations, difficulty, and prerequisites with AI."
                  : "No AI provider was used for this extraction. The server operator must configure an AI provider and you must enable AI for your account; rule-based extraction remains available.",
              }),
              el("button", {
                class: "btn btn--small",
                type: "button",
                text: "⚙️ AI settings",
                onClick: () => openAiConfigModal(() => render(ctx)),
              }),
            )
          : null,
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
          confirmButton(),
          discardButton(),
        ),
      ),
      aiRefineSection,
      tableHost,
      el(
        "div",
        { class: "card", style: { marginTop: "1rem" } },
        el("div", { class: "card__head" }, el("h3", { text: "Ready to save this syllabus?" })),
        el("p", {
          class: "muted",
          text: "Confirming stores the reviewed structure as the course syllabus and prepares a timetable proposal for your review.",
        }),
        el("div", { class: "form-actions" }, confirmButton(), discardButton()),
      ),
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
              "This course already has an approved timetable built from the current syllabus. Confirming a new structure invalidates it and prepares a new proposal for review.",
            confirmLabel: "Replace and generate proposal",
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
      mount(
        previewHost,
        el("div", { class: "loading-state", text: "Preparing a timetable proposal…" }),
      );
      await Promise.all([loadDocuments(), loadStructure()]);
      try {
        await api.generateSchedule(courseId, { strategy: defaultStrategy(ctx.course) });
        toast("Timetable proposal ready for review.", "ok");
      } catch (error) {
        // The syllabus is saved either way. The Schedule page keeps a visible
        // Generate action and explains why nothing could be placed.
        toast(
          `Syllabus confirmed, but the timetable could not be generated: ${error.message}`,
          "info",
        );
      }
      ctx.navigate(`#/courses/${courseId}/schedule`);
    } catch (error) {
      mount(previewHost, el("div", {}, errorState(error)));
      toast(error.message, "error");
    }
  }

  let editingStructure = false;
  let structureDraft = null;
  let currentStructure = null;

  function buildStructureDraft(structure) {
    return structure.units.map((unit) => ({
      id: unit.id,
      title: unit.title,
      description: unit.description || "",
      planned_minutes: unit.planned_minutes ?? null,
      topics: unit.topics.map((topic) => ({
        id: topic.id,
        title: topic.title,
        description: topic.description || "",
        learning_objectives: [...(topic.learning_objectives || [])],
        estimated_duration_minutes: topic.estimated_duration_minutes,
        difficulty: topic.difficulty,
        priority: topic.priority,
        is_splittable: Boolean(topic.is_splittable),
        needs_review: Boolean(topic.needs_review),
        prerequisite_ids: [...(topic.prerequisite_ids || [])],
      })),
    }));
  }

  function renderStructure(structure) {
    const allTopics = structure.units.flatMap((unit) => unit.topics);
    const topicTitles = new Map(allTopics.map((topic) => [topic.id, topic.title]));
    const scheduleApproved = Boolean(
      structure.schedule_impact?.has_approved_schedule || ctx.course.has_approved_schedule,
    );

    const saveButton = () =>
      el("button", {
        class: "btn",
        type: "button",
        text: "💾 Save structure",
        onClick: () => saveStructureDraft(structure),
      });
    const cancelButton = () =>
      el("button", {
        class: "btn btn--ghost",
        type: "button",
        text: "Cancel",
        onClick: () => {
          editingStructure = false;
          structureDraft = null;
          renderStructure(structure);
        },
      });

    const headerActions = editingStructure
      ? el("div", { class: "row" }, saveButton(), cancelButton())
      : el(
          "div",
          { class: "row" },
          el("button", {
            class: "btn btn--secondary btn--small",
            type: "button",
            text: "✏️ Edit structure",
            onClick: () => {
              structureDraft = buildStructureDraft(structure);
              editingStructure = true;
              renderStructure(structure);
            },
          }),
          scheduleApproved
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
        );

    const editorBody = () =>
      el(
        "div",
        { class: "stack" },
        el("p", {
          class: "muted",
          text: "Edit titles, durations, or add units and topics. Saving updates the confirmed syllabus; the timetable is not regenerated automatically.",
        }),
        structureDraft.map((unit, unitIndex) => renderUnitEditor(unit, unitIndex)),
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "＋ Add unit",
          onClick: () => {
            structureDraft.push({
              id: null,
              title: "New unit",
              description: "",
              planned_minutes: null,
              topics: [],
            });
            renderStructure(structure);
          },
        }),
        el("div", { class: "form-actions" }, saveButton(), cancelButton()),
      );

    const readBody = () =>
      el(
        "div",
        { class: "stack" },
        el("p", {
          class: "muted",
          text: "Use Edit structure to add units or topics, then save the revised syllabus.",
        }),
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
      );

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
            el("h2", { text: editingStructure ? "Edit syllabus structure" : "Confirmed structure" }),
            el("p", {
              class: "muted",
              text: `${structure.units.length} unit(s) · ${structure.topic_count} topic(s) · ${minutesLabel(
                structure.total_minutes,
              )} of teaching`,
            }),
          ),
          headerActions,
        ),
        structure.warnings?.length
          ? notice("warn", "Notes", el("ul", {}, structure.warnings.map((item) => el("li", { text: item }))))
          : null,
        editingStructure ? editorBody() : readBody(),
      ),
    );
  }

  function renderUnitEditor(unit, unitIndex) {
    return el(
      "div",
      { class: "card card--flat" },
      el(
        "div",
        { class: "form-row" },
        field(
          `Unit ${unitIndex + 1} title`,
          input({
            value: unit.title,
            onInput: (event) => {
              unit.title = event.target.value;
            },
          }),
        ),
        field(
          "Unit description",
          input({
            value: unit.description,
            onInput: (event) => {
              unit.description = event.target.value;
            },
          }),
        ),
        field(
          "Planned minutes",
          input({
            type: "number",
            min: 5,
            value: unit.planned_minutes ?? "",
            onInput: (event) => {
              unit.planned_minutes = event.target.value ? Number(event.target.value) : null;
            },
          }),
          "Optional cap for the unit.",
        ),
      ),
      table(
        [
          { label: "Topic" },
          { label: "Minutes", numeric: true },
          { label: "Difficulty" },
          { label: "Priority", numeric: true },
        ],
        unit.topics.map((topic) => [
          input({
            value: topic.title,
            onInput: (event) => {
              topic.title = event.target.value;
            },
          }),
          input({
            type: "number",
            min: 5,
            max: 600,
            value: topic.estimated_duration_minutes,
            onInput: (event) => {
              topic.estimated_duration_minutes = Number(event.target.value);
            },
          }),
          select(DIFFICULTY_OPTIONS, {
            value: topic.difficulty,
            onChange: (event) => {
              topic.difficulty = event.target.value;
            },
          }),
          input({
            type: "number",
            min: 1,
            max: 5,
            value: topic.priority,
            onInput: (event) => {
              topic.priority = Number(event.target.value);
            },
          }),
        ]),
        { empty: "This unit has no topics yet." },
      ),
      el(
        "div",
        { class: "form-actions" },
        el("button", {
          class: "btn btn--secondary btn--small",
          type: "button",
          text: "＋ Add topic",
          onClick: () => {
            unit.topics.push({
              id: null,
              title: "New topic",
              description: "",
              learning_objectives: [],
              estimated_duration_minutes: ctx.course.lecture_duration_minutes || 60,
              difficulty: "medium",
              priority: 3,
              is_splittable: true,
              needs_review: true,
              prerequisite_ids: [],
            });
            renderStructure(currentStructure);
          },
        }),
      ),
    );
  }

  async function saveStructureDraft(structure) {
    if (!structureDraft?.length) {
      toast("Add at least one unit before saving.", "error");
      return;
    }

    const units = structureDraft.map((unit) => ({
      ...(unit.id ? { id: unit.id } : {}),
      title: (unit.title || "").trim() || "Untitled unit",
      description: (unit.description || "").trim(),
      planned_minutes: unit.planned_minutes ? Number(unit.planned_minutes) : null,
      topics: unit.topics.map((topic) => ({
        ...(topic.id ? { id: topic.id } : {}),
        title: (topic.title || "").trim() || "Untitled topic",
        description: topic.description || "",
        learning_objectives: topic.learning_objectives || [],
        estimated_duration_minutes: Number(topic.estimated_duration_minutes) || 60,
        difficulty: topic.difficulty || "medium",
        priority: Number(topic.priority) || 3,
        is_splittable: Boolean(topic.is_splittable),
        needs_review: Boolean(topic.needs_review),
        ...(topic.id
          ? { prerequisite_ids: topic.prerequisite_ids || [] }
          : { prerequisite_ids: [] }),
      })),
    }));

    if (structure.schedule_impact?.has_approved_schedule) {
      const proceed = await confirmDialog({
        title: "Save the revised syllabus?",
        message:
          "This course already has an approved timetable. Saving the new structure does not regenerate it automatically; recalculate the remaining schedule when you are ready.",
        confirmLabel: "Save structure",
      });
      if (!proceed) return;
    }

    try {
      await api.saveSyllabusStructure(courseId, { units });
      toast("Syllabus structure saved.", "ok");
      editingStructure = false;
      structureDraft = null;
      await loadStructure();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadStructure() {
    mount(structureHost, el("div", { class: "loading-state", text: "Loading structure…" }));
    try {
      const structure = await api.syllabusStructure(courseId);
      currentStructure = structure;
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
      renderStructure(structure);
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
  const uploadProgress = createProgressWidget("Uploading and extracting syllabus…", true);
  const uploadError = el("div", { hidden: true });
  const uploadForm = el(
    "form",
    { class: "stack" },
    field(
      "Syllabus file",
      fileInput,
      "PDF, DOCX, or TXT up to 10 MB. Structured and analyzed with AI.",
    ),
    uploadProgress.container,
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
    button.textContent = "Extracting… 5%";
    uploadProgress.reset();
    uploadProgress.container.hidden = false;
    uploadProgress.setProgress(5, "Uploading file…", `Sending ${file.name} to server…`);

    const btnSync = window.setInterval(() => {
      button.textContent = `Extracting… ${uploadProgress.getPercent()}%`;
    }, 200);

    try {
      const preview = await api.uploadSyllabus(courseId, file, ({ loaded, total, percent }) => {
        const overall = Math.max(5, Math.round((percent / 100) * 25));
        uploadProgress.setProgress(
          overall,
          "Uploading file…",
          `Uploaded ${Math.round(loaded / 1024)} KB of ${Math.round(total / 1024)} KB (${percent}%)`,
        );
        if (percent >= 100) {
          uploadProgress.startSimulatedProgress({ startFrom: 25, isText: false });
        }
      });
      window.clearInterval(btnSync);
      uploadProgress.complete(`Extracted ${preview.stats.topic_count} topic(s)!`);
      button.textContent = "Extraction complete! (100%)";
      toast(`Extracted ${preview.stats.topic_count} topic(s) from ${preview.document.filename}.`, "ok");
      uploadForm.reset();
      await new Promise((r) => setTimeout(r, 450));
      uploadProgress.container.hidden = true;
      renderPreview(preview);
      await loadDocuments();
    } catch (error) {
      window.clearInterval(btnSync);
      uploadProgress.fail(error.message);
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
      window.clearInterval(btnSync);
      button.disabled = false;
      button.textContent = "Upload and extract";
    }
  });

  const textTitleInput = input({ placeholder: "e.g. CS101 Syllabus", value: "Pasted Syllabus" });
  const textContentInput = textarea({
    placeholder: "Paste syllabus outline, units, topics, and lecture details here…",
    rows: 6,
  });
  const textProgress = createProgressWidget("Extracting pasted text…", false);
  const textError = el("div", { hidden: true });
  const textForm = el(
    "form",
    { class: "stack" },
    field("Document title", textTitleInput),
    field("Syllabus text", textContentInput, "Paste text directly from a document or course outline."),
    textProgress.container,
    textError,
    el("button", { class: "btn", type: "submit", text: "Extract from text" }),
  );

  textForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    textError.hidden = true;
    const text = textContentInput.value.trim();
    if (!text || text.length < 10) {
      textError.hidden = false;
      mount(textError, notice("warn", "Please enter at least a few lines of syllabus text."));
      return;
    }
    const button = textForm.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Extracting… 10%";
    textProgress.reset();
    textProgress.container.hidden = false;
    textProgress.startSimulatedProgress({ startFrom: 10, isText: true });

    const btnSync = window.setInterval(() => {
      button.textContent = `Extracting… ${textProgress.getPercent()}%`;
    }, 200);

    try {
      const preview = await api.uploadTextSyllabus(courseId, {
        title: textTitleInput.value.trim() || "Pasted Syllabus",
        text,
      });
      window.clearInterval(btnSync);
      textProgress.complete(`Extracted ${preview.stats.topic_count} topic(s)!`);
      button.textContent = "Extraction complete! (100%)";
      toast(`Extracted ${preview.stats.topic_count} topic(s).`, "ok");
      textContentInput.value = "";
      await new Promise((r) => setTimeout(r, 450));
      textProgress.container.hidden = true;
      renderPreview(preview);
      await loadDocuments();
    } catch (error) {
      window.clearInterval(btnSync);
      textProgress.fail(error.message);
      textError.hidden = false;
      mount(
        textError,
        notice(
          "danger",
          "The text could not be processed.",
          el("p", { text: `${error.message}${error.code ? ` (${error.code})` : ""}` }),
        ),
      );
    } finally {
      window.clearInterval(btnSync);
      button.disabled = false;
      button.textContent = "Extract from text";
    }
  });

  const tabUpload = el("button", {
    class: "btn btn--small",
    type: "button",
    text: "Upload file",
  });
  const tabText = el("button", {
    class: "btn btn--ghost btn--small",
    type: "button",
    text: "Paste text",
  });
  const uploadContainer = el("div", {}, uploadForm);
  const textContainer = el("div", { hidden: true }, textForm);

  tabUpload.addEventListener("click", () => {
    tabUpload.className = "btn btn--small";
    tabText.className = "btn btn--ghost btn--small";
    uploadContainer.hidden = false;
    textContainer.hidden = true;
  });

  tabText.addEventListener("click", () => {
    tabText.className = "btn btn--small";
    tabUpload.className = "btn btn--ghost btn--small";
    uploadContainer.hidden = true;
    textContainer.hidden = false;
  });

  const userSettings = await api.getSettings().catch(() => ({}));
  canWriteKeys = userSettings.ai_config_writable !== false;
  const aiStatusNotice = !userSettings.llm_enabled
    ? notice(
        "warn",
        "AI Syllabus Extraction is Not Configured",
        el("p", {
          text: canWriteKeys
            ? "OpenRouter allows intelligent syllabus analysis: units, topics, estimated durations, difficulty levels, and prerequisites are extracted accurately with AI. Without an API key, rule-based fallback is used."
            : "This server has no AI provider key. Ask the deployment operator to set LLM_PROVIDER and OPENROUTER_API_KEY or GEMINI_API_KEY in the server environment, then restart the API. Rule-based extraction is always available.",
        }),
        el("button", {
          class: "btn btn--small",
          type: "button",
          style: { marginTop: "0.5rem" },
          text: canWriteKeys ? "⚙️ Setup AI Key" : "⚙️ AI settings",
          onClick: () => openAiConfigModal(() => render(ctx)),
        }),
      )
    : !userSettings.ai_enabled
    ? notice(
        "info",
        "AI Is Available but Disabled for Your Account",
        el("p", {
          text: "The server has an AI provider configured. Enable AI for your account in AI settings; the provider key stays on the server.",
        }),
        el("button", {
          class: "btn btn--small",
          type: "button",
          style: { marginTop: "0.5rem" },
          text: "⚙️ Enable AI",
          onClick: () => openAiConfigModal(() => render(ctx)),
        }),
      )
    : null;

  const aiBadge = userSettings.llm_enabled
    ? el(
        "div",
        { class: "row", style: { alignItems: "center", gap: "0.5rem", marginTop: "0.5rem" } },
        badge(
          userSettings.ai_enabled
            ? `AI Enabled: ${
                userSettings.openrouter_configured
                  ? `OpenRouter (${userSettings.openrouter_model})`
                  : userSettings.gemini_configured
                  ? `Gemini (${userSettings.gemini_model})`
                  : "Ollama"
              }`
            : "AI available · not enabled for your account",
          userSettings.ai_enabled ? "ok" : "warn",
        ),
        el("button", {
          class: "btn btn--ghost btn--small",
          type: "button",
          text: "AI Settings",
          onClick: () => openAiConfigModal(() => render(ctx)),
        }),
      )
    : null;

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
          text: "Upload or paste the syllabus, check what was extracted, and confirm it before any schedule is built.",
        }),
        aiBadge,
      ),
    ),
    aiStatusNotice,
    el(
      "div",
      { class: "grid grid--two" },
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card__head" },
          el("h2", { text: "Import syllabus" }),
          el("div", { class: "row" }, tabUpload, tabText),
        ),
        uploadContainer,
        textContainer,
      ),
      el("div", { class: "card" }, el("h2", { text: "Documents" }), documentsHost),
    ),
    previewHost,
    structureHost,
  );

  await Promise.all([loadDocuments(), loadStructure()]);
}
