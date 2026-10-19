/** Sign in / create account. */

import { ApiError, api, session } from "../api.js";
import { el, field, input, mount, notice, select } from "../dom.js";

export async function render(ctx, mode = "login") {
  const isRegister = mode === "register";
  let demoHint = null;
  try {
    const health = await api.health();
    if (["development", "dev", "test"].includes(health.environment)) {
      demoHint = notice(
        "info",
        "Development seed accounts",
        el("p", {
          text: "professor@example.edu / professor123 (course CS201 is pre-loaded).",
        }),
      );
    }
  } catch {
    demoHint = null;
  }

  const errorBox = el("div", { hidden: true });

  const fields = [];
  if (isRegister) {
    fields.push(
      field("Full name", input({ name: "name", autocomplete: "name", required: true })),
    );
  }
  fields.push(
    field(
      "Email",
      input({ name: "email", type: "email", autocomplete: "email", required: true }),
    ),
    field(
      "Password",
      input({
        name: "password",
        type: "password",
        autocomplete: isRegister ? "new-password" : "current-password",
        required: true,
        minlength: isRegister ? 8 : undefined,
      }),
      isRegister ? "At least 8 characters." : null,
    ),
  );
  if (isRegister) {
    fields.push(
      field(
        "Role",
        select(
          [
            { value: "professor", label: "Professor" },
            { value: "admin", label: "Administrator" },
          ],
          { name: "role", value: "professor" },
        ),
      ),
    );
  }

  const form = el(
    "form",
    { class: "stack", novalidate: true },
    ...fields,
    errorBox,
    el("button", {
      class: "btn btn--block",
      type: "submit",
      text: isRegister ? "Create account" : "Sign in",
    }),
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const data = Object.fromEntries(new FormData(form));
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    submit.textContent = isRegister ? "Creating…" : "Signing in…";
    try {
      const result = isRegister
        ? await api.register({
            name: data.name,
            email: data.email,
            password: data.password,
            role: data.role || "professor",
          })
        : await api.login(data.email, data.password);
      session.save(result.access_token, result.user);
      ctx.navigate("#/courses");
    } catch (error) {
      errorBox.hidden = false;
      mount(
        errorBox,
        error instanceof ApiError && error.code === "EMAIL_TAKEN"
          ? notice("warn", "That email is already registered.", el("p", { text: "Sign in instead." }))
          : notice(
              "danger",
              isRegister ? "Could not create the account." : "Could not sign in.",
              el("p", { text: error.message }),
            ),
      );
      submit.disabled = false;
      submit.textContent = isRegister ? "Create account" : "Sign in";
    }
  });

  mount(
    ctx.root,
    el(
      "div",
      { class: "auth-shell" },
      el(
        "div",
        { class: "auth-card" },
        el("h1", { text: isRegister ? "Create your account" : "Sign in" }),
        el("p", {
          class: "muted",
          text: isRegister
            ? "Professors plan courses here. Students do not use this system."
            : "Plan your syllabus, generate a lecture schedule, and recover from missed lectures.",
        }),
        demoHint,
        form,
        el(
          "div",
          { class: "auth-switch" },
          isRegister ? "Already have an account? " : "No account yet? ",
          el("a", {
            href: isRegister ? "#/login" : "#/register",
            text: isRegister ? "Sign in" : "Create one",
          }),
        ),
      ),
    ),
  );
}
