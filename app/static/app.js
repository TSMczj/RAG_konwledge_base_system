/* ============================================================
   RAG 知识库 UI
   聊天页已接 FastAPI /api/v1/chat SSE；文档页与设置页仍为原型 mock。
   ============================================================ */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const MODELS = {
  "deepseek-v4-flash": { provider: "deepseek", label: "DeepSeek V4 Flash" },
  "gpt-4.1-mini": { provider: "openai", label: "GPT-4.1 mini" },
  "claude-sonnet-4": { provider: "anthropic", label: "Claude Sonnet 4" },
};
const API_MODELS = new Set(["deepseek-v4-flash"]);

const initialSources = [
  {
    n: 1,
    file: "attention is all you need.pdf",
    page: 5,
    score: 0.648,
    meta: "text · 15 页 PDF",
    excerpt:
      "The Transformer uses multi-head attention in three different ways: in encoder-decoder attention layers, the queries come from the previous decoder layer and the keys and values come from the encoder output.",
  },
  {
    n: 2,
    file: "attention is all you need.pdf",
    page: 2,
    score: 0.586,
    meta: "text · 15 页 PDF",
    excerpt:
      "The Transformer follows this overall architecture using stacked self-attention and point-wise, fully connected layers for both the encoder and decoder.",
  },
];

const demoDocs = [
  { id: "doc-01", name: "10_项目说明.txt", ext: "txt", pages: "—", chunks: 13, status: "ready", date: "17:50" },
  { id: "doc-02", name: "2.技术参数.docx", ext: "docx", pages: "—", chunks: 6, status: "ready", date: "17:50" },
  { id: "doc-03", name: "attention is all you need.pdf", ext: "pdf", pages: 15, chunks: 102, status: "ready", date: "17:50" },
  { id: "doc-04", name: "bert.pdf", ext: "pdf", pages: 16, chunks: 180, status: "ready", date: "17:50" },
  { id: "doc-05", name: "gbk.txt", ext: "txt", pages: "—", chunks: 1, status: "ready", date: "17:50" },
  { id: "doc-06", name: "handbook-872p.docx", ext: "docx", pages: "—", chunks: 2387, status: "ready", date: "17:50" },
  { id: "doc-07", name: "lm358.pdf", ext: "pdf", pages: 68, chunks: 237, status: "ready", date: "17:52" },
  { id: "doc-08", name: "nasdaq-tsla-2025-10K-25570807.pdf", ext: "pdf", pages: 113, chunks: 1063, status: "ready", date: "17:54" },
  { id: "doc-09", name: "UTF-8.txt", ext: "txt", pages: "—", chunks: 1, status: "ready", date: "17:55" },
  { id: "doc-10", name: "中华人民共和国个人信息保护法.pdf", ext: "pdf", pages: 21, chunks: 31, status: "ready", date: "17:55" },
];

const state = {
  view: "chat",
  lang: window.I18n?.getLanguage?.() || "zh",
  model: localStorage.getItem("rag-ui-model") || "deepseek-v4-flash",
  streaming: false,
  abortController: null,
  conversation: [],
  sources: [],
  sourcesNote: "回答后在这里查看引用来源。",
  activeSource: null,
  docs: demoDocs.map((doc) => ({
    ...doc,
    removed: false,
    removedAt: "",
    progress: 100,
    stage: "已入库",
  })),
  docTab: "active",
  keys: {
    deepseek: {
      configured: true,
      masked: "sk-••••••••••••••••••4f2a",
      source: "本机保存",
      updatedAt: "2026-10-25 01:20",
    },
    dashscope: {
      configured: true,
      masked: "sk-••••••••••••••••••7b3c",
      source: "本机保存",
      updatedAt: "2026-10-25 01:20",
    },
    openai: {
      configured: true,
      masked: "sk-••••••••••••••••••9c1d",
      source: "演示配置",
      updatedAt: "2026-10-25 14:58",
    },
    anthropic: {
      configured: true,
      masked: "sk-••••••••••••••••••3a7e",
      source: "演示配置",
      updatedAt: "2026-10-25 14:58",
    },
  },
  setupStep: 1,
  setupTested: false,
  search: "",
  rebuilt: "6m 09s",
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

    status.textContent = config.configured ? "已配置" : "未配置";
    status.className = `key-status ${config.configured ? "configured" : "unconfigured"}`;
    mask.textContent = config.configured ? config.masked : "未配置";
    meta.textContent = config.configured
      ? `${config.source} · 更新于 ${config.updatedAt}`
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
    onConfirm: () => {
      const input = $("#keyEditorInput");
      const value = input ? input.value.trim() : "";
      if (!value) {
        showToast("请输入 API Key", "error");
        return;
      }
      state.keys[provider] = {
        configured: true,
        masked: maskKey(value),
        source: "本机保存",
        updatedAt: nowLabel(),
      };
      renderSettings();
      refreshKeyStateUI();
      closeModal();
      showToast(`${label} Key 已保存`);
    },
  });
}

function testKey(provider) {
  if (!state.keys[provider].configured) return;
  const button = $(`#${provider}Test`);
  if (!button) return;
  button.disabled = true;
  button.textContent = "测试中…";
  window.setTimeout(() => {
    button.disabled = false;
    button.textContent = "测试连接";
    showToast(`${providerLabel(provider)} 连接正常`);
  }, 720);
}

function clearKey(provider) {
  const label = providerLabel(provider);
  openModal({
    title: `清除 ${label} Key？`,
    body: `<p>清除后，依赖这个 Key 的功能会立即停止工作；可以随时重新配置。</p>
           <p class="modal-warning">原型中只会清除当前页面的模拟状态，不会操作真实密钥。</p>`,
    confirmText: "清除",
    danger: true,
    onConfirm: () => {
      state.keys[provider] = {
        configured: false,
        masked: "",
        source: "",
        updatedAt: "",
      };
      renderSettings();
      refreshKeyStateUI();
      closeModal();
      showToast(`${label} Key 已清除`);
    },
  });
}

function restartSetup() {
  openModal({
    title: "重新运行首次引导？",
    body: `<p>会清除当前模拟的 API Key 配置，并回到首次启动引导。</p>
           <p class="modal-warning">真实版本中，这不会删除你的真实 Key，除非你选择清除。</p>`,
    confirmText: "重新引导",
    danger: true,
    onConfirm: () => {
      state.keys.deepseek = { configured: false, masked: "", source: "", updatedAt: "" };
      state.keys.dashscope = { configured: false, masked: "", source: "", updatedAt: "" };
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
      ${[1, 2, 3, 4].map((item) => `<span class="${item <= step ? "done" : ""} ${item === step ? "current" : ""}">${item}</span>`).join("")}
      <em>第 ${step} / 4 步</em>
    </div>`;
}

function renderSetup() {
  const card = $("#setupCard");
  if (!card) return;
  const step = state.setupStep;

  if (step === 1) {
    card.innerHTML = `
      <div class="setup-intro">
        <div class="setup-hero-window" aria-hidden="true">
          <span class="setup-window-caption">LOCAL ARCHIVE</span>
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
        <h2>配置模型 api-key</h2>
        <p class="setup-lead">这个知识库需要两个 Key 才能完成问答。它们只保存在本机，不会上传到我们的服务器。</p>
      </div>
      <ul class="setup-points">
        <li><strong>DeepSeek</strong><span>负责生成最终回答</span></li>
        <li><strong>阿里云百炼</strong><span>负责把文档和问题变成向量</span></li>
      </ul>
      <div class="setup-note">保存后只显示末 4 位，例如 <code>sk-••••••••••••••••••4f2a</code>；不会显示完整内容。</div>
      <div class="setup-actions">
        <button class="primary-button" id="setupStart" type="button">开始配置</button>
        <button class="soft-button" id="setupSkip" type="button">跳过，先只读浏览</button>
      </div>`;
    $("#setupStart").addEventListener("click", () => {
      state.setupStep = 2;
      renderSetup();
    });
    $("#setupSkip").addEventListener("click", skipSetup);
    return;
  }

  if (step === 2 || step === 3) {
    const provider = step === 2 ? "deepseek" : "dashscope";
    const label = providerLabel(provider);
    const configured = state.keys[provider].configured;
    card.innerHTML = `
      ${setupProgress(step)}
      <span class="eyebrow">STEP ${step}</span>
      <h2>配置 ${label} Key</h2>
      <p class="setup-lead">${provider === "deepseek"
        ? "用于调用 deepseek-v4-flash 生成回答。"
        : "用于调用 text-embedding-v4 生成 1024 维向量；上传和检索都需要它。"}</p>
      ${configured ? `<div class="setup-existing">已配置：<code>${escapeHtml(state.keys[provider].masked)}</code>，留空则保留当前 Key。</div>` : ""}
      <label class="setup-field" for="setupKeyInput">${label} API Key
        <input id="setupKeyInput" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 ${label} API Key">
      </label>
      <div class="setup-error" id="setupError" hidden></div>
      <div class="setup-actions">
        <button class="soft-button" id="setupBack" type="button">上一步</button>
        <button class="primary-button" id="setupNext" type="button">保存并继续</button>
      </div>`;
    $("#setupBack").addEventListener("click", () => {
      state.setupStep -= 1;
      renderSetup();
    });
    $("#setupNext").addEventListener("click", () => {
      const input = $("#setupKeyInput");
      const value = input ? input.value.trim() : "";
      if (!value && !state.keys[provider].configured) {
        const error = $("#setupError");
        if (error) {
          error.textContent = "请填写 Key，或点击“跳过，先只读浏览”。";
          error.hidden = false;
        }
        return;
      }
      if (value) {
        state.keys[provider] = {
          configured: true,
          masked: maskKey(value),
          source: "本机保存",
          updatedAt: nowLabel(),
        };
      }
      state.setupStep += 1;
      renderSetup();
    });
    return;
  }

  const ready = bothKeysConfigured();
  card.innerHTML = `
    ${setupProgress(4)}
    <span class="eyebrow">STEP 4</span>
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
    state.setupStep = 3;
    renderSetup();
  });
  $("#setupTest").addEventListener("click", () => {
    const button = $("#setupTest");
    if (!button) return;
    button.disabled = true;
    button.textContent = "测试中…";
    window.setTimeout(() => {
      state.setupTested = true;
      renderSetup();
      showToast("两个 Key 连接正常");
    }, 720);
  });
  $("#setupFinish").addEventListener("click", () => {
    if (!bothKeysConfigured()) {
      showToast("还有 Key 未配置，可先跳过", "error");
      return;
    }
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
        <img class="empty-illustration" src="/static/assets/hero-books.jpg" alt="打开的书本通向知识天空">
        <h3>从你的知识库开始提问</h3>
        <p>已索引 ${activeDocs.length} 份文档、${formatNumber(totalChunks)} 个文本片段。回答会带上引用，点击 [n] 可以展开原文核对。</p>
        <div class="suggestion-grid">
          <button class="suggestion" type="button" data-prompt="LM358 的电源电压范围是多少？">LM358 的电源电压范围是多少？</button>
          <button class="suggestion" type="button" data-prompt="个人信息保护法对处理个人信息有哪些要求？">个人信息保护法对处理个人信息有哪些要求？</button>
          <button class="suggestion" type="button" data-prompt="BERT 的预训练任务是什么？">BERT 的预训练任务是什么？</button>
          <button class="suggestion" type="button" data-prompt="明天杭州天气怎么样？">明天杭州天气怎么样？</button>
        </div>
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
    .map(
      (source) => `
        <article class="source-card ${state.activeSource === source.n ? "active" : ""}" data-source="${source.n}" tabindex="0">
          <div class="source-top">
            <span class="source-index">${source.n}</span>
            <span class="source-file" title="${escapeHtml(source.file)}">${escapeHtml(source.file)}</span>
            <span class="source-score" title="相关度（越高越相关）">${Number(source.score).toFixed(3)}</span>
          </div>
          <div class="source-meta">${pageLabel(source.page)} · ${escapeHtml(source.meta)}</div>
          <p class="source-excerpt">${escapeHtml(source.excerpt)}</p>
          <button class="source-expand" type="button" data-expand="${source.n}">展开原文</button>
        </article>`
    )
    .join("");
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

/* ---------- mock replies ---------- */

function getMockReply(prompt) {
  const text = prompt.toLowerCase();

  if (/天气|预报|明天.*(股价|新闻)|库外|无关问题|weather|forecast/i.test(text)) {
    return {
      text: "当前文档中没有找到足够依据。这个问题可能超出知识库范围，建议补充相关文档，或换一个问法。",
      citations: [],
      sources: [],
      insufficient: true,
      meta: "未找到依据 · 未生成引用",
    };
  }

  if (text.includes("lm358") || text.includes("电源电压") || text.includes("supply voltage") || text.includes("power supply")) {
    return {
      text:
        "LM358 的电源电压范围取决于具体型号：LM358B / LM2904B 为 3 V 到 36 V，LM358 / LM2904 为 3 V 到 30 V，LM2904V 为 3 V 到 26 V。数据手册的规格表同时给出了最小值、最大值和单位，建议以对应型号那一行为准。",
      citations: [1, 2],
      sources: [
        {
          n: 1,
          file: "lm358.pdf",
          page: 5,
          score: 0.234,
          meta: "table · 68 页 PDF",
          excerpt: "|  |  | MIN | MAX | UNIT | ... | V Supply voltage, V+ ... 3 | 36 | V |",
        },
        {
          n: 2,
          file: "lm358.pdf",
          page: 4,
          score: 0.258,
          meta: "table · 68 页 PDF",
          excerpt: "Supply voltage, V+ ... LM358B / LM2904B: 3 V to 36 V; LM358 / LM2904: 3 V to 30 V.",
        },
      ],
      meta: "检索 5 个片段 · 首字 0.31s · 完整回答 1.8s",
    };
  }

  if (text.includes("个人信息") || text.includes("保护法") || text.includes("personal information") || text.includes("privacy law")) {
    return {
      text:
        "《个人信息保护法》要求处理个人信息遵循合法、正当、必要和诚信原则，并且应当与处理目的直接相关、采取对个人权益影响最小的方式。收集个人信息还应限于实现处理目的的最小范围，不得过度收集。",
      citations: [1, 2],
      sources: [
        {
          n: 1,
          file: "中华人民共和国个人信息保护法.pdf",
          page: 3,
          score: 0.203,
          meta: "text · 21 页 PDF",
          excerpt:
            "处理个人信息应当遵循合法、正当、必要和诚信原则，不得通过误导、欺诈、胁迫等方式处理个人信息。",
        },
        {
          n: 2,
          file: "中华人民共和国个人信息保护法.pdf",
          page: 4,
          score: 0.205,
          meta: "text · 21 页 PDF",
          excerpt:
            "收集个人信息，应当限于实现处理目的的最小范围，不得过度收集个人信息。",
        },
      ],
      meta: "检索 5 个片段 · 首字 0.28s · 完整回答 1.6s",
    };
  }

  if (text.includes("bert") || text.includes("预训练") || text.includes("pre-training") || text.includes("pretraining")) {
    return {
      text:
        "BERT 使用两个预训练任务：掩码语言模型（Masked LM）随机遮住部分 token，让模型根据左右上下文预测；下一句预测（NSP）判断两个句子是否连续。论文还通过消融实验说明这两个任务对最终效果都有贡献。",
      citations: [1, 2],
      sources: [
        {
          n: 1,
          file: "bert.pdf",
          page: 8,
          score: 0.341,
          meta: "text · 16 页 PDF",
          excerpt:
            "We demonstrate the importance of our pre-training tasks by comparing against several ablations.",
        },
        {
          n: 2,
          file: "bert.pdf",
          page: 2,
          score: 0.373,
          meta: "text · 16 页 PDF",
          excerpt:
            "BERT is designed to pre-train deep bidirectional representations from unlabeled text by jointly conditioning on both left and right context.",
        },
      ],
      meta: "检索 5 个片段 · 首字 0.35s · 完整回答 2.0s",
    };
  }

  return {
    text:
      "Transformer 采用编码器—解码器堆叠结构。最核心的是多头自注意力机制：它让模型在同一层内同时关注不同位置的信息，并通过残差连接和层归一化稳定训练。与循环网络不同，它不依赖序列顺序递归计算，因此更适合并行训练。",
    citations: [1, 2],
    sources: initialSources,
    meta: "检索 5 个片段 · 首字 0.42s · 完整回答 2.1s",
  };
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
    if (typeof payload.answer === "string") assistant.text = payload.answer;
    assistant.citations = parseAnswerCitations(assistant.text, assistant.sources);
    assistant.insufficient = Boolean(payload.fallback) || assistant.sources.length === 0;
    assistant.meta = chatMeta(payload, assistant.sources);
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
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        detail = body?.detail?.message || body?.detail || "";
      } catch {
        detail = await response.text();
      }
      throw new Error(`${response.status}${detail ? ` · ${detail}` : ""}`);
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

function statusLabel(doc) {
  if (doc.removed) return "已移出";
  const labels = {
    queued: "排队中",
    parsing: "解析中",
    splitting: "分块中",
    embedding: "嵌入中",
    writing: "写入中",
    ready: "已入库",
    error: "失败",
  };
  return labels[doc.status] || "已入库";
}

function isProcessing(doc) {
  return ["queued", "parsing", "splitting", "embedding", "writing"].includes(doc.status);
}

function progressMarkup(doc) {
  if (doc.status === "error") {
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
  if (doc.status === "error") {
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
    onConfirm: () => {
      doc.removed = true;
      doc.removedAt = nowLabel();
      state.docTab = "removed";
      renderDocs();
      updateStats();
      closeModal();
      showToast(`已移出知识库：${doc.name}，可在“已移出”中恢复或彻底删除`);
    },
  });
}

function restoreDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc) return;
  doc.removed = false;
  doc.removedAt = "";
  renderDocs();
  updateStats();
  showToast(`已恢复到知识库：${doc.name}`);
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
    onConfirm: () => {
      state.docs = state.docs.filter((item) => item.id !== id);
      renderDocs();
      updateStats();
      closeModal();
      showToast(`已彻底删除：${doc.name}`);
    },
  });
}

function switchDocTab(tab) {
  state.docTab = tab === "removed" ? "removed" : "active";
  renderDocs();
  updateStats();
}

function simulateUpload(doc, file) {
  let progress = 0;
  const isPdf = doc.ext === "pdf";
  const totalPages = isPdf ? 68 : 12;
  const totalBatches = Math.max(4, Math.min(36, Math.ceil((file.size || 24000) / 24000)));
  const willFail = !doc.retried && (
    file.size === 0 || /扫描件|fail|corrupt|scan/i.test(file.name || "")
  );

  const timer = window.setInterval(() => {
    progress = Math.min(100, progress + 1.4 + Math.random() * 2.8);

    if (willFail && progress >= 52) {
      window.clearInterval(timer);
      doc.status = "error";
      doc.progress = 52;
      doc.stage = "解析失败";
      doc.error = "未检测到可入库文本，可能是扫描件或文件损坏";
      doc.chunks = "—";
      renderDocs();
      updateStats();
      showToast(`入库失败：${file.name}，可点击重试`, "error");
      return;
    }

    if (progress >= 100) {
      window.clearInterval(timer);
      doc.status = "ready";
      doc.progress = 100;
      doc.stage = "已入库";
      doc.error = "";
      doc.pages = isPdf ? totalPages : "—";
      doc.chunks = Math.max(1, Math.min(120, Math.round((file.size || 24000) / 18000) || 1));
      renderDocs();
      updateStats();
      showToast(`${file.name} 已入库`);
      return;
    }

    doc.progress = Math.round(progress);
    if (progress < 8) {
      doc.status = "queued";
      doc.stage = "排队中";
    } else if (progress < 34) {
      doc.status = "parsing";
      const page = Math.max(1, Math.ceil(((progress - 8) / 26) * totalPages));
      doc.stage = `解析中 · 第 ${page} / ${totalPages} 页`;
    } else if (progress < 48) {
      doc.status = "splitting";
      const chunks = Math.max(1, Math.ceil(((progress - 34) / 14) * 24));
      doc.stage = `分块中 · 已生成 ${chunks} / 24 个文本片段`;
    } else if (progress < 92) {
      doc.status = "embedding";
      const batch = Math.max(1, Math.ceil(((progress - 48) / 44) * totalBatches));
      doc.stage = `嵌入中 · 第 ${batch} / ${totalBatches} 批`;
    } else {
      doc.status = "writing";
      doc.stage = "写入中 · 正在写入向量库";
    }
    renderDocs();
  }, 120);
}

function retryDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  if (!doc || doc.status !== "error") return;
  doc.retried = true;
  doc.status = "queued";
  doc.progress = 0;
  doc.stage = "重新排队";
  doc.error = "";
  doc.chunks = "—";
  renderDocs();
  showToast(`重新尝试：${doc.name}`);
  simulateUpload(doc, doc.file || { size: 24000, name: doc.name });
}

function startRebuild() {
  closeModal();
  const button = $("#rebuildButton");
  button.disabled = true;
  const activeDocs = state.docs.filter((doc) => !doc.removed);
  const total = Math.max(activeDocs.length, 1);
  let value = 0;

  openModal({
    title: "正在重建索引",
    body: `
      <p>会清空当前可检索索引，并重新解析、嵌入全部已入库文档。</p>
      <div class="progress-wrap">
        <div class="progress-track"><div class="progress-bar" id="rebuildBar"></div></div>
        <div class="progress-label"><span id="rebuildPct">0%</span><span id="rebuildTotal">0 / ${total} 份</span></div>
      </div>
      <div class="progress-file" id="rebuildFile">准备中…</div>
      <p class="modal-warning">原型演示：这里模拟约 6 分钟的重建过程，不会真的调用嵌入接口。</p>`,
    showFooter: false,
  });

  const timer = window.setInterval(() => {
    value = Math.min(100, value + 4 + Math.random() * 5);
    const bar = $("#rebuildBar");
    const pct = $("#rebuildPct");
    const totalEl = $("#rebuildTotal");
    const fileEl = $("#rebuildFile");
    if (!bar) {
      window.clearInterval(timer);
      return;
    }
    bar.style.width = `${value}%`;
    pct.textContent = `${Math.round(value)}%`;
    const done = Math.min(total, Math.ceil((value / 100) * total));
    totalEl.textContent = `${done} / ${total} 份`;
    const current = activeDocs[Math.min(done, total - 1)];
    fileEl.textContent = current ? `正在处理：${current.name}` : "正在写入向量库…";

    if (value >= 100) {
      window.clearInterval(timer);
      window.setTimeout(() => {
        closeModal();
        button.disabled = false;
        state.rebuilt = "刚刚";
        updateStats();
        showToast("索引重建完成");
      }, 420);
    }
  }, 280);
}

function handleUpload(files) {
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

  accepted.forEach((file, index) => {
    const ext = file.name.split(".").pop().toLowerCase();
    const doc = {
      id: `upload-${Date.now()}-${index}`,
      name: file.name,
      ext,
      pages: "—",
      chunks: "—",
      status: "queued",
      progress: 0,
      stage: "排队中",
      removed: false,
      removedAt: "",
      retried: false,
      file,
      date: nowLabel(),
    };
    state.docs.unshift(doc);
    state.docTab = "active";
    renderDocs();
    updateStats();
    showToast(`已加入解析队列：${file.name}`);
    simulateUpload(doc, file);
  });
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
  const suggestion = event.target.closest(".suggestion");
  if (suggestion) sendMessage(suggestion.dataset.prompt);
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
      <p class="modal-warning">重建期间问答可能不可用；会重新消耗嵌入额度。原型中会模拟约 6 分钟。</p>`,
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

const initialView = new URLSearchParams(window.location.search).get("view");
if (initialView === "documents") switchView("documents");
else if (initialView === "settings") switchView("settings");
else if (initialView === "setup") switchView("setup");
else if (!bothKeysConfigured()) switchView("setup");
else switchView("chat");
