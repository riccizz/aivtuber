const state = {
  mode: "user",
  persona: "",
  personas: [],
  runtimeSettings: {},
  runtimeSettingsSchema: [],
  settingsDirty: false,
  isInitializing: true,
  isReady: false,
  initError: "",
  hints: {
    user: "`user` 模式会把输入当作观众/用户发言。",
    cmd: "`cmd` 模式会把输入当作后台操作员对 AI 的直接指令。"
  }
};

const modeBtns = [...document.querySelectorAll(".mode-btn")];
const modeLabel = document.getElementById("modeLabel");
const modeHint = document.getElementById("modeHint");
const textInput = document.getElementById("textInput");
const statusText = document.getElementById("statusText");
const settingsStatus = document.getElementById("settingsStatus");
const sendBtn = document.getElementById("sendBtn");
const exitBtn = document.getElementById("exitBtn");
const saveSettingsBtn = document.getElementById("saveSettingsBtn");
const initBanner = document.getElementById("initBanner");
const feed = document.getElementById("feed");
const historyPanel = document.getElementById("historyPanel");
const sideStack = document.getElementById("sideStack");
const personaRow = document.getElementById("personaRow");
const settingsForm = document.getElementById("settingsForm");

function setMode(mode) {
  state.mode = mode;
  modeBtns.forEach((btn) => btn.classList.toggle("active", btn.dataset.mode === mode));
  modeLabel.textContent = `当前模式: ${mode}`;
  modeHint.textContent = state.hints[mode];
  textInput.placeholder = mode === "cmd" ? "例如：现在讲一段开场白，语气强势一点" : "例如：鹭鹭晚上好";
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderPersonas() {
  personaRow.innerHTML = state.personas.map((persona) => `
    <button
      class="persona-btn ${persona.key === state.persona ? "active" : ""}"
      data-persona="${escapeHtml(persona.key)}"
      type="button"
      ${!state.isReady ? "disabled" : ""}
    >${escapeHtml(persona.label)}</button>
  `).join("");
  [...personaRow.querySelectorAll(".persona-btn")].forEach((btn) => {
    btn.addEventListener("click", () => switchPersona(btn.dataset.persona));
  });
}

function renderInitState() {
  const disabled = !state.isReady;
  modeBtns.forEach((btn) => {
    btn.disabled = disabled;
  });
  textInput.disabled = disabled;
  sendBtn.disabled = disabled;
  if (state.isInitializing) {
    initBanner.hidden = false;
    initBanner.textContent = "CosyVoice 正在初始化，初始化完成前暂时不能发送输入。";
    statusText.textContent = "CosyVoice 正在初始化...";
    return;
  }
  if (state.initError) {
    initBanner.hidden = false;
    initBanner.textContent = `CosyVoice 初始化失败：${state.initError}`;
    statusText.textContent = `初始化失败: ${state.initError}`;
    return;
  }
  initBanner.hidden = true;
  if (statusText.textContent === "CosyVoice 正在初始化...") {
    statusText.textContent = "";
  }
}

function isFieldVisible(field) {
  if (!field.visible_if) return true;
  return Object.entries(field.visible_if).every(([key, expected]) => state.runtimeSettings[key] === expected);
}

function isFieldDisabled(field) {
  if (!field.disabled_if) return false;
  return Object.entries(field.disabled_if).every(([key, expected]) => state.runtimeSettings[key] === expected);
}

function renderRuntimeSettings() {
  settingsForm.innerHTML = state.runtimeSettingsSchema
  .filter((field) => isFieldVisible(field))
  .map((field) => {
    const value = state.runtimeSettings[field.key] ?? "";
    const disabled = isFieldDisabled(field);
    if (field.key === "voice_choice") {
      return `
        <div class="settings-field">
          <label>${escapeHtml(field.label)}</label>
          <div class="choice-row">
            ${field.options.map((option) => `
              <button
                class="choice-btn ${option.value === value ? "active" : ""}"
                data-setting-key="${escapeHtml(field.key)}"
                data-setting-value="${escapeHtml(option.value)}"
                type="button"
              >${escapeHtml(option.label)}</button>
            `).join("")}
          </div>
        </div>
      `;
    }
    if (field.key === "idle_enabled") {
      const checked = value === "true";
      return `
        <div class="settings-field switch-field">
          <div class="switch-row">
            <label for="setting-${escapeHtml(field.key)}">${escapeHtml(field.label)}</label>
            <button
              id="setting-${escapeHtml(field.key)}"
              class="ios-switch ${checked ? "active" : ""}"
              data-setting-key="${escapeHtml(field.key)}"
              data-setting-value="${checked ? "true" : "false"}"
              type="button"
              role="switch"
              aria-checked="${checked ? "true" : "false"}"
              aria-label="${escapeHtml(field.label)}"
            >
              <span class="ios-switch-thumb"></span>
            </button>
          </div>
        </div>
      `;
    }
    if (field.control === "select") {
      return `
        <div class="settings-field">
          <label for="setting-${escapeHtml(field.key)}">${escapeHtml(field.label)}</label>
          <select id="setting-${escapeHtml(field.key)}" data-setting-key="${escapeHtml(field.key)}" ${disabled ? "disabled" : ""}>
            ${field.options.map((option) => `
              <option value="${escapeHtml(option.value)}" ${option.value === value ? "selected" : ""}>${escapeHtml(option.label)}</option>
            `).join("")}
          </select>
        </div>
      `;
    }
    if (field.control === "range") {
      return `
        <div class="settings-field ${disabled ? "is-disabled" : ""}">
          <label for="setting-${escapeHtml(field.key)}">${escapeHtml(field.label)}</label>
          <div class="range-row ${disabled ? "is-disabled" : ""}">
            <input
              id="setting-${escapeHtml(field.key)}"
              data-setting-key="${escapeHtml(field.key)}"
              type="range"
              min="${field.min}"
              max="${field.max}"
              step="${field.step || 1}"
              value="${value}"
              ${disabled ? "disabled" : ""}
            >
            <span class="range-value" id="setting-value-${escapeHtml(field.key)}">${value}</span>
          </div>
        </div>
      `;
    }
    return `
      <div class="settings-field">
        <label for="setting-${escapeHtml(field.key)}">${escapeHtml(field.label)}</label>
        <input
          id="setting-${escapeHtml(field.key)}"
          data-setting-key="${escapeHtml(field.key)}"
          type="${field.control === "password" ? "password" : "text"}"
          value="${escapeHtml(String(value))}"
          ${disabled ? "disabled" : ""}
        >
      </div>
    `;
  }).join("");

  [...settingsForm.querySelectorAll(".choice-btn")].forEach((button) => {
    button.addEventListener("click", () => {
      state.settingsDirty = true;
      state.runtimeSettings[button.dataset.settingKey] = button.dataset.settingValue;
      renderRuntimeSettings();
    });
  });
  [...settingsForm.querySelectorAll(".ios-switch")].forEach((button) => {
    button.addEventListener("click", () => {
      state.settingsDirty = true;
      state.runtimeSettings[button.dataset.settingKey] = button.dataset.settingValue === "true" ? "false" : "true";
      renderRuntimeSettings();
    });
  });
  [...settingsForm.querySelectorAll('input[type="range"]')].forEach((input) => {
    input.addEventListener("input", () => {
      state.settingsDirty = true;
      state.runtimeSettings[input.dataset.settingKey] = Number(input.value);
      const valueNode = document.getElementById(`setting-value-${input.dataset.settingKey}`);
      if (valueNode) valueNode.textContent = input.value;
    });
  });
  [...settingsForm.querySelectorAll('input:not([type="range"]), select')].forEach((input) => {
    input.addEventListener("input", () => {
      state.settingsDirty = true;
      state.runtimeSettings[input.dataset.settingKey] = input.value;
    });
    input.addEventListener("change", () => {
      state.settingsDirty = true;
      state.runtimeSettings[input.dataset.settingKey] = input.value;
      if (input.dataset.settingKey === "playback_backend") {
        renderRuntimeSettings();
      }
    });
  });
}

function collectRuntimeSettings() {
  const nextSettings = { ...state.runtimeSettings };
  state.runtimeSettingsSchema.forEach((field) => {
    if (field.key === "voice_choice" || field.key === "idle_enabled") {
      nextSettings[field.key] = state.runtimeSettings[field.key];
      return;
    }
    if (isFieldDisabled(field)) {
      return;
    }
    const node = settingsForm.querySelector(`[data-setting-key="${field.key}"]`);
    if (!node) return;
    if (field.control === "range") {
      nextSettings[field.key] = Number(node.value);
      return;
    }
    nextSettings[field.key] = node.value;
  });
  return nextSettings;
}

async function saveRuntimeSettings() {
  saveSettingsBtn.disabled = true;
  settingsStatus.textContent = "保存中...";
  try {
    const settings = collectRuntimeSettings();
    const res = await fetch("/api/runtime-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "save failed");
    state.settingsDirty = false;
    settingsStatus.textContent = data.message || "已保存";
    await refreshState();
  } catch (err) {
    settingsStatus.textContent = `保存失败: ${err.message}`;
  } finally {
    saveSettingsBtn.disabled = false;
  }
}

async function switchPersona(persona) {
  if (!state.isReady) return;
  if (!persona || persona === state.persona) return;
  statusText.textContent = "切换人格中...";
  try {
    const res = await fetch("/api/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "switch failed");
    statusText.textContent = data.message || "人格已切换";
    await refreshState();
  } catch (err) {
    statusText.textContent = `切换失败: ${err.message}`;
  }
}

async function sendInput() {
  if (!state.isReady) return;
  const text = textInput.value.trim();
  if (!text) return;
  sendBtn.disabled = true;
  statusText.textContent = "发送中...";
  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: state.mode, text })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "send failed");
    textInput.value = "";
    statusText.textContent = data.message || "已发送";
  } catch (err) {
    statusText.textContent = `发送失败: ${err.message}`;
  } finally {
    sendBtn.disabled = false;
  }
}

async function exitApp() {
  if (!window.confirm("确认退出 AIVtuber 吗？")) {
    return;
  }
  exitBtn.disabled = true;
  statusText.textContent = "正在退出...";
  try {
    const res = await fetch("/api/exit", {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "exit failed");
    statusText.textContent = data.message || "正在退出";
    window.setTimeout(() => {
      try {
        window.open("", "_self");
        window.close();
      } catch (err) {
      }
      window.location.replace("about:blank");
    }, 150);
  } catch (err) {
    exitBtn.disabled = false;
    statusText.textContent = `退出失败: ${err.message}`;
  }
}

function renderState(data) {
  state.isInitializing = Boolean(data.is_initializing);
  state.isReady = Boolean(data.is_ready);
  state.initError = data.init_error || "";
  state.persona = data.current_persona?.key || "";
  state.personas = data.personas || [];
  state.runtimeSettingsSchema = data.runtime_settings_schema || [];
  if (!state.settingsDirty) {
    state.runtimeSettings = data.runtime_settings || {};
  }
  document.getElementById("eventCount").textContent = `${data.events.length} 条`;
  renderPersonas();
  renderInitState();
  if (!state.settingsDirty) {
    renderRuntimeSettings();
  }
  feed.innerHTML = [...data.events].reverse().map((event) => `
    <article class="event">
      <div class="event-meta">
        <span class="event-role">${escapeHtml(event.role_label)}</span>
        <span>${escapeHtml(event.ts_label)}</span>
      </div>
      <div class="event-text">${escapeHtml(event.text)}</div>
    </article>
  `).join("");
  feed.scrollTop = 0;
}

async function refreshState() {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();
    renderState(data);
  } catch (err) {
    statusText.textContent = `状态刷新失败: ${err.message}`;
  }
}

function syncHistoryHeight() {
  if (window.innerWidth <= 900) {
    historyPanel.style.height = "";
    return;
  }
  historyPanel.style.height = `${sideStack.offsetHeight}px`;
}

modeBtns.forEach((btn) => btn.addEventListener("click", () => setMode(btn.dataset.mode)));
sendBtn.addEventListener("click", sendInput);
exitBtn.addEventListener("click", exitApp);
saveSettingsBtn.addEventListener("click", saveRuntimeSettings);
textInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendInput();
  }
});

setMode("user");
syncHistoryHeight();
refreshState();
new ResizeObserver(syncHistoryHeight).observe(sideStack);
window.addEventListener("resize", syncHistoryHeight);
setInterval(refreshState, 1500);
