/* ============================================================
   Prototype-only i18n layer (zh-CN / en)
   说明：这是静态原型用的轻量翻译层，正式模板化时应改为服务端 i18n
   或 data-i18n + 明确 key，不要把这个 DOM 遍历方案带进生产。
   ============================================================ */

(() => {
  "use strict";

  const STORAGE_KEY = "rag-ui-lang";
  const EN = {
    // Brand / navigation
    "RAG 知识库": "RAG Knowledge Base",
    "UI 原型": "UI Prototype",
    "本地版": "Local",
    "万卷为门，开卷见天。": "Books are the gate; reading opens the sky.",
    "对话": "Chat",
    "文档管理": "Documents",
    "索引就绪": "Index ready",
    "文本片段": "passages",
    "清空会话": "New chat",
    "API 与设置": "API & Settings",
    "打开设置": "Open settings",
    "当前本地索引状态": "Current local index status",
    "主导航": "Main navigation",
    "对话消息": "Conversation",
    "输入问题": "Question input",
    "发送问题": "Send question",
    "关闭来源面板": "Close sources",
    "相关度（越高越相关）": "Relevance (higher is better)",
    "文档状态": "Document status",
    "上传文档": "Upload documents",
    "搜索文档": "Search documents",
    "设置": "Settings",
    "首次配置": "First-time setup",

    // Chat
    "知识库对话": "Knowledge Base Chat",
    "来源": "Sources",
    "流式输出": "Streaming",
    "尚未配置 API Key": "API keys not configured",
    "聊天需要 DeepSeek 与百炼嵌入 Key；配置后即可使用。": "Chat requires DeepSeek and DashScope embedding keys.",
    "去配置": "Configure",
    "问点什么…… 例如：LM358 的电源电压范围是多少？": "Ask anything… e.g. What is the supply voltage of the LM358?",
    "Enter 发送 · Shift + Enter 换行": "Enter to send · Shift + Enter for a new line",
    "回答由本地知识库与 DeepSeek 生成，请核对引用原文。": "Answers are generated from your local knowledge base and DeepSeek. Verify the cited text.",
    "回答由本地知识库与所选模型生成，请核对引用原文。": "Answers are generated from your local knowledge base and the selected model. Verify the cited text.",
    "引用来源": "Sources",
    "点击回答中的 [n] 或来源卡片可定位原文": "Click [n] in an answer or a source card to open the passage.",
    "回答后在这里查看引用来源。": "Sources will appear here after an answer.",
    "本轮没有找到可核对的引用。": "No verifiable sources in this answer.",
    "正在检索知识库…": "Searching the knowledge base…",
    "正在生成回答…": "Generating answer…",
    "正在生成": "Generating",
    "RAG 助手": "RAG Assistant",
    "刚刚": "Just now",
    "复制回答与引用": "Copy answer & sources",
    "补充文档": "Add documents",
    "换个问法": "Rephrase",
    "展开原文": "Expand passage",
    "收起原文": "Collapse passage",
    "未找到依据 · 未生成引用": "No sufficient evidence · no citations",
    "回答完成": "Answer complete",
    "生成失败": "Generation failed",
    "生成失败，未获取引用。": "Generation failed; no sources available.",
    "多模型适配中，当前仅支持 DeepSeek": "Multi-model support is in progress; only DeepSeek is available for now.",
    "已停止 · 未生成回答": "Stopped · no answer",
    "已停止 · 已保留当前内容": "Stopped · partial answer kept",
    "已停止，未完成检索。": "Stopped before retrieval completed.",
    "当前文档中没有找到足够依据。这个问题可能超出知识库范围，建议补充相关文档，或换一个问法。":
      "No sufficient evidence was found in the current documents. This question may be outside the knowledge base. Add relevant documents or rephrase the question.",
    "Transformer 的核心结构是什么？": "What is the core architecture of Transformer?",
    "Transformer 采用编码器—解码器堆叠结构。编码器和解码器都由多层自注意力与前馈网络组成，最核心的是多头自注意力机制：它让模型在同一层内同时关注不同位置的信息，再通过残差连接和层归一化稳定训练。":
      "Transformer uses a stacked encoder–decoder architecture. Both the encoder and decoder consist of multiple self-attention and feed-forward layers. Its core is multi-head self-attention, which lets the model attend to different positions in the same layer and stabilizes training with residual connections and layer normalization.",
    "LM358 的电源电压范围取决于具体型号：LM358B / LM2904B 为 3 V 到 36 V，LM358 / LM2904 为 3 V 到 30 V，LM2904V 为 3 V 到 26 V。数据手册的规格表同时给出了最小值、最大值和单位，建议以对应型号那一行为准。":
      "The LM358 supply voltage range depends on the variant: LM358B / LM2904B support 3 V to 36 V, LM358 / LM2904 support 3 V to 30 V, and LM2904V supports 3 V to 26 V. The datasheet table also gives the min, max, and unit; use the row for the specific variant.",
    "《个人信息保护法》要求处理个人信息遵循合法、正当、必要和诚信原则，并且应当与处理目的直接相关、采取对个人权益影响最小的方式。收集个人信息还应限于实现处理目的的最小范围，不得过度收集。":
      "The Personal Information Protection Law requires processing to follow the principles of lawfulness, legitimacy, necessity, and good faith. Processing must be directly related to a clear purpose and use the least intrusive approach. Collection must be limited to the minimum scope needed for that purpose.",
    "BERT 使用两个预训练任务：掩码语言模型（Masked LM）随机遮住部分 token，让模型根据左右上下文预测；下一句预测（NSP）判断两个句子是否连续。论文还通过消融实验说明这两个任务对最终效果都有贡献。":
      "BERT uses two pre-training tasks: Masked LM randomly masks some tokens and asks the model to predict them from both sides of the context; Next Sentence Prediction (NSP) decides whether two sentences are consecutive. The paper also uses ablations to show that both tasks contribute to the final result.",
    "从你的知识库开始提问": "Ask your knowledge base",
    "LM358 的电源电压范围是多少？": "What is the supply voltage range of the LM358?",
    "个人信息保护法对处理个人信息有哪些要求？": "What does the Personal Information Protection Law require when processing personal data?",
    "BERT 的预训练任务是什么？": "What are BERT's pre-training tasks?",
    "明天杭州天气怎么样？": "What is the weather in Hangzhou tomorrow?",
    "当前没有可检索文档，先上传或恢复文档": "No searchable documents. Upload or restore a document first.",

    // Documents
    "上传、移出、恢复或重建本地知识库；移出后原文件仍保留，可随时恢复。":
      "Upload, remove, restore, or rebuild your local knowledge base. Removed files stay on disk and can be restored.",
    "重建索引": "Rebuild index",
    "可检索文档": "Searchable documents",
    "可检索文本片段": "Searchable passages",
    "索引占用": "Index size",
    "上次全量重建": "Last full rebuild",
    "已移出": "Removed",
    "移出后在这里恢复或彻底删除；向量仍保留":
      "Restore or permanently delete removed documents here; vectors are kept.",
    "向知识库添加文档": "Add documents",
    "支持 PDF、Word、TXT；上传后会自动解析、分块并写入向量库。":
      "Supports PDF, Word, and TXT. Files are parsed, split, and embedded automatically.",
    "拖拽文件到这里，或点击选择": "Drag files here, or click to choose",
    "PDF / DOCX / TXT · 单文件建议 ≤ 20 MB": "PDF / DOCX / TXT · up to 20 MB per file",
    "选择文件": "Choose files",
    "已入库文档": "Active documents",
    "已移出文档": "Removed documents",
    "搜索文件名…": "Search file name…",
    "这些文档已移出知识库，检索不会命中；向量仍保留在本地，可随时恢复。彻底删除才会移除向量并删除 data/uploads 原文件。":
      "These documents are removed from the knowledge base and are not retrieved. Vectors remain locally and can be restored. Permanent deletion removes vectors and the original file in data/uploads.",
    "文件名": "File name",
    "格式": "Format",
    "页数": "Pages",
    "状态": "Status",
    "操作": "Actions",
    "删除文档会同时移除它的全部文本片段": "Deleting a document removes all of its passages.",
    "知识库为空，先上传文档。": "The knowledge base is empty. Upload a document first.",
    "没有匹配的文档。": "No matching documents.",
    "回收站是空的。被移出的文档会出现在这里，可随时恢复。":
      "No removed documents. Files you remove will appear here and can be restored.",
    "排队中": "Queued",
    "解析中": "Parsing",
    "分块中": "Splitting",
    "嵌入中": "Embedding",
    "写入中": "Writing",
    "失败": "Failed",
    "处理中…": "Processing…",
    "移出": "Remove",
    "恢复": "Restore",
    "彻底删除": "Delete permanently",
    "重试": "Retry",
    "加载中…": "Loading…",

    // Settings
    "配置自己的模型密钥。Key 只保存在本机，保存后只显示末 4 位。":
      "Configure your model keys. Keys stay on this machine and only the last 4 characters are shown.",
    "生成模型用于回答（DeepSeek / OpenAI / Anthropic）；阿里云百炼只负责向量嵌入。切换生成模型不会改变索引。":
      "Generation models answer your questions (DeepSeek / OpenAI / Anthropic). Alibaba Cloud Bailian is used only for embeddings. Switching generation models does not change the index.",
    "配置后才能使用这个模型": "Configure a key to use this model",
    "演示配置": "Demo configuration",
    "答案生成 · gpt-4.1-mini": "Answer generation · gpt-4.1-mini",
    "答案生成 · claude-sonnet-4": "Answer generation · claude-sonnet-4",
    "模型": "Model",
    "当前回答使用": "Current answer uses",
    "下一条回答将使用": "Next answer will use",
    "该模型尚未配置 Key，请先去设置": "This model has no API key configured. Open Settings first.",
    "向量模型": "Vector model",
    "问答模型": "Answer models",
    "默认": "Default",
    "只负责上传与检索；更换嵌入模型或维度可能触发索引重建。":
      "Used only for upload and retrieval. Changing the embedding model or dimensions may require a full index rebuild.",
    "用于生成回答；切换只影响后续问答，不会改变索引。":
      "Used to generate answers. Switching affects only new messages and does not change the index.",
    "切换嵌入模型会改变向量维度，已有索引必须整库重建；普通问答请只切换右侧模型。":
      "Changing the embedding model changes vector dimensions and requires a full rebuild. For normal Q&A, switch only the model on the right.",
    "＋ 添加 Provider": "＋ Add provider",
    "更多 Provider 会在正式版接入": "More providers will be available in the production version.",
    "密钥不会上传到我们的服务器": "Keys are never uploaded to our servers",
    "本地模式使用 Windows DPAPI 加密保存；界面和日志都不会出现完整 Key。调用模型时由服务端解密使用。":
      "Local mode stores keys with Windows DPAPI. The full key never appears in the UI or logs; the server decrypts it when calling a model.",
    "答案生成 · deepseek-v4-flash": "Answer generation · deepseek-v4-flash",
    "向量嵌入 · text-embedding-v4": "Embeddings · text-embedding-v4",
    "已配置": "Configured",
    "未配置": "Not configured",
    "本机保存": "Saved locally",
    "更换 Key": "Replace key",
    "配置 Key": "Configure key",
    "测试连接": "Test connection",
    "清除": "Clear",
    "配置后才能生成回答": "Required for answer generation",
    "上传与检索需要这个 Key": "Required for upload and retrieval",
    "首次启动会自动进入引导；也可以重新运行。":
      "Setup runs automatically on first launch; you can run it again.",
    "重新运行首次引导": "Run setup again",
    "阿里云百炼": "Alibaba Cloud Bailian",
    "测试中…": "Testing…",
    "保存": "Save",
    "保存后只显示末 4 位，例如 sk-••••••••••••••••••4f2a；不会显示完整内容，也不会写进日志。":
      "Only the last 4 characters are shown after saving, e.g. sk-••••••••••••••••••4f2a. The full key is never displayed or written to logs.",
    "请输入 API Key": "Enter an API key",
    "清除后，依赖这个 Key 的功能会立即停止工作；可以随时重新配置。":
      "Features that depend on this key will stop working immediately. You can configure it again at any time.",
    "原型中只会清除当前页面的模拟状态，不会操作真实密钥。":
      "In this prototype, only the mock state on this page is cleared; real keys are not touched.",
    "重新运行首次引导？": "Run setup again?",
    "会清除当前模拟的 API Key 配置，并回到首次启动引导。":
      "This clears the mock key configuration and returns to the first-run setup.",
    "真实版本中，这不会删除你的真实 Key，除非你选择清除。":
      "In the real app, this does not delete your real keys unless you choose to clear them.",
    "重新引导": "Run setup",

    // Setup
    "配置模型 api-key": "Configure model API keys",
    "这个知识库需要两个 Key 才能完成问答。它们只保存在本机，不会上传到我们的服务器。":
      "This knowledge base needs two keys to answer questions. They stay on this machine and are never uploaded to our servers.",
    "负责生成最终回答": "Generates the final answer",
    "负责把文档和问题变成向量": "Turns documents and questions into vectors",
    "保存后只显示末 4 位，例如": "Only the last 4 characters are shown after saving, e.g. ",
    "；不会显示完整内容。": ". The full key is never displayed.",
    "；不会显示完整内容，也不会写进日志。": ". The full key is never displayed or written to logs.",
    "，留空则保留当前 Key。": ", leave blank to keep the current key.",
    "保存后只显示末 4 位，例如 sk-••••••••••••••••••4f2a；不会显示完整内容。":
      "Only the last 4 characters are shown after saving, e.g. sk-••••••••••••••••••4f2a. The full key is never displayed.",
    "开始配置": "Start setup",
    "跳过，先只读浏览": "Skip for now",
    "用于调用 deepseek-v4-flash 生成回答。": "Used to generate answers with deepseek-v4-flash.",
    "用于调用 text-embedding-v4 生成 1024 维向量；上传和检索都需要它。":
      "Used to create 1024-dimensional embeddings with text-embedding-v4. Required for upload and retrieval.",
    "已配置：": "Configured: ",
    "上一步": "Back",
    "保存并继续": "Save and continue",
    "请填写 Key，或点击“跳过，先只读浏览”。": "Enter a key, or choose “Skip for now”.",
    "保存前先确认两个 Key 都能连通。测试由服务端发起，浏览器不会接触完整 Key。":
      "Before saving, check that both keys work. The server runs the test; the browser never sees the full key.",
    "连接测试通过，可以开始使用。": "Connection test passed. You can start using the knowledge base.",
    "完成，进入知识库": "Finish and open the knowledge base",
    "先跳过，稍后配置": "Skip and configure later",
    "知者不惑。": "The wise are not perplexed.",
    "—— 孔子": "— Confucius",
    "知者不惑。—— 孔子": "The wise are not perplexed. — Confucius",
    "配置进度": "Setup progress",
    "打开的书本通向知识天空": "An open book leading to a sky of knowledge",
    "双 Key 配置": "Two-key setup",

    // Common actions / states
    "取消": "Cancel",
    "确认": "Confirm",
    "关闭": "Close",
    "删除": "Delete",
    "移出知识库？": "Remove from knowledge base?",
    "彻底删除？": "Delete permanently?",
    "彻底删除文档？": "Delete document permanently?",
    "重建整个索引？": "Rebuild the entire index?",
    "正在重建索引": "Rebuilding index",
    "将": "Remove",
    "从检索结果和“已入库”列表中移出。": "from search results and the Active list.",
    "原文件和向量都会保留，可随时在“已移出”中恢复；不会删除原文件（": "The original file and vectors are kept. Restore from Removed at any time. The original file is not deleted (",
    "）。": ").",
    "将删除文档": "This will delete document",
    "：": ": ",
    "个文本片段、对应向量，以及": "passages, their vectors, and",
    "个文本片段、对应向量，以及原文件（": "passages, their vectors, and the original file (",
    "以及原文件（": "and the original file (",
    "中的原文件。": "in the original file.",
    "此操作不可恢复。如果只是暂时不想检索它，请使用“移出知识库”。":
      "This cannot be undone. If you only want to hide it from retrieval, use “Remove from knowledge base”.",
    "已移出知识库：": "Removed: ",
    "准备中…": "Preparing…",
    "已移出知识库": "Removed from knowledge base",
    "已恢复到知识库": "Restored to knowledge base",
    "已彻底删除": "Permanently deleted",
    "会话已清空": "Conversation cleared",
    "索引重建完成": "Index rebuild complete",
    "请先配置 API Key": "Configure API keys first",
    "配置完成，开始提问吧": "Setup complete. Start asking.",
    "已跳过配置：聊天与上传会提示缺少 API Key":
      "Setup skipped: chat and upload will report missing API keys.",
    "还有 Key 未配置，可先跳过": "Some keys are still missing; you can skip for now.",
    "两个 Key 连接正常": "Both keys are working",
    "已复制回答": "Answer copied",
    "回答与引用已复制": "Answer and sources copied",
    "复制失败，请手动选择文本": "Copy failed. Select the text manually.",
    "已加入解析队列": "Added to processing queue",
    "已入库": "Indexed",
    "入库失败": "Indexing failed",
    "重新尝试": "Retrying",
    "只支持 PDF / DOCX / TXT 文件": "Only PDF / DOCX / TXT files are supported",
    "请选择文件": "Choose a file",
    "无法上传文档": "Cannot upload document",
    "上传需要阿里云百炼嵌入 API Key：文档要先生成 1024 维向量才能入库。请先在设置中配置百炼 Key。":
      "Upload requires a DashScope embedding key: documents need 1024-dimensional vectors before they can be indexed. Configure the key in Settings first.",

    // Modal copy
    "原文件和向量都会保留，可随时在“已移出”中恢复；不会删除 data/uploads 原文件。":
      "The original file and vectors are kept. Restore from “Removed” at any time; data/uploads is not deleted.",
    "这会清空当前索引并重新解析、嵌入": "This clears the current index and re-parses and re-embeds",
    "份已入库文档（共": "active documents (",
    "个文本片段）。": "passages total).",
    "开始重建": "Start rebuild",
    "重建期间问答可能不可用；会重新消耗嵌入额度。原型中会模拟约 6 分钟。":
      "Chat may be unavailable during the rebuild and embedding quota will be used again. The prototype simulates about 6 minutes.",
    "会清空当前可检索索引，并重新解析、嵌入全部已入库文档。":
      "This clears the searchable index and re-parses and re-embeds all active documents.",
    "原型演示：这里模拟约 6 分钟的重建过程，不会真的调用嵌入接口。":
      "Prototype: simulates about 6 minutes of rebuilding; no embedding API is called.",
    "将删除": "Will delete",
    "的": "of",
    "个文本片段、对应向量，以及 data/uploads 中的原文件。":
      "passages, their vectors, and the original file in data/uploads.",
  };

  const PATTERNS = [
    [/^基于 (\d+) 份文档回答，引用可以展开核对原文$/, (m, n) => `Answers from ${n} documents · expand citations to verify`],
    [/^(\d+) 份可检索文档 · 引用可展开核对$/, (m, n) => `${n} searchable documents · citations can be expanded`],
    [/^已索引 (\d+) 份文档、([\d,]+) 个文本片段。回答会带上引用，点击 \[n\] 可以展开原文核对。$/, (m, docs, chunks) => `Indexed ${docs} documents and ${chunks} passages. Answers include citations; click [n] to verify the source.`],
    [/^可检索 (\d+) 个文件 \/ ([\d,]+) 个文本片段$/, (m, docs, chunks) => `${docs} searchable files / ${chunks} passages`],
    [/^可检索 (\d+) 个文件 \/ ([\d,]+) 个文本片段；已移出 (\d+) 个（([\d,]+) 个文本片段仍占用向量空间）$/, (m, docs, chunks, removed, removedChunks) => `${docs} searchable files / ${chunks} passages; ${removed} removed (${removedChunks} passages still occupy vector space)`],
    [/^共 (\d+) 个文件，([\d,]+) 个文本片段$/, (m, docs, chunks) => `${docs} files, ${chunks} passages`],
    [/^当前显示 (\d+) 个文件 · ([\d,]+) 个可检索文本片段$/, (m, docs, chunks) => `Showing ${docs} files · ${chunks} searchable passages`],
    [/^第 (\d+) 页$/, (m, n) => `Page ${n}`],
    [/^查看引用 (\d+)$/, (m, n) => `View citation ${n}`],
    [/^无页码$/, () => "No page number"],
    [/^第 (\d+) 页 · text · (\d+) 页 PDF$/, (m, page, pages) => `Page ${page} · text · ${pages}-page PDF`],
    [/^第 (\d+) 页 · table · (\d+) 页 PDF$/, (m, page, pages) => `Page ${page} · table · ${pages}-page PDF`],
    [/^无页码 · text · (\d+) 页 (PDF|Word|DOCX|TXT)$/, (m, pages, type) => `No page number · text · ${pages}-page ${type}`],
    [/^无页码 · text · (.+)$/, (m, meta) => `No page number · text · ${meta}`],
    [/^无页码 · table · (.+)$/, (m, meta) => `No page number · table · ${meta}`],
    [/^(\d+) 页 (PDF|Word|DOCX|TXT)$/, (m, pages, type) => `${pages}-page ${type}`],
    [/^text · (\d+) 页 (PDF|Word|DOCX|TXT)$/, (m, pages, type) => `text · ${pages}-page ${type}`],
    [/^table · (\d+) 页 (PDF|Word|DOCX|TXT)$/, (m, pages, type) => `table · ${pages}-page ${type}`],
    [/^这些文档已移出知识库，检索不会命中；向量仍保留在本地，可随时恢复。彻底删除才会移除向量并删除$/, () => "These documents are removed from the knowledge base and are not retrieved. Vectors remain locally and can be restored. Permanent deletion removes vectors and deletes "],
    [/^这些文档已移出知识库，检索不会命中；向量仍保留在本地，可随时恢复。彻底删除才会移除向量并删除原文件（$/, () => "These documents are removed from the knowledge base and are not retrieved. Vectors remain locally and can be restored. Permanent deletion removes vectors and the original file ("],
    [/^原文件。$/, () => "the original file."],
    [/^检索 (\d+) 个片段 · 首字 ([\d.]+)s · 完整回答 ([\d.]+)s$/, (m, n, ttft, total) => `${n} passages · first token ${ttft}s · ${total}s total`],
    [/^请求失败：(.+)$/, (m, detail) => `Request failed: ${detail}`],
    [/^当前回答使用 (.+)$/, (m, name) => `Current answer uses ${name}`],
    [/^下一条回答将使用 (.+)$/, (m, name) => `Next answer will use ${name}`],
    [/^演示配置 · 更新于 (.+)$/, (m, time) => `Demo configuration · updated ${time}`],
    [/^解析中 · 第 (\d+) \/ (\d+) 页$/, (m, page, pages) => `Parsing · page ${page} of ${pages}`],
    [/^分块中 · 已生成 (\d+) \/ 24 个文本片段$/, (m, n) => `Splitting · ${n} of 24 passages`],
    [/^嵌入中 · 第 (\d+) \/ (\d+) 批$/, (m, batch, batches) => `Embedding · batch ${batch} of ${batches}`],
    [/^写入中 · 正在写入向量库$/, () => "Writing · saving vectors"],
    [/^第 (\d+) \/ 4 步$/, (m, n) => `Step ${n} of 4`],
    [/^已配置：(.+)$/, (m, mask) => `Configured: ${mask}`],
    [/^本机保存 · 更新于 (.+)$/, (m, time) => `Saved locally · updated ${time}`],
    [/^已加入解析队列：(.+)$/, (m, name) => `Added to queue: ${name}`],
    [/^入库失败：(.+)，可点击重试$/, (m, name) => `Indexing failed: ${name}. You can retry.`],
    [/^重新尝试：(.+)$/, (m, name) => `Retrying: ${name}`],
    [/^已移出知识库：(.+)，可在“已移出”中恢复或彻底删除$/, (m, name) => `Removed: ${name}. Restore or delete permanently from Removed.`],
    [/^已移出知识库：(.+)$/, (m, name) => `Removed: ${name}`],
    [/^已恢复到知识库：(.+)$/, (m, name) => `Restored: ${name}`],
    [/^已彻底删除：(.+)$/, (m, name) => `Permanently deleted: ${name}`],
    [/^(.+) 已入库$/, (m, name) => `${name} indexed`],
    [/^(.+) Key 已保存$/, (m, name) => `${name} key saved`],
    [/^(.+) Key 已清除$/, (m, name) => `${name} key cleared`],
    [/^(.+) 连接正常$/, (m, name) => `${name} connection works`],
    [/^清除 (.+) Key？$/, (m, name) => `Clear ${name} key?`],
    [/^更换 (.+) Key$/, (m, name) => `Replace ${name} key`],
    [/^配置 (.+) Key$/, (m, name) => `Configure ${name} key`],
    [/^(.+) Key$/, (m, name) => `${name} key`],
    [/^正在处理 (.+)$/, (m, name) => `Processing ${name}`],
  ];

  let lang = "zh";
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "zh") lang = stored;
  } catch {
    // localStorage may be unavailable
  }

  const originalText = new Map();
  const originalAttrs = new Map();
  let observer = null;
  let applying = false;

  function lookup(text) {
    const leading = text.match(/^\s*/)[0];
    const trailing = text.match(/\s*$/)[0];
    const core = text.trim();
    if (!core) return null;
    if (Object.prototype.hasOwnProperty.call(EN, core)) {
      return `${leading}${EN[core]}${trailing}`;
    }
    if (!/[\u4e00-\u9fff]/.test(core)) return null;
    for (const [pattern, replacer] of PATTERNS) {
      if (pattern.test(core)) {
        return `${leading}${core.replace(pattern, replacer)}${trailing}`;
      }
    }
    return null;
  }

  function translateTextNode(node) {
    if (lang !== "en" || !node.nodeValue) return;
    const current = node.nodeValue;
    if (originalText.has(node)) {
      const previous = originalText.get(node);
      const translatedPrevious = lookup(previous);
      if (current !== previous && current !== translatedPrevious) {
        originalText.set(node, current);
      }
    } else {
      originalText.set(node, current);
    }
    const translated = lookup(current);
    if (translated === null) return;
    if (current === translated) return;
    applying = true;
    node.nodeValue = translated;
    applying = false;
  }

  function translateAttrs(el) {
    if (lang !== "en" || !el.getAttribute) return;
    for (const attr of ["placeholder", "title", "aria-label"]) {
      const value = el.getAttribute(attr);
      if (!value) continue;
      let store = originalAttrs.get(el);
      if (!store) {
        store = {};
        originalAttrs.set(el, store);
      }
      if (!(attr in store)) store[attr] = value;
      const translated = lookup(value);
      if (translated !== null && translated !== value) {
        el.setAttribute(attr, translated.trim());
      }
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      translateTextNode(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    if (["SCRIPT", "STYLE"].includes(root.tagName)) return;
    translateAttrs(root);
    const elementWalker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let element = elementWalker.nextNode();
    while (element) {
      translateAttrs(element);
      element = elementWalker.nextNode();
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      translateTextNode(node);
      node = walker.nextNode();
    }
  }

  function apply() {
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    if (lang === "en") {
      walk(document.body);
      startObserver();
    }
    updateToggle();
  }

  function restore() {
    for (const [node, text] of originalText) {
      if (node && node.parentNode) node.nodeValue = text;
    }
    originalText.clear();
    for (const [el, attrs] of originalAttrs) {
      for (const [attr, value] of Object.entries(attrs)) {
        el.setAttribute(attr, value);
      }
    }
    originalAttrs.clear();
  }

  function startObserver() {
    if (observer) observer.disconnect();
    observer = new MutationObserver((mutations) => {
      if (lang !== "en" || applying) return;
      for (const mutation of mutations) {
        if (mutation.type === "childList") {
          mutation.addedNodes.forEach((node) => walk(node));
        } else if (mutation.type === "characterData") {
          translateTextNode(mutation.target);
        } else if (mutation.type === "attributes") {
          translateAttrs(mutation.target);
        }
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["placeholder", "title", "aria-label"],
    });
  }

  function updateToggle() {
    const button = document.querySelector("#langButton");
    if (!button) return;
    const next = lang === "zh" ? "English" : "简体中文";
    button.textContent = lang === "zh" ? "EN" : "中文";
    const label = lang === "zh" ? "切换到 English" : "Switch to Simplified Chinese";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.dataset.next = next;
  }

  function setLanguage(next) {
    if (next !== "en" && next !== "zh") return;
    if (next === lang) return;
    if (observer) observer.disconnect();
    if (next === "zh") {
      restore();
      lang = "zh";
      document.documentElement.lang = "zh-CN";
    } else {
      lang = "en";
      document.documentElement.lang = "en";
      walk(document.body);
      startObserver();
    }
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // ignore
    }
    updateToggle();
    window.dispatchEvent(new CustomEvent("i18n:change", { detail: { lang } }));
  }

  function getLanguage() {
    return lang;
  }

  window.I18n = { getLanguage, setLanguage, apply, t: (text) => (lang === "en" ? lookup(text) ?? text : text) };

  document.addEventListener("click", (event) => {
    if (event.target.closest("#langButton")) {
      setLanguage(lang === "zh" ? "en" : "zh");
    }
  });

  window.addEventListener("DOMContentLoaded", () => {
    if (lang === "en") apply();
    else updateToggle();
  });
})();
