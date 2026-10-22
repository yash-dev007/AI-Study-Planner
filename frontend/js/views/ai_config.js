/**
 * AI Configuration component and modal for setting up OpenRouter, Gemini, or Ollama.
 *
 * In production the server owns the provider credentials: the card exposes the
 * per-teacher opt-in, provider, and model, but never an API-key field. In
 * development the key can still be written to `backend/.env` from the UI.
 */

import { api } from "../api.js";
import {
  clear,
  el,
  field,
  input,
  mount,
  notice,
  openModal,
  select,
  toast,
} from "../dom.js";

export const OPENROUTER_MODELS = [
  { value: "google/gemini-2.0-flash-001", label: "Google: Gemini 2.0 Flash (Recommended · fast & economical)" },
  { value: "openai/gpt-4o-mini", label: "OpenAI: GPT-4o Mini (Fast & balanced)" },
  { value: "anthropic/claude-3.5-sonnet", label: "Anthropic: Claude 3.5 Sonnet (Advanced reasoning)" },
  { value: "deepseek/deepseek-chat", label: "DeepSeek: DeepSeek-V3 / Chat (Ultra low cost)" },
  { value: "meta-llama/llama-3.3-70b-instruct", label: "Meta: Llama 3.3 70B Instruct" },
  { value: "custom", label: "Custom model identifier…" },
];

export const GEMINI_MODELS = [
  { value: "gemini-2.0-flash", label: "Gemini 2.0 Flash (Recommended)" },
  { value: "gemini-1.5-flash", label: "Gemini 1.5 Flash" },
  { value: "gemini-1.5-pro", label: "Gemini 1.5 Pro" },
  { value: "custom", label: "Custom model identifier…" },
];

export function buildAiConfigCard(userSettings, onUpdated) {
  const canWriteKeys = userSettings?.ai_config_writable !== false;
  let activeProvider = userSettings?.preferred_ai_provider || "openrouter";
  if (activeProvider === "none") activeProvider = "openrouter";

  const providerSelect = select(
    [
      { value: "openrouter", label: "OpenRouter (Access Claude, GPT-4o, Gemini, DeepSeek)" },
      { value: "gemini", label: "Google Gemini (Direct API)" },
      { value: "ollama", label: "Ollama (Local LLM server)" },
    ],
    { value: activeProvider },
  );

  const keyInput = input({
    type: "password",
    placeholder: activeProvider === "openrouter" ? "Paste OpenRouter key (sk-or-v1-...)" : "Paste API key (AIzaSy...)",
    autocomplete: "off",
  });

  const toggleKeyBtn = el("button", {
    class: "btn btn--ghost btn--small",
    type: "button",
    text: "👁️ Show",
    style: { marginLeft: "0.5rem" },
    onClick: () => {
      if (keyInput.type === "password") {
        keyInput.type = "text";
        toggleKeyBtn.textContent = "🙈 Hide";
      } else {
        keyInput.type = "password";
        toggleKeyBtn.textContent = "👁️ Show";
      }
    },
  });

  const keyRow = el(
    "div",
    { class: "row", style: { alignItems: "center", width: "100%" } },
    el("div", { style: { flex: "1" } }, keyInput),
    toggleKeyBtn,
  );

  const customModelInput = input({
    placeholder: "e.g. mistralai/mistral-large-2407",
    value: userSettings?.preferred_ai_model || "",
  });
  const customModelField = field("Custom Model ID", customModelInput);
  customModelField.hidden = true;

  const getModelOptions = (provider) => {
    if (provider === "openrouter") return OPENROUTER_MODELS;
    if (provider === "gemini") return GEMINI_MODELS;
    return [{ value: "llama3.1:8b", label: "llama3.1:8b" }, { value: "custom", label: "Custom model…" }];
  };

  let currentModel =
    userSettings?.preferred_ai_model ||
    (activeProvider === "gemini"
      ? "gemini-2.0-flash"
      : activeProvider === "ollama"
      ? "llama3.1:8b"
      : "google/gemini-2.0-flash-001");
  const modelSelect = select(getModelOptions(activeProvider), { value: currentModel });

  function syncModelSelect() {
    const opts = getModelOptions(providerSelect.value);
    clear(modelSelect);
    for (const opt of opts) {
      modelSelect.append(el("option", { value: opt.value, text: opt.label }));
    }
    const hasPreset = opts.some((o) => o.value === currentModel);
    if (hasPreset) {
      modelSelect.value = currentModel;
      customModelField.hidden = true;
    } else {
      modelSelect.value = "custom";
      customModelInput.value = currentModel;
      customModelField.hidden = false;
    }
  }
  syncModelSelect();

  modelSelect.addEventListener("change", () => {
    if (modelSelect.value === "custom") {
      customModelField.hidden = false;
      customModelInput.focus();
    } else {
      customModelField.hidden = true;
      currentModel = modelSelect.value;
    }
  });

  providerSelect.addEventListener("change", () => {
    const prov = providerSelect.value;
    if (prov === "openrouter") {
      keyInput.placeholder = "Paste OpenRouter key (sk-or-v1-...)";
      currentModel = "google/gemini-2.0-flash-001";
    } else if (prov === "gemini") {
      keyInput.placeholder = "Paste Gemini key (AIzaSy...)";
      currentModel = "gemini-2.0-flash";
    } else {
      keyInput.placeholder = "(Ollama requires no API key)";
      currentModel = "llama3.1:8b";
    }
    syncModelSelect();
  });

  const accountToggle = el("input", { type: "checkbox" });
  accountToggle.checked = Boolean(userSettings?.ai_enabled);

  const statusContainer = el("div", { style: { marginBottom: "0.75rem" } });

  function activeProviderLabel(settings) {
    if (settings.openrouter_configured) {
      return `OpenRouter (${settings.openrouter_model || "google/gemini-2.0-flash-001"})`;
    }
    if (settings.gemini_configured) {
      return `Google Gemini (${settings.gemini_model || "gemini-2.0-flash"})`;
    }
    if (settings.ollama_configured) {
      return "Ollama";
    }
    return "no provider";
  }

  function updateStatusDisplay(settings) {
    const serverReady = Boolean(settings.llm_enabled);
    const enabled = Boolean(settings.ai_enabled);
    const provider = activeProviderLabel(settings);

    if (!serverReady) {
      mount(
        statusContainer,
        notice(
          "warn",
          "AI Is Not Configured on This Server",
          el("p", {
            text: canWriteKeys
              ? "Paste an API key below to unlock AI-assisted extraction and refinement."
              : `The deployment has no AI provider key. Ask the operator to set LLM_PROVIDER and the matching OPENROUTER_API_KEY or GEMINI_API_KEY in the server environment, then restart the API. Rule-based extraction keeps working without it.`,
          }),
        ),
      );
      return;
    }

    if (!enabled) {
      mount(
        statusContainer,
        notice(
          "info",
          "AI Is Available but Disabled for Your Account",
          el("p", {
            text: canWriteKeys
              ? `The server is configured for ${provider}. Tick "Use AI for my syllabus extraction" and save to opt in.`
              : `This server is configured for ${provider}. Tick "Use AI for my syllabus extraction" below to opt in; your provider credentials stay on the server.`,
          }),
        ),
      );
      return;
    }

    mount(
      statusContainer,
      notice(
        "ok",
        "AI Enabled for Your Account",
        el("p", {
          text: `Syllabus extraction and refinement can use ${provider}. AI is advisory: rejected or unavailable calls still fall back to the rule-based extractor.`,
        }),
      ),
    );
  }
  updateStatusDisplay(userSettings || {});

  const feedbackArea = el("div", { hidden: true });

  const testButton = el("button", {
    class: "btn btn--ghost",
    type: "button",
    text: canWriteKeys ? "🔌 Test Connection" : "🔌 Test Server Connection",
  });

  const saveKeyButton = el("button", {
    class: "btn",
    type: "button",
    text: "💾 Save API Key",
  });

  const savePreferencesButton = el("button", {
    class: "btn",
    type: "button",
    text: "💾 Save AI Preferences",
  });

  const selectedModel = () =>
    modelSelect.value === "custom" ? customModelInput.value.trim() : modelSelect.value;

  async function refreshSettings(fallback) {
    try {
      return await api.getSettings();
    } catch {
      return fallback;
    }
  }

  testButton.addEventListener("click", async () => {
    feedbackArea.hidden = true;
    testButton.disabled = true;
    testButton.textContent = "Testing…";
    try {
      const result = await api.testAiConnection({
        provider: providerSelect.value,
        api_key: keyInput.value.trim() || undefined,
        model: selectedModel() || undefined,
      });
      feedbackArea.hidden = false;
      if (result.ok) {
        mount(feedbackArea, notice("ok", "Connection Test Succeeded", el("p", { text: result.message })));
        toast(result.message, "ok");
      } else {
        mount(feedbackArea, notice("danger", "Connection Test Failed", el("p", { text: result.message })));
        toast(result.message, "error");
      }
    } catch (err) {
      feedbackArea.hidden = false;
      mount(feedbackArea, notice("danger", "Connection Error", el("p", { text: err.message })));
    } finally {
      testButton.disabled = false;
      testButton.textContent = canWriteKeys ? "🔌 Test Connection" : "🔌 Test Server Connection";
    }
  });

  saveKeyButton.addEventListener("click", async () => {
    feedbackArea.hidden = true;
    saveKeyButton.disabled = true;
    saveKeyButton.textContent = "Saving…";
    try {
      const updated = await api.saveAiConfig({
        provider: providerSelect.value,
        api_key: keyInput.value.trim() || undefined,
        model: selectedModel() || undefined,
      });
      // The endpoint always opts the teacher in; respect an explicit untick.
      if (!accountToggle.checked) {
        await api.updateSettings({ ai_enabled: false });
      }
      const fresh = await refreshSettings({
        ...userSettings,
        ...updated,
        ai_enabled: accountToggle.checked,
      });
      toast("AI settings saved successfully!", "ok");
      feedbackArea.hidden = false;
      mount(feedbackArea, notice("ok", "Configuration Saved", el("p", { text: updated.message })));
      updateStatusDisplay(fresh);
      keyInput.value = "";
      if (typeof onUpdated === "function") onUpdated(fresh);
    } catch (err) {
      feedbackArea.hidden = false;
      mount(feedbackArea, notice("danger", "Failed to save configuration", el("p", { text: err.message })));
      toast(err.message, "error");
    } finally {
      saveKeyButton.disabled = false;
      saveKeyButton.textContent = "💾 Save API Key";
    }
  });

  savePreferencesButton.addEventListener("click", async () => {
    feedbackArea.hidden = true;
    savePreferencesButton.disabled = true;
    savePreferencesButton.textContent = "Saving…";
    try {
      const updated = await api.updateSettings({
        ai_enabled: accountToggle.checked,
        preferred_ai_provider: providerSelect.value,
        preferred_ai_model: selectedModel() || "",
      });
      toast(
        accountToggle.checked ? "AI enabled for your account." : "AI disabled for your account.",
        "ok",
      );
      feedbackArea.hidden = false;
      mount(
        feedbackArea,
        notice(
          "ok",
          "Preferences Saved",
          el("p", {
            text: accountToggle.checked
              ? "Your syllabus uploads can now use the server-managed AI provider."
              : "Your syllabus uploads will use the rule-based extractor until you enable AI again.",
          }),
        ),
      );
      updateStatusDisplay(updated);
      if (typeof onUpdated === "function") onUpdated(updated);
    } catch (err) {
      feedbackArea.hidden = false;
      mount(feedbackArea, notice("danger", "Failed to save preferences", el("p", { text: err.message })));
      toast(err.message, "error");
    } finally {
      savePreferencesButton.disabled = false;
      savePreferencesButton.textContent = "💾 Save AI Preferences";
    }
  });

  return el(
    "div",
    { class: "stack" },
    statusContainer,
    !canWriteKeys
      ? el("p", {
          class: "muted",
          text: "API credentials are managed by the server environment in this deployment. You can still choose the provider, model, and whether your account uses AI.",
        })
      : null,
    field(
      "AI Provider",
      providerSelect,
      canWriteKeys
        ? "OpenRouter gives access to all leading AI models through a single key."
        : "Choose the provider the server should use when it processes your syllabi.",
    ),
    canWriteKeys
      ? field(
          "API Key",
          keyRow,
          "Stored securely in server backend/.env; never exposed or printed.",
        )
      : null,
    field("Model", modelSelect, "Select the model to use for extraction and syllabus refinement."),
    customModelField,
    el(
      "label",
      { class: "field field--check" },
      accountToggle,
      el("span", { text: "Use AI for my syllabus extraction" }),
    ),
    feedbackArea,
    el(
      "div",
      { class: "form-actions", style: { marginTop: "1rem" } },
      canWriteKeys ? saveKeyButton : savePreferencesButton,
      testButton,
    ),
  );
}

export async function openAiConfigModal(onSuccess) {
  let settings;
  try {
    settings = await api.getSettings();
  } catch (err) {
    toast(`Failed to load current settings: ${err.message}`, "error");
    settings = {};
  }

  const body = el("div", { class: "stack" });
  const { close } = openModal({
    title: "⚙️ AI Configuration",
    body,
  });

  const card = buildAiConfigCard(settings, (newSettings) => {
    toast("AI Configuration Updated!", "ok");
    setTimeout(() => {
      close();
      if (typeof onSuccess === "function") onSuccess(newSettings);
    }, 600);
  });

  mount(body, card);
}
