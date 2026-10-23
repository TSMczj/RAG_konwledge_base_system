# RAG_konwledge_base_system

> A personal knowledge base system constructed based on the Naive Retrieval-Augmented Generation technology. It is currently in the development and iteration process.

基于 LangChain + FastAPI 的**本地私有知识库问答系统**。把 PDF / Word / TXT 导入知识库，提问时用向量检索召回原文片段，交给大模型生成**带出处**的回答，通过浏览器页面使用。

**定位**：单用户、本地运行、不对外发布。

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

| 能力 | 说明 |
|---|---|
| 多格式摄入 | PDF / Word / TXT，保留页码与表格结构 |
| 语义检索 | 向量相似度 Top-K 召回 |
| 带出处回答 | 每条结论标注 `[n] 文件名 · 第 x 页`，可展开原文核对 |
| 多轮对话 | 支持追问，自动改写指代性问题 |
| 兜底防幻觉 | 知识库无相关内容时明确说明，不编造 |
| 文档管理 | 网页上传 / 列表 / 删除 / 重建索引 |

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
│   ├── api/                 #   REST 路由层
│   ├── core/                #   业务核心（解析 / 分块 / 嵌入 / 检索 / 生成）
│   ├── models/              #   Pydantic 数据结构
│   ├── templates/           #   Jinja2 页面
│   └── static/              #   css / js
├── data/                    # 运行期数据（内容不进版本库）
│   ├── uploads/             #   原始文档
│   └── chroma/              #   向量库持久化
├── docs/                    # 项目文档
├── logs/                    # 日志输出
├── scripts/                 # 运维 / 自检脚本
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

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 按需修改 .env 中的模型名、检索参数、路径
```

### 4. 启动

```bash
# 启动服务后访问 http://127.0.0.1:8000
uvicorn app.main:app --reload
```

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
| `docs/进度与续接.md` | **本地专用**（未纳入版本库，见 `.gitignore`）：当前进度、下一步、已踩过的坑、环境速查、开新会话的粘贴模板 |
| [`docs/RAG需求文档-v1.0.md`](docs/RAG需求文档-v1.0.md) | 需求规格：目标、12 条功能需求、6 条非功能需求、验收标准、风险、决策记录 |
| [`docs/RAG开发任务清单-v1.0.md`](docs/RAG开发任务清单-v1.0.md) | 开发任务分解：38 条任务 / 5 个阶段、关键路径、需求覆盖矩阵 |

> 对应的 `.html` 版本为阅读版（任务清单 HTML 支持点击勾选进度）。

## 开发进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Sprint 0 | 项目初始化 | 已完成（T-0.1 ~ T-0.6） |
| Sprint 1 | 文档摄入与索引（M1） | 进行中（T-1.1 ~ T-1.7、T-2.1 ~ T-2.3、T-3.1 已完成） |
| Sprint 2 | 检索与生成（M2） | 未开始 |
| Sprint 3 | Web 化（M3） | 未开始 |
| Sprint 4 | 多轮与交付（M4） | 未开始 |

详细任务见 [`docs/RAG开发任务清单-v1.0.md`](docs/RAG开发任务清单-v1.0.md)。

---

## 注意事项

- **本仓库为公开仓库**：提交前务必确认没有密钥、原始文档、向量数据被误提交（`.gitignore` 已覆盖，但仍需自查）。
- **API Key 只走系统环境变量**，不写入任何文件。
- **嵌入维度一经确定不要更改**（当前 1024），否则全部历史向量失效，必须整库重建。
