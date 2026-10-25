# RAG_konwledge_base_system

> A personal knowledge base system constructed based on the Naive Retrieval-Augmented Generation technology. It is currently in the development and iteration process.

基于 LangChain + FastAPI 的**本地私有知识库问答系统**。把 PDF / Word / TXT 导入知识库，提问时用向量检索召回原文片段，交给大模型生成**带出处**的回答。

**定位**：单用户、本地运行、不对外发布。

**当前状态**：M1（文档索引）和 M2（检索生成）已完成，M3 Web API 已完成到聊天 SSE；UI 壳已从 `prototype/ui` 迁入 `templates/` + `app/static/`，但聊天页仍使用 mock 数据，T-5.5 还需要接 `/chat` SSE。因此现在可以通过 CLI 和 `/docs` 使用完整问答链路，网页端处于“界面已定稿、数据待接通”的状态。

---

## 目录

- [核心能力](#核心能力)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [提交规范](#提交规范)
- [代码风格与注释](#代码风格与注释)
- [项目文档](#项目文档)
- [开发进度](#开发进度)

---

## 核心能力

| 能力 | 状态 | 说明 |
|---|---|---|
| 多格式摄入 | ✅ 已实现 | PDF / Word / TXT，保留页码与表格结构 |
| 语义检索 | ✅ 已实现 | 向量相似度 Top-K 召回，默认 K=5 |
| 带出处回答 | ✅ 已实现 | CLI/API 回答标注 `[n] 文件名 · 页码`，可展开原文核对 |
| 兜底防幻觉 | ✅ 已实现 | 无有效引用时返回“知识库中没有相关信息。” |
| 文档管理 API | ✅ 已实现 | 列表 / 上传 job / 软删除 / 恢复 / 彻底删除 / 重建 |
| Web API | ✅ 已实现 | FastAPI + SSE；`/docs` 可调通已实现接口 |
| Web 页面 | 🟡 进行中 | UI 壳已迁入 `templates/`；聊天仍为 mock，T-5.5 待接 SSE |
| 多轮对话 | ⏳ 未开始 | Sprint 4（T-6） |
| 一键启动脚本 | ⏳ 未实现 | Sprint 4（T-7.2） |

## 技术栈

| 层 | 选型 |
|---|---|
| 编排框架 | LangChain |
| Web 框架 | FastAPI + Uvicorn + Jinja2 |
| 向量数据库 | Chroma（本地持久化，cosine） |
| 嵌入模型 | 阿里云百炼 `text-embedding-v4`（1024 维） |
| 生成模型 | DeepSeek `deepseek-v4-flash` |
| 文档解析 | PyMuPDF + pdfplumber（PDF）、python-docx（Word） |
| 配置 / 日志 | pydantic-settings / loguru |
| 环境隔离 | Python venv（暂不上 Docker） |

## 目录结构

```
RAG_konwledge_base_system/
├── app/                     # 应用主体
│   ├── api/                 #   chat / documents / health / index 路由
│   ├── core/                #   loader / splitter / embedder / vectorstore / retriever / prompt / llm / qa
│   ├── services/            #   文档管理与后台上传/重建任务
│   ├── static/              #   css / js
│   ├── context.py           #   RequestContext / SecretProvider 边界
│   ├── config.py            #   pydantic-settings
│   └── main.py              #   FastAPI 入口（127.0.0.1:8000）
├── templates/               # Jinja2 页面（index.html 已接入定稿 UI）
├── data/                    # 运行期数据（内容不进版本库）
│   ├── uploads/             #   原始文档
│   └── chroma/              #   向量库持久化
├── docs/                    # 项目文档
├── logs/                    # 日志输出
├── prototype/ui/            # UI 设计留档（不再作为运行页面）
├── scripts/                 # 建库 / 管理 / 问答 / 评估 / 自检脚本
├── tests/                   # 测试
├── .env.example             # 非敏感配置模板
└── .gitignore
```

## 快速开始

### 1. 前置要求

- Python 3.11+
- 已配置好两个**系统环境变量**（不是 `.env`）：

| 环境变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 调用 DeepSeek 生成回答 |
| `DASHSCOPE_API_KEY` | 调用阿里云百炼嵌入模型 |

> Windows 配置方式：`setx DEEPSEEK_API_KEY "sk-xxx"`，**配置后需重开终端**才生效。

### 2. 安装依赖

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1      # PowerShell
# .venv\Scripts\activate.bat    # cmd
# source .venv/bin/activate     # macOS / Linux

python -m pip install -r requirements.txt
```

### 3. 配置

```powershell
Copy-Item .env.example .env
# 按需修改 .env 中的模型名、检索参数、路径
```

`.env` 只放非敏感参数；两个 API Key 继续走系统环境变量。

### 4. 启动 API

```powershell
# 只监听 127.0.0.1:8000
python -m app.main

# 或显式用 uvicorn（开发时可加 --reload）
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后：

- 首页：<http://127.0.0.1:8000/>（定稿 UI 壳；聊天暂为 mock，待接 SSE）
- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

### 5. 使用方式

**CLI 建库 / 管理：**

```powershell
# 建库：默认扫描 data/uploads
.venv\Scripts\python.exe scripts\build_index.py

# 递归扫描、强制重嵌
.venv\Scripts\python.exe scripts\build_index.py --recursive --force

# 列出 / 删除 / 重建（重建会重新消耗嵌入额度）
.venv\Scripts\python.exe scripts\manage_index.py --list
.venv\Scripts\python.exe scripts\manage_index.py --delete <file_hash或文件名>
.venv\Scripts\python.exe scripts\manage_index.py --rebuild
```

**CLI 问答：**

```powershell
.venv\Scripts\python.exe scripts\ask.py "LM358 的电源电压范围是多少？"
.venv\Scripts\python.exe scripts\ask.py --k 3 "Transformer 的核心结构是什么？"
```

**检索命中率评估：**

```powershell
.venv\Scripts\python.exe scripts\eval_retrieval.py
```

**模型连通性自检：**

```powershell
.venv\Scripts\python.exe scripts\check_models.py
```

**Web UI 与设计留档：**

```powershell
# 正式页面：由 FastAPI 提供 templates + app/static
python -m app.main
# http://127.0.0.1:8000/

# 设计留档：旧 prototype，不连接真实 API
python -m http.server 8765 --bind 127.0.0.1 --directory prototype/ui
# http://127.0.0.1:8765/
```

正式页面的 UI 壳已迁入 `templates/` 和 `app/static/`，但聊天数据仍是 mock；T-5.5 接 `/chat` SSE 后才会使用真实回答。`prototype/ui` 仅保留用于对照设计，不再作为运行页面。

---

## 提交规范

**核心原则：一个功能一次提交。** 完成一个任务（对应开发任务清单里的 `T-x.y`）就提交一次，不要攒一大堆一起提交。

### 格式

```
<type>(<scope>): <subject>
```

| 部分 | 说明 |
|---|---|
| `type` | 变更类型，见下表 |
| `scope` | **任务编号**（如 `T-1.3`）或模块名（如 `loader`） |
| `subject` | 中文简述，动宾结构，**不加句号**，不超过 50 字 |

### type 取值

| type | 用于 |
|---|---|
| `feat` | 新增功能 |
| `fix` | 修 bug |
| `docs` | 文档变更 |
| `chore` | 构建 / 配置 / 依赖等杂项 |
| `refactor` | 重构（不改行为） |
| `test` | 增删改测试 |
| `perf` | 性能优化 |
| `style` | 格式调整（不影响逻辑） |

### 示例

```
feat(T-1.3): PDF 按页解析并保留页码
feat(T-3.1): 接入 Qwen 嵌入客户端，支持分批与重试
fix(T-4.1): 修正 Top-K 结果未按相似度降序的问题
docs(T-7.3): 补充环境变量配置说明
test(T-7.1): 为分块器补充边界用例
chore(T-0.2): 锁定依赖版本
```

### 何时提交

```
写完一个可运行的功能  →  提交
阶段验收通过          →  提交并打 tag，如 v0.1-M1
修完一个 bug          →  提交
```

> 提交前用 `git status` 确认**没有把 `.env`、`data/`、`logs/` 加进来**。

---

## 代码风格与注释

**注释是写给人审核的**，目标不是"记录代码做了什么"，而是让人**不查外部文档就能读懂意图与取舍**。

### 三层注释

| 层级 | 要求 | 反面例子 |
|---|---|---|
| **文件头** | 模块 docstring：这个文件负责什么、在整体流程中的哪一环、有什么关键约束 | 直接裸写 import |
| **函数 / 类** | docstring 说明用途；有参数写 `Args`，有返回值写 `Returns`，会抛异常写 `Raises` | 只写 `"""获取数据"""` |
| **关键行内** | 解释**为什么**这么做（尤其踩过坑、有取舍的地方） | `i += 1  # i 加一` |

### 硬性约定

- 注释一律用**中文**；标识符、提交信息里的 type 用英文。
- **标注需求编号**，方便和需求文档 / 开发任务清单对账：

  ```python
  """文档加载器（对应 FR-02 文档解析 / T-1.1 加载器抽象层）。

  按扩展名分发到具体解析器，统一返回 (text, metadata) 结构。
  """
  ```

- **外部接口的硬约束必须就近写明**，并说明后果：

  ```python
  EMBEDDING_BATCH_LIMIT = 10  # 阿里云百炼嵌入接口单次最多 10 条，超出会直接报错
  ```

- **已知限制与取舍要写清楚**，不留"以后再说"式模糊注释。例如 PDF 公式无法识别时，
  要写明"V1 不做公式识别，此处会丢失"。
- 与安全相关的代码（密钥读取、脱敏）必须注明**为什么这样设计**。

### 不要做的事

- 不写复述代码的废话注释（`# 循环遍历列表`）。
- 不留被注释掉的死代码 —— 用 git 就能找回，不需要在源码里保留。
- 不用注释弥补糟糕的命名：能靠改名说清的就改名。

---

## 项目文档

| 文档 | 说明 |
|---|---|
| [`docs/README.md`](docs/README.md) | **文档地图与唯一事实源**：每类事实只在一个文档维护 |
| [`docs/README-开发问题排查.md`](docs/README-开发问题排查.md) | **开发问题排查记录**：真实问题的现象、分析过程、根因、解决方案、验证数据与最终成果 |
| [`docs/架构演进设计.md`](docs/架构演进设计.md) | **架构演进 ADR**：本地优先、可多租户化的模块化单体；租户边界、密钥、存储与服务器迁移路线 |
| [`docs/API契约-v1.0.md`](docs/API契约-v1.0.md) | **前后端 API 契约**：聊天 SSE、上传任务、软删除、Key 设置、错误结构 |
| `docs/进度与续接.md` | **本地专用**（未纳入版本库，见 `.gitignore`）：当前进度、下一步、已踩过的坑、环境速查、开新会话的粘贴模板 |
| [`docs/RAG需求文档-v1.0.md`](docs/RAG需求文档-v1.0.md) | 需求规格：目标、12 条功能需求、6 条非功能需求、验收标准、风险、决策记录 |
| [`docs/RAG开发任务清单-v1.0.md`](docs/RAG开发任务清单-v1.0.md) | 开发任务分解：38 条任务 / 5 个阶段、关键路径、需求覆盖矩阵 |

## 开发进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Sprint 0 | 项目初始化 | 已完成（T-0.1 ~ T-0.6） |
| Sprint 1 | 文档摄入与索引（M1） | 已完成（T-1.1 ~ T-1.7、T-2.1 ~ T-2.3、T-3.1 ~ T-3.6；M1 出口验收通过） |
| Sprint 2 | 检索与生成（M2） | 已完成（T-4.1 ~ T-4.8；M2 出口验收通过） |
| Sprint 3 | Web 化（M3） | 进行中（T-5.1 ~ T-5.4 已完成） |
| Sprint 4 | 多轮与交付（M4） | 未开始 |

详细任务见 [`docs/RAG开发任务清单-v1.0.md`](docs/RAG开发任务清单-v1.0.md)。

---

## 注意事项

- **本仓库为公开仓库**：提交前务必确认没有密钥、原始文档、向量数据被误提交（`.gitignore` 已覆盖，但仍需自查）。
- **API Key 只走系统环境变量**，不写入任何文件。
- **Windows 中文路径**：Chroma 1.5.9 在含中文的目录下不会写 HNSW 索引文件（重启后报 `Error loading hnsw index`）。本机用目录联接 `D:\rag_chroma_link` 指向项目 `data/chroma`，并在 `.env` 设置 `CHROMA_DIR=D:/rag_chroma_link`；其他中文路径环境需同样处理。
- **嵌入维度一经确定不要更改**（当前 1024），否则全部历史向量失效，必须整库重建。
