/** Small DOM toolkit: elements, forms, tables, notices, toasts, dialogs. */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key in node && typeof value !== "object") {
      node[key] = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  append(node, children);
  return node;
}

function append(parent, children) {
  for (const child of children.flat(4)) {
    if (child === undefined || child === null || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function mount(node, ...children) {
  clear(node);
  append(node, children);
}

export function fragment(...children) {
  const box = document.createDocumentFragment();
  append(box, children);
  return box;
}

export function badge(text, kind = "") {
  return el("span", { class: `badge${kind ? ` badge--${kind}` : ""}`, text });
}

export function stat({ label, value, hint, kind = "" }) {
  return el(
    "div",
    { class: `stat${kind ? ` stat--${kind}` : ""}` },
    el("div", { class: "stat__label", text: label }),
    el("div", { class: "stat__value", text: value }),
    hint ? el("div", { class: "stat__hint", text: hint }) : null,
  );
}

export function notice(kind, title, ...body) {
  return el(
    "div",
    { class: `notice notice--${kind}`, role: kind === "danger" ? "alert" : undefined },
    title ? el("div", { class: "notice__title", text: title }) : null,
    ...body,
  );
}

export function bulletList(items) {
  return el("ul", {}, items.map((item) => el("li", { text: item })));
}

export function field(label, control, hint) {
  return el(
    "label",
    { class: "field" },
    el("span", { class: "field__label", text: label }),
    control,
    hint ? el("span", { class: "field__hint", text: hint }) : null,
  );
}

export function input({ type = "text", value = "", ...rest } = {}) {
  const node = el("input", { class: "input", type, ...rest });
  if (value !== undefined && value !== null) node.value = value;
  return node;
}

export function select(options, { value, ...rest } = {}) {
  const node = el("select", { class: "select", ...rest });
  for (const option of options) {
    const item = el("option", { value: option.value, text: option.label });
    if (String(option.value) === String(value)) item.selected = true;
    node.append(item);
  }
  return node;
}

export function textarea({ value = "", ...rest } = {}) {
  const node = el("textarea", { class: "textarea", ...rest });
  node.value = value ?? "";
  return node;
}

export function checkbox(label, { checked = false, ...rest } = {}) {
  const control = el("input", { type: "checkbox", ...rest });
  control.checked = Boolean(checked);
  return el("label", { class: "field field--check" }, control, el("span", { text: label }));
}

export function table(columns, rows, { empty = "Nothing here yet." } = {}) {
  if (!rows.length) return el("div", { class: "empty-state", text: empty });
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      { class: "table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          columns.map((column) =>
            el("th", {
              class: column.numeric ? "numeric" : "",
              text: column.label,
              scope: "col",
            }),
          ),
        ),
      ),
      el(
        "tbody",
        {},
        rows.map((row) =>
          el(
            "tr",
            {},
            row.map((cell, index) =>
              el("td", { class: columns[index]?.numeric ? "numeric" : "" }, cell),
            ),
          ),
        ),
      ),
    ),
  );
}

export function progressBar(percent, kind = "") {
  const safe = Math.max(0, Math.min(100, Number(percent) || 0));
  return el(
    "div",
    { class: "progress-bar", role: "progressbar", "aria-valuenow": safe, "aria-valuemin": 0, "aria-valuemax": 100 },
    el("div", {
      class: `progress-bar__fill${kind ? ` progress-bar__fill--${kind}` : ""}`,
      style: { width: `${safe}%` },
    }),
  );
}

export function emptyState(message, actionLabel, onAction) {
  return el(
    "div",
    { class: "empty-state" },
    el("p", { text: message }),
    actionLabel ? el("button", { class: "btn", type: "button", text: actionLabel, onClick: onAction }) : null,
  );
}

export function errorState(error) {
  return el(
    "div",
    { class: "error-state", role: "alert" },
    el("strong", { text: error?.message || "Something went wrong." }),
    error?.code && error.code !== "ERROR" ? el("div", { class: "mono", text: error.code }) : null,
  );
}

/* ------------------------------------------------------------------ toasts */

export function toast(message, kind = "info", timeout = 5000) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const node = el("div", { class: `toast toast--${kind}`, text: message });
  host.append(node);
  window.setTimeout(() => node.remove(), timeout);
}

/* ----------------------------------------------------------------- dialogs */

let modalOpener = null;

export function openModal({ title, body, actions = [], onClose }) {
  const root = document.getElementById("modal-root");
  const previousFocus = document.activeElement;
  modalOpener = previousFocus;
  clear(root);

  const close = () => {
    clear(root);
    root.hidden = true;
    if (typeof onClose === "function") onClose();
    if (modalOpener && modalOpener.isConnected) modalOpener.focus();
  };

  const dialog = el(
    "div",
    { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title || "Dialog" },
    el(
      "div",
      { class: "modal__head" },
      el("h2", { text: title || "" }),
      el("button", {
        class: "modal__close",
        type: "button",
        "aria-label": "Close",
        text: "✕",
        onClick: close,
      }),
    ),
    el("div", { class: "modal__body" }, body),
    actions.length
      ? el(
          "div",
          { class: "form-actions" },
          actions.map((action) =>
            el("button", {
              class: `btn${action.kind ? ` btn--${action.kind}` : ""}`,
              type: "button",
              text: action.label,
              onClick: async () => {
                const result = await action.onClick?.();
                if (result !== false) close();
              },
            }),
          ),
        )
      : null,
  );

  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  root.append(dialog);
  root.hidden = false;
  root.onclick = (event) => {
    if (event.target === root) close();
  };
  (dialog.querySelector("input, select, textarea, button") || dialog).focus?.();
  return { close };
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    openModal({
      title,
      body: el("p", { text: message }),
      actions: [
        { label: "Cancel", kind: "secondary", onClick: () => resolve(false) },
        {
          label: confirmLabel,
          kind: danger ? "danger" : "",
          onClick: () => resolve(true),
        },
      ],
      onClose: () => resolve(false),
    });
  });
}

/** A small form dialog; resolves with the field values or null when cancelled. */
export function formDialog({ title, message, fields, confirmLabel = "Save", danger = false }) {
  return new Promise((resolve) => {
    const controls = new Map();
    const body = el(
      "div",
      {},
      message ? el("p", { text: message }) : null,
      fields.map((definition) => {
        let control;
        if (definition.type === "select") {
          control = select(definition.options, { value: definition.value });
        } else if (definition.type === "textarea") {
          control = textarea({ value: definition.value ?? "", rows: 3 });
        } else if (definition.type === "number") {
          control = input({ type: "number", value: definition.value ?? "", min: definition.min, max: definition.max });
        } else if (definition.type === "date") {
          control = input({ type: "date", value: definition.value ?? "" });
        } else {
          control = input({ type: definition.type === "time" ? "time" : "text", value: definition.value ?? "" });
        }
        if (definition.name) controls.set(definition.name, control);
        return field(definition.label, control, definition.hint);
      }),
    );

    openModal({
      title,
      body,
      actions: [
        { label: "Cancel", kind: "secondary", onClick: () => resolve(null) },
        {
          label: confirmLabel,
          kind: danger ? "danger" : "",
          onClick: () => {
            const values = {};
            for (const [name, control] of controls) {
              values[name] = control.type === "number" ? Number(control.value) : control.value;
            }
            resolve(values);
          },
        },
      ],
      onClose: () => resolve(null),
    });
  });
}
