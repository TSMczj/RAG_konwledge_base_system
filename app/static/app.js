/* ============================================================
   RAG 知识库 UI
   聊天、文档、设置三页均已接 FastAPI。
   ============================================================ */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const MODELS = {
  "deepseek-v4-flash": { provider: "deepseek", label: "DeepSeek V4 Flash" },
  "gpt-4.1-mini": { provider: "openai", label: "GPT-4.1 mini" },
  "claude-sonnet-4": { provider: "anthropic", label: "Claude Sonnet 4" },
};
const API_MODELS = new Set(["deepseek-v4-flash"]);

async function apiJson(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const body = JSON.parse(text);
      detail = body?.detail?.message || body?.detail || body?.message || text;
    } catch {
      // keep raw text
    }
    throw new Error(`${response.status}${detail ? ` · ${detail}` : ""}`);
  }
  return response.json();
}

const state = {
  view: "chat",
  lang: window.I18n?.getLanguage?.() || "zh",
  model: localStorage.getItem("rag-ui-model") || "deepseek-v4-flash",
  streaming: false,
  abortController: null,
  sessionId: null,
  conversation: [],
  sources: [],
  sourcesNote: "回答后在这里查看引用来源。",
  activeSource: null,
  docs: [],
  docsLoading: true,
  docsError: "",
  docTab: "active",
  keys: {
    deepseek: { configured: false, masked: "未配置", source: "", updatedAt: "" },
    dashscope: { configured: false, masked: "未配置", source: "", updatedAt: "" },
    openai: { configured: false, masked: "未配置", source: "", updatedAt: "" },
    anthropic: { configured: false, masked: "未配置", source: "", updatedAt: "" },
  },
  setupStep: 1,
  setupTested: false,
  search: "",
  rebuilt: "—",
  streamTimer: null,
  streamAssistant: null,
  streamFull: "",
  streamIndex: 0,
  streamDone: false,
};

const messagesEl = $("#messageList");
const sourcesListEl = $("#sourcesList");
const sourcesPanel = $("#sourcesPanel");
const drawerBackdrop = $("#drawerBackdrop");
const questionInput = $("#questionInput");
const sendButton = $("#sendButton");
const docsTableBody = $("#docsTableBody");
const fileInput = $("#fileInput");
const modalBackdrop = $("#modalBackdrop");
const modalTitle = $("#modalTitle");
const modalBody = $("#modalBody");
const modalFooter = $("#modalFooter");
const toastEl = $("#toast");

let modalConfirmHandler = null;
let toastTimer = null;

/* ---------- utilities ---------- */

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function formatNumber(value) {
  return new Intl.NumberFormat(state.lang === "en" ? "en-US" : "zh-CN").format(Number(value) || 0);
}

function maskKey(value) {
  const key = String(value || "").trim();
  if (!key) return "";
  if (key.length <= 8) return "••••";
  const prefix = key.startsWith("sk-") ? "sk-" : "";
  return `${prefix}${"•".repeat(18)}${key.slice(-4)}`;
}

function bothKeysConfigured() {
  const provider = modelDef(state.model).provider;
  return Boolean(state.keys.dashscope.configured && state.keys[provider]?.configured);
}

function setupKeysConfigured() {
  return Boolean(state.keys.deepseek.configured && state.keys.dashscope.configured);
}

function missingKeyText() {
  const missing = [];
  if (!state.keys.deepseek.configured) missing.push("DeepSeek");
  if (!state.keys.dashscope.configured) missing.push("百炼嵌入");
  if (!missing.length) return "";
  if (missing.length === 2) {
    return "尚未配置 DeepSeek 和百炼嵌入 API Key。聊天需要两者：DeepSeek 负责生成回答，百炼负责检索与上传。请先到设置中配置。";
  }
  if (missing[0] === "DeepSeek") {
    return "尚未配置 DeepSeek API Key，无法生成回答。请先到设置中配置。";
  }
  return "尚未配置百炼嵌入 API Key，无法检索知识库或上传文档。请先到设置中配置。";
}

function refreshKeyStateUI() {
  const banner = $("#keyBanner");
  if (!banner) return;
  banner.hidden = bothKeysConfigured();
}

function providerLabel(provider) {
  const labels = {
    deepseek: "DeepSeek",
    dashscope: "阿里云百炼",
    openai: "OpenAI",
    anthropic: "Anthropic",
  };
  return labels[provider] || provider;
}

function secretSourceLabel(source) {
  return { local: "本机保存", env: "系统环境变量", none: "未配置" }[source] || "未配置";
}

function secretStateFromApi(payload) {
  return {
    configured: Boolean(payload.configured),
    masked: payload.masked || "未配置",
    source: secretSourceLabel(payload.source),
    updatedAt: payload.updated_at ? formatApiDate(payload.updated_at) : "",
  };
}

async function loadSecrets() {
  try {
    const data = await apiJson("/settings/secrets");
    Object.entries(data.providers || {}).forEach(([provider, payload]) => {
      state.keys[provider] = secretStateFromApi(payload);
    });
    state.secretsComplete = Boolean(data.complete);
  } catch (error) {
    state.secretsError = error.message;
  }
  renderSettings();
  refreshKeyStateUI();
  updateModelSelect();
}

async function saveSecret(provider, key) {
  const payload = await apiJson(`/settings/secrets/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  state.keys[provider] = secretStateFromApi(payload);
  renderSettings();
  refreshKeyStateUI();
  updateModelSelect();
  if (bothKeysConfigured()) loadDocuments();
  return payload;
}

async function testSecret(provider) {
  return apiJson(`/settings/secrets/${provider}/test`, { method: "POST" });
}

async function clearSecret(provider) {
  const payload = await apiJson(`/settings/secrets/${provider}`, { method: "DELETE" });
  state.keys[provider] = {
    configured: Boolean(payload.configured),
    masked: "未配置",
    source: "",
    updatedAt: "",
  };
  renderSettings();
  refreshKeyStateUI();
  updateModelSelect();
  return payload;
}

function modelDef(id) {
  return MODELS[id] || MODELS["deepseek-v4-flash"];
}

function modelLabel(id) {
  return modelDef(id).label;
}

function modelAvailable(id) {
  return API_MODELS.has(id) && Boolean(state.keys[modelDef(id).provider]?.configured);
}

function updateModelSelect() {
  const select = $("#modelSelect");
  if (!select) return;
  $$("option", select).forEach((option) => {
    option.disabled = !modelAvailable(option.value);
  });
  if (!modelAvailable(state.model)) {
    const fallback = Object.keys(MODELS).find((id) => modelAvailable(id)) || "deepseek-v4-flash";
    state.model = fallback;
  }
  select.value = state.model;
  const hint = $("#modelHint");
  if (hint) hint.textContent = `当前回答使用 ${modelLabel(state.model)}`;
}

function handleModelChange(next) {
  if (!API_MODELS.has(next)) {
    showToast("多模型适配中，当前仅支持 DeepSeek", "error");
    updateModelSelect();
    return;
  }
  if (!modelAvailable(next)) {
    showToast("该模型尚未配置 Key，请先去设置", "error");
    updateModelSelect();
    return;
  }
  state.model = next;
  try {
    localStorage.setItem("rag-ui-model", next);
  } catch {
    // ignore
  }
  updateModelSelect();
  showToast(`下一条回答将使用 ${modelLabel(next)}`);
}

function renderSettings() {
  ["deepseek", "dashscope", "openai", "anthropic"].forEach((provider) => {
    const config = state.keys[provider];
    if (!config) return;
    const status = $(`#${provider}Status`);
    const mask = $(`#${provider}Mask`);
    const meta = $(`#${provider}Meta`);
    const edit = $(`#${provider}Edit`);
    const test = $(`#${provider}Test`);
    const clear = $(`#${provider}Clear`);
    if (!status) return;

    if (!["deepseek", "dashscope"].includes(provider)) {
      status.textContent = "未接入";
      status.className = "key-status unconfigured";
      mask.textContent = "—";
      meta.textContent = "多模型适配中，暂未接入";
      edit.disabled = true;
      test.disabled = true;
      clear.disabled = true;
      return;
    }

    status.textContent = config.configured ? "已配置" : "未配置";
    status.className = `key-status ${config.configured ? "configured" : "unconfigured"}`;
    mask.textContent = config.configured ? config.masked : "未配置";
    meta.textContent = config.configured
      ? [config.source, config.updatedAt ? `更新于 ${config.updatedAt}` : ""].filter(Boolean).join(" · ")
      : (provider === "dashscope" ? "上传与检索需要这个 Key" : "配置后才能使用这个模型");
    edit.textContent = config.configured ? "更换 Key" : "配置 Key";
    test.disabled = !config.configured;
    clear.disabled = !config.configured;
  });
  updateModelSelect();
}

function openKeyEditor(provider) {
  const label = providerLabel(provider);
  openModal({
    title: `${state.keys[provider].configured ? "更换" : "配置"} ${label} Key`,
    body: `
      <label class="key-editor-label" for="keyEditorInput">${label} API Key</label>
      <input class="key-editor-input" id="keyEditorInput" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴新的 Key">
      <p class="modal-warning">保存后只显示末 4 位，例如 <code>sk-••••••••••••••••••4f2a</code>；不会显示完整内容，也不会写进日志。</p>`,
    confirmText: "保存",
    onConfirm: async () => {
      const input = $("#keyEditorInput");
      const value = input ? input.value.trim() : "";
      if (!value) {
        showToast("请输入 API Key", "error");
        return;
      }
      const button = $("#modalConfirm");
      if (button) {
        button.disabled = true;
        button.textContent = "保存中…";
      }
      try {
        await saveSecret(provider, value);
        closeModal();
        showToast(`${label} Key 已保存`);
      } catch (error) {
        showToast(`保存失败：${error.message}`, "error");
        if (button) {
          button.disabled = false;
          button.textContent = "保存";
        }
      }
    },
  });
}

async function testKey(provider) {
  if (!state.keys[provider].configured) return;
  const button = $(`#${provider}Test`);
  if (!button) return;
  button.disabled = true;
  button.textContent = "测试中…";
  try {
    const result = await testSecret(provider);
    if (result.ok) {
      showToast(`${providerLabel(provider)} 连接正常${result.latency_ms ? `（${result.latency_ms}ms）` : ""}`);
    } else {
      showToast(`${providerLabel(provider)} 连接失败：${result.message || "未知错误"}`, "error");
    }
  } catch (error) {
    showToast(`${providerLabel(provider)} 测试失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "测试连接";
  }
}

function clearKey(provider) {
  const label = providerLabel(provider);
  openModal({
    title: `清除 ${label} Key？`,
    body: `<p>清除后，依赖这个 Key 的功能会立即停止工作；可以随时重新配置。</p>
           <p class="modal-warning">Key 从本机加密存储中删除，不会影响其他 provider。</p>`,
    confirmText: "清除",
    danger: true,
    onConfirm: async () => {
      try {
        await clearSecret(provider);
        closeModal();
        showToast(`${label} Key 已清除`);
      } catch (error) {
        showToast(`清除失败：${error.message}`, "error");
      }
    },
  });
}

function restartSetup() {
  openModal({
    title: "重新运行首次引导？",
    body: `<p>会回到首次启动引导，方便重新检查两个 Key 的配置。</p>
           <p class="modal-warning">不会删除已保存的 Key；如需清除，请在 Key 卡片上单独操作。</p>`,
    confirmText: "重新引导",
    danger: false,
    onConfirm: () => {
      state.setupStep = 1;
      state.setupTested = false;
      renderSettings();
      refreshKeyStateUI();
      closeModal();
      switchView("setup");
    },
  });
}

function showKeyError(title, message) {
  openModal({
    title,
    body: `<p>${escapeHtml(message)}</p>
           <p class="modal-warning">Key 只保存在本机；配置后会立即生效。</p>`,
    confirmText: "去设置",
    onConfirm: () => {
      closeModal();
      switchView("settings");
    },
  });
}

function setupProgress(step) {
  return `
    <div class="setup-progress" aria-label="配置进度">
      ${[1, 2].map((item) => `<span class="${item <= step ? "done" : ""} ${item === step ? "current" : ""}">${item}</span>`).join("")}
      <em>第 ${step} / 2 步</em>
    </div>`;
}

function renderSetup() {
  const card = $("#setupCard");
  if (!card) return;
  const step = state.setupStep;

  if (step === 1) {
    const chatConfigured = Boolean(state.keys.deepseek?.configured);
    const embedConfigured = Boolean(state.keys.dashscope?.configured);
    card.innerHTML = `
      <div class="setup-intro">
        <div class="setup-hero-window" aria-hidden="true">
          <img class="setup-hero-logo" src="/static/assets/logo-mark.svg" alt="">
        </div>
        <div class="setup-motto-block">
          <span class="eyebrow">WELCOME</span>
          <div class="setup-motto" aria-label="知者不惑。—— 孔子">
            <span class="motto-line motto-top">知者不惑。</span>
            <span class="motto-author">—— 孔子</span>
          </div>
        </div>
      </div>
      <div class="setup-key-head">
        <span class="eyebrow">STEP 1</span>
        <h2>配置模型 api-key</h2>
        <p class="setup-lead">这个知识库需要两个 Key 才能完成问答。它们只保存在本机，不会上传到我们的服务器。</p>
      </div>

      <section class="setup-provider-section" aria-labelledby="setupChatTitle">
        <h3 id="setupChatTitle">问答模型 Key</h3>
        <label class="setup-field" for="setupChatProvider">Provider
          <select id="setupChatProvider">
            <option value="deepseek">DeepSeek</option>
          </select>
        </label>
        ${chatConfigured ? `<div class="setup-existing">已配置：<code>${escapeHtml(state.keys.deepseek.masked)}</code>，留空则保留当前 Key。</div>` : ""}
        <label class="setup-field" for="setupChatKey">DeepSeek API Key
          <input id="setupChatKey" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 DeepSeek API Key">
        </label>
      </section>

      <section class="setup-provider-section" aria-labelledby="setupEmbedTitle">
        <h3 id="setupEmbedTitle">嵌入模型 Key</h3>
        <label class="setup-field" for="setupEmbedProvider">Provider
          <select id="setupEmbedProvider">
            <option value="dashscope">阿里云百炼</option>
          </select>
        </label>
        ${embedConfigured ? `<div class="setup-existing">已配置：<code>${escapeHtml(state.keys.dashscope.masked)}</code>，留空则保留当前 Key。</div>` : ""}
        <label class="setup-field" for="setupEmbedKey">阿里云百炼 API Key
          <input id="setupEmbedKey" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 阿里云百炼 API Key">
        </label>
      </section>

      <div class="setup-error" id="setupError" hidden></div>
      <div class="setup-note">保存后只显示末 4 位，例如 <code>sk-••••••••••••••••••4f2a</code>；不会显示完整内容。</div>
      <div class="setup-actions">
        <button class="primary-button" id="setupNext" type="button">保存并继续</button>
        <button class="soft-button" id="setupSkip" type="button">跳过，先只读浏览</button>
      </div>`;
    $("#setupNext").addEventListener("click", async () => {
      const chatInput = $("#setupChatKey");
      const embedInput = $("#setupEmbedKey");
      const chatValue = chatInput ? chatInput.value.trim() : "";
      const embedValue = embedInput ? embedInput.value.trim() : "";
      const button = $("#setupNext");
      const errorBox = $("#setupError");
      const showError = (message) => {
        if (errorBox) {
          errorBox.textContent = message;
          errorBox.hidden = false;
        }
      };

      if (!chatValue && !chatConfigured) {
        showError("请填写 DeepSeek API Key。");
        return;
      }
      if (!embedValue && !embedConfigured) {
        showError("请填写阿里云百炼 API Key。");
        return;
      }

      if (!chatValue && !embedValue) {
        state.setupStep = 2;
        renderSetup();
        return;
      }

      if (button) {
        button.disabled = true;
        button.textContent = "保存中…";
      }
      try {
        if (chatValue) await saveSecret("deepseek", chatValue);
        if (embedValue) await saveSecret("dashscope", embedValue);
      } catch (error) {
        showError(`保存失败：${error.message}`);
        if (button) {
          button.disabled = false;
          button.textContent = "保存并继续";
        }
        return;
      }

      state.setupTested = false;
      if (!setupKeysConfigured()) {
        showError("Key 尚未配置完整，请检查。");
        if (button) {
          button.disabled = false;
          button.textContent = "保存并继续";
        }
        return;
      }
      state.setupStep = 2;
      renderSetup();
    });
    $("#setupSkip").addEventListener("click", skipSetup);
    return;
  }

  const ready = setupKeysConfigured();
  card.innerHTML = `
    ${setupProgress(2)}
    <span class="eyebrow">STEP 2</span>
    <h2>测试连接</h2>
    <p class="setup-lead">保存前先确认两个 Key 都能连通。测试由服务端发起，浏览器不会接触完整 Key。</p>
    <div class="setup-test-list">
      <div class="setup-test-row"><span>DeepSeek</span><strong class="${state.keys.deepseek.configured ? "ok" : "missing"}">${state.keys.deepseek.configured ? "已保存" : "未配置"}</strong></div>
      <div class="setup-test-row"><span>阿里云百炼</span><strong class="${state.keys.dashscope.configured ? "ok" : "missing"}">${state.keys.dashscope.configured ? "已保存" : "未配置"}</strong></div>
    </div>
    ${state.setupTested ? '<div class="setup-success">连接测试通过，可以开始使用。</div>' : ""}
    <div class="setup-actions">
      <button class="soft-button" id="setupBack" type="button">上一步</button>
      <button class="soft-button" id="setupTest" type="button" ${ready ? "" : "disabled"}>测试连接</button>
      <button class="primary-button" id="setupFinish" type="button">完成，进入知识库</button>
    </div>
    <button class="setup-skip-link" id="setupSkip" type="button">先跳过，稍后配置</button>`;
  $("#setupBack").addEventListener("click", () => {
    state.setupStep = 1;
    renderSetup();
  });
  $("#setupTest").addEventListener("click", async () => {
    const button = $("#setupTest");
    if (!button) return;
    button.disabled = true;
    button.textContent = "测试中…";
    try {
      const results = await Promise.all([
        testSecret("deepseek"),
        testSecret("dashscope"),
      ]);
      const failed = results.filter((result) => !result.ok);
      if (!failed.length) {
        state.setupTested = true;
        renderSetup();
        showToast("两个 Key 连接正常");
      } else {
        renderSetup();
        showToast(`连接测试失败：${failed[0].message || "未知错误"}`, "error");
      }
    } catch (error) {
      button.disabled = false;
      button.textContent = "测试连接";
      showToast(`连接测试失败：${error.message}`, "error");
    }
  });
  $("#setupFinish").addEventListener("click", async () => {
    if (!setupKeysConfigured()) {
      showToast("还有 Key 未配置，可先跳过", "error");
      return;
    }
    await loadDocuments();
    refreshKeyStateUI();
    switchView("chat");
    showToast("配置完成，开始提问吧");
  });
  $("#setupSkip").addEventListener("click", skipSetup);
}

function skipSetup() {
  switchView("documents");
  refreshKeyStateUI();
  showToast("已跳过配置：聊天与上传会提示缺少 API Key", "error");
}

function nowLabel() {
  return "刚刚";
}

function updateStats() {
  const activeDocs = state.docs.filter((doc) => !doc.removed);
  const removedDocs = state.docs.filter((doc) => doc.removed);
  const activeChunks = activeDocs.reduce((sum, doc) => sum + (Number(doc.chunks) || 0), 0);
  const removedChunks = removedDocs.reduce((sum, doc) => sum + (Number(doc.chunks) || 0), 0);

  $("#statDocs").textContent = formatNumber(activeDocs.length);
  $("#statChunks").textContent = formatNumber(activeChunks);
  $("#topChunkCount").textContent = formatNumber(activeChunks);
  $("#statRebuild").textContent = state.rebuilt;
  $("#activeDocCount").textContent = formatNumber(activeDocs.length);
  $("#removedDocCount").textContent = formatNumber(removedDocs.length);
  $("#chatSubtitle").textContent = activeDocs.length
    ? `基于 ${activeDocs.length} 份文档回答，引用可以展开核对原文`
    : "当前没有可检索文档，先上传或恢复文档";
  $("#docsFooter").textContent =
    `可检索 ${activeDocs.length} 个文件 / ${formatNumber(activeChunks)} 个文本片段` +
    (removedDocs.length ? `；已移出 ${removedDocs.length} 个（${formatNumber(removedChunks)} 个文本片段仍占用向量空间）` : "");

  if (!state.conversation.length) renderConversation();
}

function scrollMessages() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

/* ---------- chat rendering ---------- */

function renderConversation() {
  if (!state.conversation.length) {
    const activeDocs = state.docs.filter((doc) => !doc.removed);
    const totalChunks = activeDocs.reduce((sum, doc) => sum + (Number(doc.chunks) || 0), 0);
    messagesEl.innerHTML = `
      <div class="empty-state">
        <img class="empty-logo" src="/static/assets/logo-mark.svg" alt="RAG 知识库">
        <h3>从你的知识库开始提问</h3>
        <p>已索引 ${activeDocs.length} 份文档、${formatNumber(totalChunks)} 个文本片段。回答会带上引用，点击 [n] 可以展开原文核对。</p>
      </div>`;
    return;
  }

  messagesEl.innerHTML = state.conversation
    .map((message, messageIndex) => {
      if (message.role === "user") {
        return `
          <article class="message user">
            <div class="user-bubble">${escapeHtml(message.text)}</div>
          </article>`;
      }

      if (message.role === "error") {
        return `
          <article class="message assistant error-message">
            <div class="assistant-avatar error" aria-hidden="true">!</div>
            <div class="assistant-body">
              <div class="message-meta"><strong>需要配置</strong><span class="meta-dot">·</span><span>${escapeHtml(message.time || "刚刚")}</span></div>
              <div class="key-error-card">
                <p>${escapeHtml(message.text)}</p>
                <button class="go-settings" type="button" data-action="settings">去设置</button>
              </div>
            </div>
          </article>`;
      }

      const citations = (message.citations || [])
        .map(
          (n) =>
            `<button class="citation ${state.activeSource === n ? "active" : ""}" type="button" data-source="${n}" aria-label="查看引用 ${n}">${n}</button>`
        )
        .join("");

      let answer = "";
      if (message.streaming && !message.text) {
        answer = `<span class="typing" aria-label="正在生成"><i></i><i></i><i></i></span>`;
      } else {
        answer = `${escapeHtml(message.text)}${citations ? ` ${citations}` : ""}${message.streaming ? '<span class="stream-cursor" aria-hidden="true"></span>' : ""}`;
      }

      return `
        <article class="message assistant">
          <div class="assistant-avatar" aria-hidden="true">R</div>
          <div class="assistant-body">
            <div class="message-meta">
              <strong>RAG 助手</strong>
              <span class="message-model">${escapeHtml(modelLabel(message.model || state.model))}</span>
              <span class="meta-dot">·</span>
              <span>${escapeHtml(message.time || "刚刚")}</span>
              ${message.streaming ? '<span class="meta-dot">·</span><span>正在生成</span>' : ""}
            </div>
            <div class="answer ${message.insufficient ? "insufficient" : ""}">${answer}</div>
            ${message.meta ? `<div class="answer-meta">${escapeHtml(message.meta)}</div>` : ""}
            ${message.insufficient && !message.streaming ? `
              <div class="insufficient-actions">
                <button class="soft-button" type="button" data-answer-action="documents">补充文档</button>
                <button class="soft-button" type="button" data-answer-action="rephrase">换个问法</button>
              </div>` : ""}
            ${!message.streaming && message.text && !message.insufficient ? `<div class="answer-actions"><button class="copy-answer" type="button" data-index="${messageIndex}">复制回答与引用</button></div>` : ""}
          </div>
        </article>`;
    })
    .join("");

  requestAnimationFrame(scrollMessages);
}

function renderSources() {
  const sources = state.sources || [];
  $("#sourceCount").textContent = String(sources.length);
  $("#mobileSourceCount").textContent = String(sources.length);

  if (!sources.length) {
    sourcesListEl.innerHTML = `
      <div class="sources-empty">
        ${escapeHtml(state.sourcesNote || "回答后在这里查看引用来源。")}
      </div>`;
    return;
  }

  sourcesListEl.innerHTML = sources
    .map((source) => {
      const excerpt = source.kind === "table"
        ? renderTableExcerpt(source.excerpt)
        : `<p class="source-excerpt">${escapeHtml(source.excerpt)}</p>`;
      return `
        <article class="source-card ${state.activeSource === source.n ? "active" : ""}" data-source="${source.n}" tabindex="0">
          <div class="source-top">
            <span class="source-index">${source.n}</span>
            <span class="source-file" title="${escapeHtml(source.file)}">${escapeHtml(source.file)}</span>
            <span class="source-score" title="相关度（越高越相关）">${Number(source.score).toFixed(3)}</span>
          </div>
          <div class="source-meta">${pageLabel(source.page)} · ${escapeHtml(source.meta)}</div>
          ${excerpt}
          <button class="source-expand" type="button" data-expand="${source.n}">展开原文</button>
        </article>`;
    })
    .join("");
}

function splitTableRow(line) {
  let text = String(line || "").trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  const cells = [];
  let current = "";
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\\" && text[index + 1] === "|") {
      current += "|";
      index += 1;
      continue;
    }
    if (char === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
}

function renderTableCell(cell) {
  // 先转义，再把 Markdown 表格里用于换行的 <br> 还原成真正的换行。
  return escapeHtml(cell).replace(/&lt;br\s*\/?&gt;/gi, "<br>");
}

function renderTableExcerpt(markdown) {
  const lines = String(markdown || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|"));
  if (lines.length < 2) {
    return `<p class="source-excerpt">${escapeHtml(markdown)}</p>`;
  }

  const isSeparator = (cells) =>
    cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  const rows = lines
    .map(splitTableRow)
    .filter((cells) => !isSeparator(cells));
  if (!rows.length) {
    return `<p class="source-excerpt">${escapeHtml(markdown)}</p>`;
  }

  const [header, ...body] = rows;
  const head = header.map((cell) => `<th>${renderTableCell(cell)}</th>`).join("");
  const bodyRows = body
    .map((row) => `<tr>${row.map((cell) => `<td>${renderTableCell(cell)}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="source-table-wrap"><table class="source-table"><thead><tr>${head}</tr></thead><tbody>${bodyRows}</tbody></table></div>`;
}

function pageLabel(page) {
  const value = Number(page);
  return Number.isFinite(value) && value > 0 ? `第 ${value} 页` : "无页码";
}

function selectSource(n) {
  state.activeSource = n;
  $$(".source-card", sourcesListEl).forEach((card) => {
    card.classList.toggle("active", Number(card.dataset.source) === n);
  });
  $$(".citation", messagesEl).forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.source) === n);
  });

  const card = $(`.source-card[data-source="${n}"]`, sourcesListEl);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (window.innerWidth <= 900) openSources();
}

function toggleSourceExpanded(n) {
  const card = $(`.source-card[data-source="${n}"]`, sourcesListEl);
  if (!card) return;
  const expanded = card.classList.toggle("expanded");
  const button = $("[data-expand]", card);
  if (button) button.textContent = expanded ? "收起原文" : "展开原文";
}

function updateComposerState() {
  sendButton.classList.toggle("streaming", state.streaming);
  sendButton.setAttribute("aria-label", state.streaming ? "停止生成" : "发送问题");
  sendButton.title = state.streaming ? "停止生成" : "发送问题";
}

function finishStreaming(stopped = false) {
  if (state.streamTimer) {
    window.clearInterval(state.streamTimer);
    state.streamTimer = null;
  }
  if (stopped && state.abortController) {
    state.abortController.abort();
    state.abortController = null;
  }
  if (!state.streamAssistant) return;

  const assistant = state.streamAssistant;
  if (!assistant.text) assistant.text = stopped ? "（已停止）" : "（未返回内容）";
  assistant.streaming = false;
  assistant.citations = assistant.citations || [];
  assistant.meta = stopped
    ? (assistant.text === "（已停止）" ? "已停止 · 未生成回答" : "已停止 · 已保留当前内容")
    : (assistant.meta || "回答完成");
  state.streaming = false;
  state.streamAssistant = null;
  updateComposerState();
  renderConversation();
  if (stopped && !state.sources.length) state.sourcesNote = "已停止，未完成检索。";
  renderSources();
}

function parseSseBlock(block) {
  let event = "message";
  const data = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  });
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

function mapApiSource(source) {
  const name = source.file_name || "未命名文档";
  const ext = name.includes(".") ? name.split(".").pop().toUpperCase() : "文档";
  return {
    n: Number(source.n),
    file: name,
    page: source.page,
    score: Number(source.score) || 0,
    kind: source.kind || "text",
    meta: `${source.kind || "text"} · ${ext}`,
    excerpt: source.excerpt || "",
  };
}

function parseAnswerCitations(answer, sources) {
  const valid = new Set((sources || []).map((source) => source.n));
  const found = [...String(answer || "").matchAll(/\[(\d+)\]/g)]
    .map((match) => Number(match[1]))
    .filter((n) => valid.has(n));
  return [...new Set(found)];
}

function formatSeconds(ms) {
  return (Math.max(0, Number(ms) || 0) / 1000).toFixed(1);
}

function chatMeta(payload, sources) {
  const timings = payload.timings || {};
  if (!sources.length) return "未找到依据 · 未生成引用";
  return `检索 ${sources.length} 个片段 · 首字 ${formatSeconds(timings.ttft_ms)}s · 完整回答 ${formatSeconds(timings.total_ms)}s`;
}

function handleChatEvent(event, payload, assistant) {
  if (event === "sources") {
    const sources = (payload.sources || []).map(mapApiSource);
    assistant.sources = sources;
    state.sources = sources;
    state.sourcesNote = sources.length ? "" : "本轮没有找到可核对的引用。";
    state.activeSource = sources[0]?.n ?? null;
    renderSources();
    renderConversation();
    return;
  }

  if (event === "delta") {
    assistant.text += payload.text || "";
    assistant.meta = "正在生成回答…";
    renderConversation();
    return;
  }

  if (event === "done") {
    if (payload.session_id) state.sessionId = payload.session_id;
    if (typeof payload.answer === "string") assistant.text = payload.answer;
    if (payload.chat_mode) {
      // 问候/闲聊：服务端已确认不需要引用，清掉检索阶段先渲染出的来源卡片。
      assistant.sources = [];
      assistant.citations = [];
      assistant.insufficient = false;
      state.sources = [];
      state.sourcesNote = "闲聊不需要知识库引用。";
      state.activeSource = null;
      assistant.meta = payload.timings
        ? `闲聊 · 未使用知识库引用 · 完整回答 ${formatSeconds(payload.timings.total_ms)}s`
        : "闲聊 · 未使用知识库引用";
    } else if (payload.fallback) {
      // 兜底答案没有有效引用；检索阶段先展示的片段不应继续冒充“引用来源”。
      assistant.sources = [];
      assistant.citations = [];
      assistant.insufficient = true;
      state.sources = [];
      state.sourcesNote = "本轮未采用检索片段。";
      state.activeSource = null;
      assistant.meta = chatMeta(payload, []);
    } else {
      assistant.citations = parseAnswerCitations(assistant.text, assistant.sources);
      assistant.insufficient = Boolean(payload.fallback) || assistant.sources.length === 0;
      assistant.meta = chatMeta(payload, assistant.sources);
    }
    finishStreaming(false);
    return;
  }

  if (event === "error") {
    throw new Error(payload.message || "生成失败");
  }
}

async function streamChatFromApi(question, assistant) {
  const controller = new AbortController();
  state.abortController = controller;
  let sawDone = false;

  try {
    const response = await fetch("/api/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        question,
        ...(state.sessionId ? { session_id: state.sessionId } : {}),
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      // 错误响应体只能读一次：先取文本，再尝试按 JSON 解析。
      // 旧实现先 json() 再 text()，非 JSON 的 500 会二次读 body，
      // 结果把真实错误掩盖成 “body stream already read”。
      const raw = await response.text();
      let detail = raw.trim();
      try {
        const body = JSON.parse(raw);
        detail =
          body?.detail?.message ||
          (typeof body?.detail === "string" ? body.detail : "") ||
          body?.message ||
          raw.trim();
      } catch {
        // 保留原始文本（例如代理返回的 HTML 错误页）
      }
      throw new Error(`${response.status}${detail ? ` · ${detail.slice(0, 300)}` : ""}`);
    }
    if (!response.body) throw new Error("浏览器不支持流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (block) {
          const parsed = parseSseBlock(block);
          if (parsed) {
            if (parsed.event === "done") sawDone = true;
            handleChatEvent(parsed.event, parsed.data, assistant);
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }

    if (!sawDone && state.streamAssistant === assistant) {
      throw new Error("连接提前结束");
    }
  } catch (error) {
    if (controller.signal.aborted || state.streamAssistant !== assistant) return;
    assistant.streaming = false;
    assistant.text = `请求失败：${error.message}`;
    assistant.meta = "生成失败";
    assistant.citations = [];
    assistant.sources = [];
    state.streaming = false;
    state.streamAssistant = null;
    state.sources = [];
    state.sourcesNote = "生成失败，未获取引用。";
    updateComposerState();
    renderConversation();
    renderSources();
  } finally {
    if (state.abortController === controller) state.abortController = null;
  }
}

function sendMessage(rawText) {
  const text = String(rawText || "").trim();
  if (!text || state.streaming) return;

  if (!bothKeysConfigured()) {
    state.conversation.push({ role: "user", text, time: nowLabel() });
    state.conversation.push({ role: "error", text: missingKeyText(), time: nowLabel() });
    renderConversation();
    refreshKeyStateUI();
    showToast("请先配置 API Key", "error");
    return;
  }

  state.conversation.push({ role: "user", text, time: nowLabel() });
  state.conversation.push({
    role: "assistant",
    text: "",
    citations: [],
    sources: [],
    model: state.model,
    meta: "正在检索知识库…",
    time: nowLabel(),
    streaming: true,
  });
  const assistant = state.conversation[state.conversation.length - 1];
  state.streamAssistant = assistant;
  state.streaming = true;
  updateComposerState();
  state.sources = [];
  state.sourcesNote = "正在检索知识库…";
  state.activeSource = null;
  questionInput.value = "";
  autoResizeTextarea();
  renderConversation();
  renderSources();
  streamChatFromApi(text, assistant);
}

function clearChat() {
  if (state.streamTimer) window.clearInterval(state.streamTimer);
  if (state.abortController) state.abortController.abort();
  state.streamTimer = null;
  state.abortController = null;
  state.streaming = false;
  state.streamAssistant = null;
  state.sessionId = null;
  state.conversation = [];
  state.sources = [];
  state.sourcesNote = "回答后在这里查看引用来源。";
  state.activeSource = null;
  updateComposerState();
  renderConversation();
  renderSources();
  closeSources();
  showToast("会话已清空");
  questionInput.focus();
}

/* ---------- sources drawer ---------- */

function openSources() {
  sourcesPanel.classList.add("open");
  drawerBackdrop.hidden = false;
}

function closeSources() {
  sourcesPanel.classList.remove("open");
  drawerBackdrop.hidden = true;
}

/* ---------- documents ---------- */

function formatApiDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(state.lang === "en" ? "en-US" : "zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function mapApiDocument(item) {
  const name = item.file_name || "未命名文档";
  const ext = (item.doc_type || name.split(".").pop() || "txt").toLowerCase();
  const removed = item.status === "removed";
  return {
    id: item.id,
    name,
    ext,
    pages: item.pages || "—",
    chunks: item.chunks ?? "—",
    size: item.size,
    status: removed ? "removed" : "ready",
    removed,
    removedAt: item.removed_at || "",
    progress: 100,
    stage: removed ? "已移出" : "已入库",
    date: formatApiDate(item.created_at),
  };
}

async function loadDocuments() {
  state.docsLoading = true;
  state.docsError = "";
  try {
    const data = await apiJson("/documents?status=all&page_size=200");
    state.docs = (data.items || []).map(mapApiDocument);
  } catch (error) {
    state.docsError = error.message;
    if (!state.docs.length) state.docs = [];
  } finally {
    state.docsLoading = false;
    renderDocs();
    updateStats();
  }
}

function statusLabel(doc) {
  if (doc.removed) return "已移出";
  const labels = {
    queued: "排队中",
    running: "处理中",
    parsing: "解析中",
    splitting: "分块中",
    embedding: "嵌入中",
    writing: "写入中",
    ready: "已入库",
    skipped: "已跳过",
    failed: "失败",
    error: "失败",
  };
  return labels[doc.status] || "已入库";
}

function isProcessing(doc) {
  return ["queued", "running", "parsing", "splitting", "embedding", "writing"].includes(doc.status);
}

function progressMarkup(doc) {
  if (doc.status === "failed" || doc.status === "error") {
    return `<div class="row-error">${escapeHtml(doc.error || "处理失败")}</div>`;
  }
  if (!isProcessing(doc)) return "";
  const percent = Math.max(0, Math.min(100, Math.round(doc.progress || 0)));
  return `
    <div class="row-progress"><div class="row-progress-bar" style="width:${percent}%"></div></div>
    <div class="row-progress-text">${escapeHtml(doc.stage || statusLabel(doc))} · ${percent}%</div>`;
}

function actionMarkup(doc) {
  if (doc.removed) {
    return `
      <button class="row-action" type="button" data-action="restore" data-id="${escapeHtml(doc.id)}">恢复</button>
      <button class="row-action danger" type="button" data-action="permanent" data-id="${escapeHtml(doc.id)}">彻底删除</button>`;
  }
  if (doc.status === "error" || doc.status === "failed") {
    return `
      <button class="row-action primary" type="button" data-action="retry" data-id="${escapeHtml(doc.id)}">重试</button>
      <button class="row-action danger" type="button" data-action="remove" data-id="${escapeHtml(doc.id)}">移出</button>`;
  }
  if (isProcessing(doc)) {
    return `<span class="row-action-muted">处理中…</span>`;
  }
  return `<button class="row-action" type="button" data-action="remove" data-id="${escapeHtml(doc.id)}">移出</button>`;
}

function renderDocs() {
  const query = state.search.trim().toLowerCase();
  const isRemovedTab = state.docTab === "removed";
  const docs = state.docs
    .filter((doc) => (isRemovedTab ? doc.removed : !doc.removed))
    .filter((doc) => doc.name.toLowerCase().includes(query));

  $("#docsTitle").textContent = isRemovedTab ? "已移出文档" : "已入库文档";
  $("#removedNotice").hidden = !isRemovedTab;
  $("#uploadPanel").hidden = isRemovedTab;
  $$(".doc-tab").forEach((tab) => {
    const active = tab.dataset.docTab === state.docTab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });

  if (state.docsLoading && !state.docs.length) {
    docsTableBody.innerHTML = `<tr><td colspan="6" class="empty-table">正在读取文档列表…</td></tr>`;
    return;
  }

  if (state.docsError && !state.docs.length) {
    docsTableBody.innerHTML = `<tr><td colspan="6" class="empty-table">读取文档失败：${escapeHtml(state.docsError.slice(0, 300))}</td></tr>`;
    return;
  }

  if (!docs.length) {
    const message = isRemovedTab
      ? "回收站是空的。被移出的文档会出现在这里，可随时恢复。"
      : (query ? "没有匹配的文档。" : "知识库为空，先上传文档。");
    docsTableBody.innerHTML = `<tr><td colspan="6" class="empty-table">${message}</td></tr>`;
    return;
  }

  docsTableBody.innerHTML = docs
    .map((doc) => {
      const ext = doc.ext || "txt";
      const pages = doc.pages === "—" ? "—" : formatNumber(doc.pages);
      const chunks = doc.chunks === "—" ? "—" : formatNumber(doc.chunks);
      const badgeClass = doc.removed ? "removed" : doc.status;
      return `
        <tr>
          <td>
            <div class="doc-name">
              <span class="doc-type-icon ${escapeHtml(ext)}">${escapeHtml(ext.toUpperCase())}</span>
              <span title="${escapeHtml(doc.name)}"><strong>${escapeHtml(doc.name)}</strong></span>
            </div>
          </td>
          <td><span class="doc-type">${escapeHtml(ext)}</span></td>
          <td class="num">${pages}</td>
          <td class="num">${chunks}</td>
          <td>
            <span class="status-badge ${badgeClass}"><span class="dot"></span>${statusLabel(doc)}</span>
            ${progressMarkup(doc)}
          </td>
          <td class="right">${actionMarkup(doc)}</td>
        </tr>`;
    })
    .join("");
}

function removeDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc) return;

  openModal({
    title: "移出知识库？",
    body: `
      <p>将 <strong>${escapeHtml(doc.name)}</strong> 从检索结果和“已入库”列表中移出。</p>
      <p class="modal-warning">原文件和向量都会保留，可随时在“已移出”中恢复；不会删除原文件（<code>data/uploads</code>）。</p>`,
    confirmText: "移出",
    onConfirm: async () => {
      try {
        await apiJson(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
        doc.removed = true;
        doc.removedAt = nowLabel();
        doc.status = "removed";
        doc.stage = "已移出";
        state.docTab = "removed";
        renderDocs();
        updateStats();
        closeModal();
        showToast(`已移出知识库：${doc.name}，可在“已移出”中恢复或彻底删除`);
      } catch (error) {
        showToast(`移出失败：${error.message}`, "error");
      }
    },
  });
}

async function restoreDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc) return;
  try {
    await apiJson(`/documents/${encodeURIComponent(id)}/restore`, { method: "POST" });
    doc.removed = false;
    doc.removedAt = "";
    doc.status = "ready";
    doc.stage = "已入库";
    renderDocs();
    updateStats();
    showToast(`已恢复到知识库：${doc.name}`);
  } catch (error) {
    showToast(`恢复失败：${error.message}`, "error");
  }
}

function permanentlyDeleteDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc) return;

  openModal({
    title: "彻底删除文档？",
    body: `
      <p>将删除文档 <strong>${escapeHtml(doc.name)}</strong>：<strong>${formatNumber(doc.chunks || 0)}</strong> 个文本片段、对应向量，以及原文件（<code>data/uploads</code>）。</p>
      <p class="modal-warning">此操作不可恢复。如果只是暂时不想检索它，请使用“移出知识库”。</p>`,
    confirmText: "彻底删除",
    danger: true,
    onConfirm: async () => {
      try {
        await apiJson(`/documents/${encodeURIComponent(id)}/permanent`, { method: "DELETE" });
        state.docs = state.docs.filter((item) => item.id !== id);
        renderDocs();
        updateStats();
        closeModal();
        showToast(`已彻底删除：${doc.name}`);
      } catch (error) {
        showToast(`删除失败：${error.message}`, "error");
      }
    },
  });
}

function switchDocTab(tab) {
  state.docTab = tab === "removed" ? "removed" : "active";
  renderDocs();
  updateStats();
}

function retryDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc || !["error", "failed"].includes(doc.status)) return;
  if (!doc.file) {
    showToast("无法重试：浏览器没有保留原文件，请重新上传", "error");
    return;
  }
  state.docs = state.docs.filter((item) => item.id !== id);
  renderDocs();
  updateStats();
  showToast(`重新尝试：${doc.name}`);
  handleUpload([doc.file]);
}

function formatJobDuration(job) {
  const start = new Date(job.created_at).getTime();
  const end = new Date(job.updated_at).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return "刚刚";
  const seconds = Math.max(1, Math.round((end - start) / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

async function startRebuild() {
  closeModal();
  const button = $("#rebuildButton");
  button.disabled = true;

  try {
    const job = await apiJson("/index/rebuild", { method: "POST" });
    openModal({
      title: "正在重建索引",
      body: `
        <p>正在清空并重新解析、嵌入全部已入库文档。</p>
        <div class="progress-wrap">
          <div class="progress-track"><div class="progress-bar" id="rebuildBar"></div></div>
          <div class="progress-label"><span id="rebuildPct">0%</span><span id="rebuildTotal">准备中…</span></div>
        </div>
        <div class="progress-file" id="rebuildFile">等待任务启动…</div>
        <p class="modal-warning">重建期间问答可能不可用；会重新消耗嵌入额度。</p>`,
      showFooter: false,
    });
    pollRebuildJob(job.job_id, button);
  } catch (error) {
    button.disabled = false;
    showToast(`重建失败：${error.message}`, "error");
  }
}

async function pollRebuildJob(jobId, button) {
  try {
    const job = await apiJson(`/index/jobs/${encodeURIComponent(jobId)}`);
    const bar = $("#rebuildBar");
    if (!bar) {
      button.disabled = false;
      return;
    }

    const percent = Math.max(0, Math.min(100, Number(job.percent) || 0));
    bar.style.width = `${percent}%`;
    $("#rebuildPct").textContent = `${percent}%`;
    $("#rebuildTotal").textContent = job.total
      ? `${job.current || 0} / ${job.total} 份`
      : "准备中…";
    $("#rebuildFile").textContent = job.message || job.stage || "正在处理…";

    if (job.state === "ready") {
      closeModal();
      button.disabled = false;
      state.rebuilt = formatJobDuration(job);
      await loadDocuments();
      updateStats();
      showToast("索引重建完成");
      return;
    }
    if (job.state === "failed") {
      closeModal();
      button.disabled = false;
      await loadDocuments();
      showToast(`重建失败：${job.error || job.message || "未知错误"}`, "error");
      return;
    }

    window.setTimeout(() => pollRebuildJob(jobId, button), 700);
  } catch (error) {
    closeModal();
    button.disabled = false;
    showToast(`查询重建状态失败：${error.message}`, "error");
  }
}

async function pollUploadJob(doc) {
  if (!state.docs.some((item) => item.id === doc.id)) return;
  try {
    const job = await apiJson(`/documents/uploads/${encodeURIComponent(doc.jobId)}`);
    doc.status = job.state || "queued";
    doc.progress = Number(job.percent) || 0;
    doc.stage = job.stage || statusLabel(doc);
    if (job.unit === "chunk" && job.total) doc.chunks = job.total;
    if (job.error) doc.error = job.error;

    if (doc.status === "ready") {
      renderDocs();
      updateStats();
      showToast(`${doc.name} 已入库`);
      await loadDocuments();
      return;
    }
    if (doc.status === "failed" || doc.status === "skipped") {
      doc.status = "failed";
      doc.error = job.error || job.message || "处理失败";
      renderDocs();
      updateStats();
      showToast(`入库失败：${doc.name}`, "error");
      return;
    }

    renderDocs();
    updateStats();
    window.setTimeout(() => pollUploadJob(doc), 700);
  } catch (error) {
    doc.status = "failed";
    doc.error = error.message;
    renderDocs();
    updateStats();
    showToast(`查询入库状态失败：${error.message}`, "error");
  }
}

async function handleUpload(files) {
  if (!state.keys.dashscope.configured) {
    showKeyError(
      "无法上传文档",
      "上传需要阿里云百炼嵌入 API Key：文档要先生成 1024 维向量才能入库。请先在设置中配置百炼 Key。"
    );
    return;
  }

  const allowed = new Set(["pdf", "docx", "txt"]);
  const accepted = Array.from(files).filter((file) => {
    const ext = file.name.split(".").pop().toLowerCase();
    return allowed.has(ext);
  });

  if (!accepted.length) {
    showToast("只支持 PDF / DOCX / TXT 文件", "error");
    return;
  }

  const formData = new FormData();
  accepted.forEach((file) => formData.append("files", file, file.name));

  try {
    const result = await apiJson("/documents/upload", { method: "POST", body: formData });
    const jobs = result.jobs || [];
    jobs.forEach((job) => {
      const file = accepted.find((item) => item.name === job.file_name) || accepted[0];
      const ext = (file?.name.split(".").pop() || "txt").toLowerCase();
      const doc = {
        id: `job-${job.job_id}`,
        jobId: job.job_id,
        name: job.file_name || file?.name || "未命名文档",
        ext,
        pages: "—",
        chunks: "—",
        status: "queued",
        progress: 0,
        stage: "排队中",
        removed: false,
        removedAt: "",
        error: "",
        retried: false,
        file,
        date: nowLabel(),
      };
      state.docs.unshift(doc);
      pollUploadJob(doc);
    });
    state.docTab = "active";
    renderDocs();
    updateStats();
    showToast(`已加入解析队列：${accepted.length} 个文件`);
  } catch (error) {
    showToast(`上传失败：${error.message}`, "error");
  }
}

/* ---------- modal / toast ---------- */

function openModal({ title, body, confirmText = "确认", cancelText = "取消", danger = false, showFooter = true, onConfirm = null }) {
  modalTitle.textContent = title;
  modalBody.innerHTML = body;
  modalFooter.innerHTML = showFooter
    ? `<button class="modal-button" type="button" id="modalCancel">${escapeHtml(cancelText)}</button>
       <button class="modal-button ${danger ? "danger" : "primary"}" type="button" id="modalConfirm">${escapeHtml(confirmText)}</button>`
    : "";

  modalConfirmHandler = onConfirm;
  modalBackdrop.hidden = false;

  if (showFooter) {
    $("#modalCancel").addEventListener("click", closeModal);
    $("#modalConfirm").addEventListener("click", () => {
      if (modalConfirmHandler) modalConfirmHandler();
    });
  }
}

function closeModal() {
  modalBackdrop.hidden = true;
  modalConfirmHandler = null;
  const rebuildButton = $("#rebuildButton");
  if (rebuildButton) rebuildButton.disabled = false;
}

function showToast(message, type = "success") {
  toastEl.textContent = message;
  toastEl.classList.toggle("error", type === "error");
  toastEl.hidden = false;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastEl.hidden = true;
  }, 2800);
}

async function copyMessage(index) {
  const message = state.conversation[index];
  if (!message || !message.text) return;
  const refs = (message.sources || [])
    .map((source, i) => `[${i + 1}] ${source.file} · ${pageLabel(source.page)} · ${source.meta}\n${source.excerpt}`)
    .join("\n\n");
  const text = refs ? `${message.text}\n\n引用来源：\n${refs}` : message.text;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    showToast(refs ? "回答与引用已复制" : "回答已复制");
  } catch {
    showToast("复制失败，请手动选择文本", "error");
  }
}

/* ---------- textarea ---------- */

function autoResizeTextarea() {
  questionInput.style.height = "auto";
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, 140)}px`;
}

/* ---------- events ---------- */

$$(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
});

function switchView(view) {
  state.view = view;
  document.body.dataset.view = view;
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `${view}View`));
  $$(".view-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  if (view === "documents") {
    renderDocs();
    updateStats();
    closeSources();
  } else if (view === "settings") {
    renderSettings();
    closeSources();
  } else if (view === "setup") {
    renderSetup();
    closeSources();
  } else {
    refreshKeyStateUI();
    requestAnimationFrame(scrollMessages);
  }
}

messagesEl.addEventListener("click", (event) => {
  const answerAction = event.target.closest("[data-answer-action]");
  if (answerAction) {
    if (answerAction.dataset.answerAction === "documents") {
      switchView("documents");
    } else {
      questionInput.focus();
      questionInput.value = "";
    }
    return;
  }
  const copy = event.target.closest(".copy-answer");
  if (copy) {
    copyMessage(Number(copy.dataset.index));
    return;
  }
  const goSettings = event.target.closest(".go-settings");
  if (goSettings) {
    switchView("settings");
    return;
  }
  const citation = event.target.closest(".citation");
  if (citation) {
    selectSource(Number(citation.dataset.source));
    return;
  }
});

sourcesListEl.addEventListener("click", (event) => {
  const expand = event.target.closest("[data-expand]");
  if (expand) {
    event.stopPropagation();
    toggleSourceExpanded(Number(expand.dataset.expand));
    return;
  }
  const card = event.target.closest(".source-card");
  if (card) selectSource(Number(card.dataset.source));
});

sourcesListEl.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest(".source-card");
  if (!card) return;
  event.preventDefault();
  selectSource(Number(card.dataset.source));
});

sendButton.addEventListener("click", () => {
  if (state.streaming) {
    finishStreaming(true);
    return;
  }
  sendMessage(questionInput.value);
});

questionInput.addEventListener("input", autoResizeTextarea);
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!state.streaming) sendMessage(questionInput.value);
  }
});

$("#clearChatTop").addEventListener("click", clearChat);
$("#settingsButton").addEventListener("click", () => switchView("settings"));
$("#keyBannerAction").addEventListener("click", () => switchView("settings"));
$("#sourcesToggle").addEventListener("click", openSources);
$("#closeSources").addEventListener("click", closeSources);
drawerBackdrop.addEventListener("click", closeSources);

["deepseek", "dashscope", "openai", "anthropic"].forEach((provider) => {
  $(`#${provider}Edit`).addEventListener("click", () => openKeyEditor(provider));
  $(`#${provider}Test`).addEventListener("click", () => testKey(provider));
  $(`#${provider}Clear`).addEventListener("click", () => clearKey(provider));
});
$("#restartSetup").addEventListener("click", restartSetup);
$("#modelSelect").addEventListener("change", (event) => handleModelChange(event.target.value));
$("#addProviderButton").addEventListener("click", () => {
  showToast("更多 Provider 会在正式版接入");
});

docsTableBody.addEventListener("click", (event) => {
  const action = event.target.closest(".row-action");
  if (!action) return;
  const id = action.dataset.id;
  if (action.dataset.action === "remove") removeDoc(id);
  else if (action.dataset.action === "restore") restoreDoc(id);
  else if (action.dataset.action === "permanent") permanentlyDeleteDoc(id);
  else if (action.dataset.action === "retry") retryDoc(id);
});

$$(".doc-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchDocTab(tab.dataset.docTab));
});

$("#docSearch").addEventListener("input", (event) => {
  state.search = event.target.value;
  renderDocs();
});

fileInput.addEventListener("change", () => {
  handleUpload(fileInput.files);
  fileInput.value = "";
});

$("#rebuildButton").addEventListener("click", () => {
  const activeDocs = state.docs.filter((doc) => !doc.removed);
  const total = activeDocs.reduce((sum, doc) => sum + (Number(doc.chunks) || 0), 0);
  openModal({
    title: "重建整个索引？",
    body: `
      <p>这会清空当前索引并重新解析、嵌入 <strong>${activeDocs.length}</strong> 份已入库文档（共<strong>${formatNumber(total)}</strong> 个文本片段）。</p>
      <p class="modal-warning">重建期间问答可能不可用；会重新消耗嵌入额度。</p>`,
    confirmText: "开始重建",
    danger: true,
    onConfirm: startRebuild,
  });
});

$("#modalClose").addEventListener("click", closeModal);
modalBackdrop.addEventListener("click", (event) => {
  if (event.target === modalBackdrop) closeModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeModal();
    closeSources();
  }
});

/* ---------- boot ---------- */

window.addEventListener("i18n:change", (event) => {
  state.lang = event.detail?.lang === "en" ? "en" : "zh";
  updateStats();
});

renderConversation();
renderSources();
renderDocs();
renderSettings();
updateStats();
updateComposerState();
autoResizeTextarea();
refreshKeyStateUI();
async function boot() {
  await loadSecrets();
  await loadDocuments();
  const initialView = new URLSearchParams(window.location.search).get("view");
  if (initialView === "documents") switchView("documents");
  else if (initialView === "settings") switchView("settings");
  else if (initialView === "setup") switchView("setup");
  else if (!bothKeysConfigured()) switchView("setup");
  else switchView("chat");
}

boot();
