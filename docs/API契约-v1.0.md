# API 契约 v1.0

> 本文是**路由、字段、SSE、错误码、job 状态机的唯一来源**。字段变更必须先改本文，再改实现与 UI；需求/架构只链接本文，不再复制接口表。
>
> | 项 | 值 |
> |---|---|
> | 状态 | **Implemented（P0–P4）**；未处理异常已统一 JSON，HTTPException 包装与 `X-Request-ID` 响应头仍待补 |
> | 日期 | 2026-10-25 |
> | 前缀 | `/api/v1` |
> | 本地模式 | `127.0.0.1`，无登录，固定 `tenant_id="local"` |
> | 服务器模式 | 登录后从 session 推导 tenant；未认证 401 |
> | 关联 | [文档地图](README.md) · [架构演进设计](架构演进设计.md) · [需求文档](RAG需求文档-v1.0.md) · [任务清单](RAG开发任务清单-v1.0.md) |

### 实现状态（2026-10-26）

| 模块 | 状态 | 当前实现说明 |
|---|---|---|
| `GET /health` | ✅ 已实现 | 只返回 `status/version/mode`；`index` 统计未实现 |
| `POST /chat` SSE | ✅ 已实现 | 真实流式验收通过；无引用时 `done.answer` 回退固定话术 |
| 文档上传 / job | ✅ 已实现 | 后台串行任务；状态枚举见 §四 |
| 文档列表 / 软删除 / 恢复 / 彻底删除 | ✅ 已实现 | `document_id` 当前使用 `file_hash` |
| 索引重建 / job | ✅ 已实现 | 先算完再替换；失败保留旧索引；保留已移出文档的向量 |
| Key 设置 | ✅ 已实现 | DPAPI 本机加密存储；`settings/secrets` CRUD + test；系统环境变量优先 |
| 统一错误结构 / `X-Request-ID` | ⏳ 部分实现 | 未处理 500 已统一为 `{code,message,detail,retryable,request_id}` 并记录堆栈；HTTPException 包装与响应头 `X-Request-ID` 待补 |
| `document_id` ULID | ⏳ 未切换 | 当前 `file_hash`；服务器 Beta 全量重建时切换 |

---

## 一、通用约定

### 1.1 内容类型

- 普通接口：`application/json; charset=utf-8`
- 聊天：`text/event-stream`（SSE）
- 上传：`multipart/form-data`

### 1.2 认证与租户

**本地模式**：

- 只监听 `127.0.0.1`；
- 不要求登录；
- 服务端把 tenant 固定为 `"local"`；
- 前端不得传 `tenant_id`。

**服务器模式**：

- 必须登录；
- tenant/user 从 session 或 token 推导；
- 请求体/header 中的 `tenant_id` 一律忽略；
- 未认证返回 `401`，越权返回 `403`；
- 所有业务查询必须带服务端注入的 tenant 条件。

### 1.3 错误结构

```json
{
  "error": {
    "code": "CONFIG_MISSING",
    "message": "尚未配置百炼嵌入 API Key",
    "detail": "请在设置中配置 DASHSCOPE_API_KEY",
    "retryable": false,
    "request_id": "req_01J..."
  }
}
```

错误码约定：

| code | HTTP | 含义 |
|---|---|---|
| `INVALID_REQUEST` | 400 | 参数/格式错误 |
| `UNAUTHORIZED` | 401 | 未登录（服务器模式） |
| `FORBIDDEN` | 403 | 无权限/跨租户 |
| `NOT_FOUND` | 404 | 文档/任务不存在 |
| `CONFLICT` | 409 | 重复操作/状态冲突 |
| `UNSUPPORTED_FILE` | 415 | 不支持的格式 |
| `FILE_TOO_LARGE` | 413 | 超过大小限制 |
| `CONFIG_MISSING` | 503 | 缺少模型 Key |
| `UPSTREAM_TIMEOUT` | 504 | 模型/嵌入超时 |
| `RATE_LIMITED` | 429 | 限流 |
| `INTERNAL` | 500 | 未预期错误 |

### 1.4 请求 ID

所有响应返回 `X-Request-ID`，错误体含 `request_id`；日志按它关联，不记录 Key/完整文档内容。

### 1.5 时间与 ID

- 时间：ISO 8601 UTC，例如 `2026-10-25T01:20:00Z`；
- 文档 ID：目标为服务器生成的 ULID；**当前本地实现暂用 `file_hash` 作为 `document_id`**，前端只把它当不透明字符串，不依赖其形态；
- `file_hash`：内容 SHA-256，只用于去重/版本；服务器 Beta 全量重建时切换为 ULID 身份。

---

## 二、健康检查

### `GET /api/v1/health`

响应 `200`：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "mode": "local"
}
```

`index` 统计（文档数/chunk 数）为后续字段，当前实现不返回；本地模式不暴露敏感路径。

---

## 三、聊天接口（SSE）

### `POST /api/v1/chat`

请求：

```json
{
  "question": "Transformer 的核心结构是什么？",
  "session_id": "可选；不传则新建"
}
```

约束：

- `question` 非空，长度上限由配置控制（建议 4000 字）；
- `session_id` 只用于多轮上下文；V1 内存保存最近 `MAX_HISTORY_TURNS` 轮，重启即清；
- 有历史且当前问题为追问时，服务端先做查询改写，再检索；改写失败回退原问题；
- 服务器不得把 `tenant_id`/Key 写进 SSE。

响应头：

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

### 3.1 事件顺序

推荐顺序：

```text
sources  →  delta*  →  done
                └→ error
```

- `sources` 在检索完成后、生成开始前发送；前端可以先渲染引用；
- `delta` 可以 0~N 个；每个只包含增量文本；
- `done` 必须发送一次，包含完整答案；
- `error` 可能替代 `done`；若已发送部分 `delta`，前端保留已生成内容并显示错误。

### 3.2 `sources`

```text
event: sources
data: {"sources":[{
  "n": 1,
  "document_id": "01J...",
  "file_name": "attention is all you need.pdf",
  "page": 5,
  "chunk_id": "01J...:000123",
  "kind": "text",
  "score": 0.352,
  "excerpt": "The Transformer uses multi-head attention ..."
}]}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `n` | int | 与回答中的 `[n]` 一一对应，从 1 开始 |
| `document_id` | string | 文档 ID |
| `file_name` | string | 展示文件名 |
| `page` | int | PDF 从 1 开始；TXT/Word 为 0 |
| `chunk_id` | string | 向量 ID |
| `kind` | string | `text` / `table` |
| `score` | float | **cosine 相似度，0~1，越大越相关** |
| `excerpt` | string | 原文片段，前端截断展示 |

> 当前 Chroma 返回 cosine 距离；检索器负责转换为 `score = 1 - distance`，再进入契约。
>
> `sources` 数量可能小于 `TOP_K`：低于 `MIN_SCORE`（默认 0.25）的片段已在检索层过滤。

### 3.3 `delta`

```text
event: delta
data: {"text":"Transformer 由"}
```

### 3.4 `done`

```text
event: done
data: {
  "session_id": "01J...",
  "answer": "完整回答文本",
  "rewritten_query": "多轮追问改写后的独立查询；首轮等于 question",
  "usage": {"prompt_tokens": 1200, "completion_tokens": 180},
  "fallback": false,
  "chat_mode": false,
  "timings": {"retrieval_ms": 220, "ttft_ms": 410, "total_ms": 2600}
}
```

字段：

- `fallback=true`：没有有效引用，`answer` 已被替换为固定话术「知识库中没有相关信息。」；客户端应清空本轮 `sources`，不要把检索阶段先到的片段当成“引用来源”展示；
- `chat_mode=true`：本轮是问候/闲聊或与知识库无关的输入，`answer` 是暖心回应，**不含引用**；客户端应清空本轮 `sources`、隐藏引用卡片，不要再展示检索阶段先到的片段；
- `[[CHAT]]` 是服务端内部标记，只用于区分闲聊与知识回答，**不得出现在 `delta` / `answer` 中**。

### 3.5 `error`

```text
event: error
data: {
  "code": "CONFIG_MISSING",
  "message": "尚未配置 DeepSeek API Key",
  "retryable": false,
  "request_id": "req_01J..."
}
```

### 3.6 停止生成

- 前端使用 `AbortController` 关闭 SSE；
- 服务端检测断连后停止上游生成，已生成内容不落库（V1）；
- V1 不单独提供 `/stop` 接口；
- 若未来需要多端同步停止，再增加 `POST /chat/{message_id}/stop`。

### 3.7 无检索结果

- 低于 `MIN_SCORE`（默认 `0.25`）的片段不进入上下文；全部低于阈值时 `sources: []`；
- 即使没有可用片段，仍会调用一次生成模型，用来区分问候/闲聊与知识问题：
  - `chat_mode=true`：按系统提示词第 7 条暖心回应，不带引用；
  - 否则 `answer` 为固定话术「知识库中没有相关信息。」，`fallback=true`；
- `done` 正常结束，不做 404。

---

## 四、文档上传（后台任务）

### `POST /api/v1/documents/upload`

`multipart/form-data`：

```text
files: <file1>
files: <file2>
```

响应 `202`：

```json
{
  "jobs": [
    {
      "job_id": "job_01J...",
      "file_name": "lm358.pdf",
      "state": "queued"
    }
  ]
}
```

规则：

- 支持 `.pdf` / `.docx` / `.txt`；
- 单文件建议上限 20 MB（可配置）；
- 每个文件一个 job；
- 同一租户同一时间只运行一个索引 job，其他 `queued`；
- 相同 `file_hash` 已存在 → `state="skipped"`，返回已有 `document_id`；
- 同名文件（`source_file` 相同、内容哈希不同）→ 视为替换：先写临时文件再原子覆盖，新版本入库成功后清理旧版本向量（不删除当前文件）；
- 损坏文件 → 该 job `failed`，不影响其他文件。

### `GET /api/v1/documents/uploads/{job_id}`

响应 `200`：

```json
{
  "job_id": "job_01J...",
  "document_id": "01J...",
  "file_name": "lm358.pdf",
  "state": "embedding",
  "percent": 76,
  "stage": "嵌入中 · 第 8 / 24 批",
  "current": 8,
  "total": 24,
  "unit": "batch",
  "message": "正在生成向量",
  "error": null,
  "created_at": "2026-10-25T01:20:00Z",
  "updated_at": "2026-10-25T01:21:00Z"
}
```

`state` 枚举：

| state | 含义 |
|---|---|
| `queued` | 排队中 |
| `running` | 任务已开始，准备解析 |
| `parsing` | 解析中（PDF 可按页） |
| `splitting` | 分块中 |
| `embedding` | 嵌入并写入向量库 |
| `ready` | 已入库，可检索 |
| `skipped` | 相同 hash 已存在，跳过 |
| `failed` | 失败，`error` 给出原因 |

`unit`：`page` / `chunk` / `batch` / `file`。

进度计算建议：

```text
queued     0–5%
running    1–5%
parsing    5–35%
splitting 35–50%
embedding 50–99%
ready     100%
```

前端轮询间隔 500–800ms；完成后停止轮询并刷新文档列表。

> 上传进度必须是真实任务进度，不允许前端用 CSS 假动画伪造百分比。

---

## 五、文档管理

### 5.1 列表

`GET /api/v1/documents?status=active|removed&query=&page=1&page_size=50`

响应：

```json
{
  "items": [
    {
      "id": "01J...",
      "file_name": "lm358.pdf",
      "doc_type": "pdf",
      "pages": 68,
      "chunks": 237,
      "size": 4154321,
      "status": "active",
      "created_at": "2026-10-25T01:20:00Z",
      "removed_at": null
    }
  ],
  "total": 10,
  "counts": {"active": 10, "removed": 0, "chunks": 4021}
}
```

规则：

- 默认只返回 `active`；
- 文件名搜索只用于展示，不参与身份；
- 服务器模式必须自动加 tenant 条件。

### 5.2 移出知识库（软删除）

`DELETE /api/v1/documents/{id}`

响应 `200`：

```json
{
  "id": "01J...",
  "status": "removed",
  "removed_at": "2026-10-25T01:30:00Z"
}
```

语义：

- 文档从检索与 `active` 列表移除；
- 原文件与向量保留；
- 可恢复；
- 不释放磁盘/向量空间；
- 重复调用幂等：已 removed 仍返回 200。

### 5.3 恢复

`POST /api/v1/documents/{id}/restore`

响应 `200`：

```json
{"id":"01J...","status":"active","removed_at":null}
```

- 恢复后立即可检索；
- 不重新解析、不重新嵌入；
- 若原文件已被删除，仍可恢复向量；重建会保留这些向量、不重新解析该文件，但该文档之后无法通过重建更新内容。

### 5.4 彻底删除

`DELETE /api/v1/documents/{id}/permanent`

响应 `200`：

```json
{
  "id": "01J...",
  "deleted_chunks": 237,
  "file_deleted": true
}
```

语义：

- 删除业务记录、全部 chunk/向量、`data/uploads` 原文件；
- 不可恢复；
- 需要二次确认；
- 文件路径必须在 `UPLOAD_DIR` 内，防路径穿越；
- Chroma/HNSW 的物理 tombstone 不保证立即收缩；V1 以逻辑删除 + 文件删除为准。

### 5.5 重建索引

`POST /api/v1/index/rebuild`

响应 `202`：

```json
{"job_id":"job_01J...","state":"queued","scope":"active"}
```

`GET /api/v1/index/jobs/{job_id}` 返回与上传 job 相同的进度结构，`unit="file"`，`state` 使用 `queued/running/parsing/splitting/embedding/ready/failed`。

- 只重建 `active` 文档；
- `removed` 文档保持 removed，其向量保留（恢复仍可用）；
- 先解析、分块并算完全部向量，最后才替换索引；任何文件失败都会中止并保留旧索引；
- 磁盘上存在、但当前索引里缺失向量的 active 文件会被补回；
- 同一时间只允许一个重建任务；
- 会重新消耗嵌入额度，UI 必须提示。

---

## 六、API Key 设置

### 6.1 状态查询

`GET /api/v1/settings/secrets`

响应：

```json
{
  "providers": {
    "deepseek": {
      "configured": true,
      "masked": "sk-••••••••••••••••••4f2a",
      "source": "local",
      "updated_at": "2026-10-25T01:20:00Z",
      "last_test": {"ok": true, "latency_ms": 690, "model": "deepseek-v4-flash"}
    },
    "dashscope": {
      "configured": true,
      "masked": "sk-••••••••••••••••••7b3c",
      "source": "local",
      "updated_at": "2026-10-25T01:20:00Z",
      "last_test": {"ok": true, "latency_ms": 1400, "model": "text-embedding-v4"}
    }
  },
  "complete": true
}
```

规则：

- **绝不返回完整 Key**；
- `source`：`local`（本机加密存储）/ `env`（环境变量）/ `none`；
- 掩码格式：若 Key 以 `sk-` 开头，显示 `sk-` + 18 个 `•` + 末 4 位；
- `complete` 表示聊天所需的两个 Key 是否都可用。

### 6.2 保存/更换

`PUT /api/v1/settings/secrets/{provider}`

`provider`：`deepseek` | `dashscope`

请求：

```json
{"key":"sk-..."}
```

响应 `200`：

```json
{
  "provider":"deepseek",
  "configured":true,
  "masked":"sk-••••••••••••••••••4f2a",
  "source":"local",
  "updated_at":"2026-10-25T01:20:00Z"
}
```

规则：

- 服务端校验非空/长度；
- 本地模式用 DPAPI/keyring 加密保存；
- 服务器模式用每用户加密密钥库；
- 不写日志、不回显完整 Key；
- 保存后浏览器不再持有完整 Key。

### 6.3 测试连接

`POST /api/v1/settings/secrets/{provider}/test`

请求体为空；服务端用已保存 Key 测试。

响应：

```json
{"ok":true,"latency_ms":690,"model":"deepseek-v4-flash","message":"连接正常"}
```

失败：

```json
{"ok":false,"code":"UPSTREAM_TIMEOUT","message":"连接超时","retryable":true}
```

### 6.4 清除

`DELETE /api/v1/settings/secrets/{provider}`

响应 `200`：

```json
{"provider":"deepseek","configured":false}
```

- 清除后依赖该 provider 的功能立即不可用；
- 聊天/上传接口返回 `CONFIG_MISSING`；
- 不自动删除历史消息/文档。

### 6.5 首次启动

- 前端调用 `GET /api/v1/settings/secrets`；
- `complete=false` → 进入 `/setup`；
- 允许“跳过，先只读浏览”；
- 跳过时：
  - 聊天发送 → 前端显示缺失 Key 的错误气泡；
  - 上传 → 返回/显示 `CONFIG_MISSING`；
  - 文档列表可只读。
- 配置完成后回到 `/`。

---

## 七、UI 与接口映射

| UI 行为 | 接口 |
|---|---|
| 发送问题 | `POST /api/v1/chat`（SSE） |
| 停止生成 | 前端 Abort SSE |
| 引用卡片 | `sources` 事件中的字段 |
| 上传文件 | `POST /api/v1/documents/upload` |
| 上传进度条 | `GET /api/v1/documents/uploads/{job_id}` |
| 已入库列表 | `GET /api/v1/documents?status=active` |
| 移出 | `DELETE /api/v1/documents/{id}` |
| 已移出列表 | `GET /api/v1/documents?status=removed` |
| 恢复 | `POST /api/v1/documents/{id}/restore` |
| 彻底删除 | `DELETE /api/v1/documents/{id}/permanent` |
| 重建索引 | `POST /api/v1/index/rebuild` + job 查询 |
| 设置页状态 | `GET /api/v1/settings/secrets` |
| 保存/更换 Key | `PUT /api/v1/settings/secrets/{provider}` |
| 测试连接 | `POST /api/v1/settings/secrets/{provider}/test` |

---

## 八、实现顺序

具体任务编号、依赖与完成状态由 [开发任务清单](RAG开发任务清单-v1.0.md) 管理，本文不重复维护。接口层面的实现状态见开头的“实现状态”表。

---

## 九、验收清单

- [ ] 所有响应符合错误结构，且带 `request_id`
- [ ] 聊天 SSE 事件顺序稳定，`sources` 中的 `n` 与回答引用一致
- [ ] 停止生成后不残留上游请求
- [ ] 上传 job 状态机覆盖 queued/running/parsing/splitting/embedding/ready/skipped/failed
- [ ] 上传进度来自真实阶段，不是前端伪造
- [ ] 软删除后检索不到，恢复后立即可检索
- [ ] 彻底删除同时移除 chunk/向量/原文件
- [ ] `settings/secrets` 永远不返回完整 Key
- [ ] 本地模式只监听 `127.0.0.1`
- [ ] 服务器模式下所有查询都有 tenant 隔离测试

---

## 十、接口决策记录

| # | 问题 | 状态 | 结论 |
|---|---|---|---|
| Q1 | `score` 用相似度还是距离 | 已定 | 用 0~1 相似度（越大越好），检索器内部从 cosine 距离转换 |
| Q2 | 文档 ID 用 ULID 还是 UUID | 目标已定 | 目标 ULID；当前本地实现过渡为 `file_hash` |
| Q3 | 上传是否支持取消 | 待定 | V1 不做；先做失败重试 |
| Q4 | 彻底删除同步还是异步 | 已定 | 小文件同步 200；大库后续可转 202 job |
| Q5 | 会话历史存哪里 | 待定 | V1 内存，T-6 再上 SQLite |
| Q6 | 服务器模式认证方式 | 待定 | OIDC / Passkey，后续定 |

> 契约变更流程：先改本文 → 更新测试 → 更新前端 mock → 再改实现。禁止只改一端。
