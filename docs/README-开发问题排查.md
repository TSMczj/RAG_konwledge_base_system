# 开发问题排查记录（README）

> 这份文档回答三个问题：**开发中遇到了什么、怎么定位、最后怎么解决**。
>
> 它不是 changelog，也不是 API 文档；每个问题都按
> **现象 → 分析 → 根因 → 解决 → 验证 → 经验** 展开，方便后来者遇到相似症状时直接对照。
>
> | 项 | 值 |
> |---|---|
> | 状态 | **M1 归档快照**（不代表当前 M2/M3 状态） |
> | 适用阶段 | Sprint 0 ~ Sprint 1（项目初始化 / 文档摄入与索引 M1） |
> | 代码基线 | `4e7b8b7`（T-3.6 修复，已推送） |
> | 测试 | `63 passed`（归档时点） |
> | 当前主库 | 10 个文件 / 4021 个 chunk |
> | 环境 | Windows + DSH 文件沙箱 · Python 3.11.9 · Chroma 1.5.9 · LangChain 1.4.5 |
> | 最后更新 | 2026-10-24 |
>
> **当前状态不要看本文**：任务完成状态见 [开发任务清单](RAG开发任务清单-v1.0.md)，最新接口见 [API 契约](API契约-v1.0.md)，本机交接见 `进度与续接.md`。本文只保留“当时怎么坏的、怎么修的”。

---

## 一、先看结论

这一阶段真正花时间的，不是“写功能”，而是下面四类问题：

1. **环境层**：Windows 沙箱 ACL、Git 凭据、控制台编码、日志 multiprocessing 管道。
2. **文档解析层**：PDF 每行一个换行导致 chunk 截断；pdfplumber 把注意力可视化图/结构图误判成表格。
3. **向量存储层**：Chroma 1.5.9 在中文路径下不写 HNSW 的 `.bin`，`count()` 正常但重启即坏。
4. **资源层**：1M 嵌入额度，必须把“重建”当成昂贵操作，不能反复 `--force` / `--rebuild`。

最终结果：

- 10 份真实文档入库成功：PDF 5、Word 2、TXT 3；`count()=4021`。
- HNSW `data_level0.bin`、`header.bin`、`length.bin`、`link_lists.bin` 均已落盘，**新进程**可正常 `list` / `query`。
- 真实表格：技术参数 1 张、lm358 55 张、TSLA 55 张、handbook 2 张；attention 的 6 张假表已清零。
- 20 个随机 chunk 无单词/句子中间截断；损坏文件不中断同批解析；纯图片扫描件有告警。
- `--rebuild` 在主库全量执行通过：4021 → 4021，耗时 369s；独立小 collection 也已验证。
- 充值额度后完成 M1 出口验收；War and Peace 不在 `data/uploads`，未参与嵌入。

已知限制（不是“已解决”，不要误读）：

- BERT 第 3/15/16 页的结构图仍有 6 个图注碎片被识别成表格。
- 双栏 PDF 的阅读顺序仍可能交错，行拼接只能解决“行内截断”，不能重建版面。
- V1 不做 OCR，纯扫描件解析为空。
- Chroma 仍依赖 ASCII 路径联接；换机器/迁移时必须重建联接。

---

## 二、排查方法：遇到问题先分层

这个项目的链路是：

```text
文件 → 解析（loader）→ 分块（splitter）→ 嵌入（embedder）→ 存储（vectorstore）→ 检索
```

绝大多数“看起来像 A 的问题”，实际根因在上一层。我的固定顺序是：

1. **先复现，不先改代码**：记录命令、输入文件、完整报错、发生时间、能否稳定复现。
2. **判断问题在哪一层**：不要一上来调 chunk_size 或换模型。
3. **做最小对照**：一次只改一个变量，例如 ASCII 路径 vs 中文路径、`add` vs `upsert`、有日志 vs 无日志。
4. **收集证据**：日志、耗时、`count()`、目录里的 sidecar 文件、进程是否退出、API 返回维度。
5. **在新进程验证**：进程内 `count()` 正常不代表持久化正常；必须关掉再打开。
6. **补回归测试**：把这次失败模式固化成一个断言，避免下次重犯。

### 2.1 症状对应的第一检查点

| 症状 | 第一检查点 | 常见误判 |
|---|---|---|
| 所有命令都报 ACL / Win32 5 | 沙箱 ACL 与父目录继承 | 以为代码没权限，直接改代码 |
| 解析很慢、日志暴涨 | 第三方库 DEBUG 日志 | 以为 PDF 太大或 API 慢 |
| chunk 在单词/句子中间断 | PDF 文本层换行是否被当成分隔符 | 反复调 `CHUNK_SIZE` |
| 表格内容串行/单字符列 | 表格检测是否把图当成了表 | 直接怪分块器 |
| `count()` 正常、重启报 HNSW | 目录里有没有 `.bin` sidecar | 只看 count 就认为成功 |
| 建库“成功”但 CLI 退出码 1 | PowerShell 对 stderr 的包装 | 以为业务失败，重复重建 |
| 重建后额度骤降 | 是否全量重嵌入 | 以为 `--rebuild` 不花钱 |

### 2.2 一条重要原则

**先证明“哪一层坏了”，再决定“改哪一层”。**

例如 chunk 截断，最初看起来像 splitter 参数问题；实际证据是 PDF 文本层每行一个 `\n`，splitter 只是忠实地按 `\n` 切。改 splitter 参数治不好，必须在 loader 把软换行拼回段落。

---

## 三、问题索引

| ID | 问题 | 影响 | 根因 | 状态 |
|---|---|---|---|---|
| ENV-01 | 沙箱写权限全域失败 | 所有 pwsh 命令被阻断 | 工作区根目录缺少 `WRITE_OWNER`，子对象缺少可继承沙箱授权 | 已修复 |
| ENV-02 | Git push 被拒 / 凭据不可用 | 无法推送 | 远端有新提交；沙箱内凭据助手依赖命名管道/控制台 | 已规避 |
| ENV-03 | `check_models.py` 打印 ⚠️ 崩溃 | 误以为 Key 失效 | Windows 控制台 GBK 无法编码 emoji | 已规避 |
| LOG-01 | loguru `enqueue=True` 报 WinError 5 | 日志初始化失败 | multiprocessing 命名管道被沙箱限制 | 已修复（`f017d08`） |
| PDF-01 | 解析 100 页 PDF 10 分钟未完成 | 建库卡死、日志 8.9 MB | pdfminer DEBUG 逐 token 写日志 | 已修复（`4e7b8b7`） |
| PDF-02 | chunk 在行尾/单词中间截断 | 检索片段语义不完整 | PDF 文本层每行一个 `\n`，被 splitter 当分隔符 | 已修复（`4e7b8b7`） |
| PDF-03 | 注意力图/结构图被识别成表格 | 假表格污染检索 | pdfplumber 只按线条检测，图也有线条 | 部分修复（attention 清零；BERT 6 个已知） |
| VEC-01 | `count()` 正常，重启报 HNSW 加载失败 | 索引不可用 | Chroma 1.5.9 在中文路径下不写 HNSW `.bin` | 已修复（ASCII 目录联接） |
| VEC-02 | 客户端未关闭 / API 不兼容 | HNSW 可能未完整落盘；调用报错 | Chroma 客户端需显式 close；langchain-chroma 无公开 upsert/count | 已修复 |
| QUOTA-01 | 1M 嵌入额度接近用完 | 不能反复全量重建 | 多次修复重建 + `--rebuild` 重复嵌入 | 已解决（充值后全量重建 4021→4021） |
| MODEL-01 | DeepSeek 默认思考模式 | 响应慢、token 高、小 `max_tokens` 正文为空 | `deepseek-v4-flash` 默认返回 `reasoning_content` | 已知，T-4.4 处理 |
| TEST-01 | 测试夹具与真实契约不一致 | 误报代码 bug | 合成表格行数不对；`hash-a` 不是 64 位十六进制 | 已修复 |
| BUILD-01 | 后台建库“退出码 1” | 误判建库失败并重跑 | PowerShell 把 stderr 包装成 NativeCommandError；stdout 被缓冲 | 已规避 |

---

## 四、问题详情

### 4.1 ENV-01：Windows 沙箱写权限全域失败

#### 现象

所有 `pwsh` 文件操作都失败，连读取工作区都被阻断：

```text
SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\个人知识库rag系统)
```

错误不是来自项目代码，而是 DSH 在给工作区授予写权限时失败。

#### 分析过程

1. 先用 `diagnose-windows-sandbox-acl` 检查工作区及每一级父目录，而不是直接改项目文件权限。
2. 诊断结果：工作区根目录缺少 `WRITE_OWNER`；已有子对象缺少可继承的沙箱授权和 Low 完整性级别。
3. 只修根目录不够——沙箱进程要能写入已有文件，子对象也必须继承到授权。
4. 修复后仍有 9 个文件属于管理员，当前用户无法接管；不能直接删除重建。

#### 根因

工作区 ACL 在创建/迁移过程中不完整：

- 根目录没有 `WRITE_OWNER`，导致 DSH 无法继续授予写权限；
- 子对象没有继承到沙箱 SID 的写权限；
- 部分文件 owner 是管理员，普通用户修改会被拒绝。

#### 解决

1. 给当前用户补根目录 FullControl，并验证 `writeOwner=true`。
2. 对工作区子树应用可继承授权（仅工作区，不扩大到整个磁盘）：

   ```powershell
   icacls "<workspace>" /grant "<current-user>:(OI)(CI)F" /T
   icacls "<workspace>" /setintegritylevel "(OI)(CI)Low" /T
   ```

3. 对 9 个管理员拥有的文件，用**字节完全相同**的副本替换，替换前后做 SHA-256 校验；原文件备份到独立报告目录。
4. 全程记录到 ACL 修复报告，不关闭沙箱、不把整个磁盘设成 Everyone FullControl。

本次处理了 **29,615 个对象**，9 个文件完成字节级替换，文档数据无丢失。

#### 验证

- 工作区可正常写入、删除、`git status`；
- 项目测试可运行；
- ACL 报告与备份可追溯。

#### 经验

- 遇到“所有命令都 Win32 5”，先查 ACL，不要改业务代码。
- ACL 修复必须限定范围；`/T` 很危险，执行前确认目标绝对路径。
- 替换文件必须做哈希校验和备份，不能“凭感觉”覆盖。

---

### 4.2 ENV-02：Git push 被拒与沙箱凭据问题

#### 现象

- `git push` 报 non-fast-forward：远端有本地没有的 README 提交。
- 沙箱内 `git fetch/push` 无法使用凭据助手，报 `could not read Username`，或卡在 `sh.exe` 命名管道。

#### 分析过程

1. 先 `git fetch` 查看远端提交，确认不是自己误删历史，而是远端 README 更新。
2. 本地提交与远端分叉，需要 rebase，而不是 `push --force`。
3. rebase 后本地提交 hash 会变化，这是正常的，不是内容丢失。
4. 凭据助手需要控制台/命名管道，而沙箱禁止这类 IPC；这是沙箱边界，不是 Git 配置错误。

#### 根因

- 远端分支前进，本地未同步；
- Git credential helper 在受限沙箱中无法完成交互认证。

#### 解决

```powershell
git fetch origin
git rebase origin/main
# 解决冲突后
git push origin main
```

推送时使用一次完全访问权限（仅用于网络凭据），并关闭交互提示：

```powershell
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"
git push origin main
```

#### 验证

- `git status -sb` 显示 `## main...origin/main`；
- 远端提交历史与本地一致；
- 没有把 token、`.env`、`data/` 提交进去。

#### 经验

- push 前先 `fetch`，不要习惯性 `--force`。
- 沙箱内凭据失败是预期边界，不要把 token 写进 remote URL 或日志。
- 完全访问只用于“认证”这一步，不用于日常读写。

---

### 4.3 ENV-03：控制台 GBK 打印 emoji 崩溃

#### 现象

`scripts/check_models.py` 在打印 Key 长度提示时崩溃：

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u26a0'
```

但此时 DeepSeek 和百炼的 Key 其实都已经通过格式检查。

#### 分析过程

1. 报错发生在 `print`，不是 HTTP 请求；
2. 字符 `\u26a0` 是 ⚠️；
3. Windows 控制台代码页是 GBK，Python stdout 编码无法表示该字符；
4. 这不是 Key 失效，也不是 API 401。

#### 根因

CLI 输出包含非 GBK 字符，而进程 stdout 仍按 GBK 编码。

#### 解决

在命令前设置 UTF-8：

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
```

长期建议：CLI 提示尽量用纯 ASCII 的 `[WARN]`，不要依赖 emoji 跨平台显示。

#### 验证

设置编码后重跑：

- DeepSeek：通过，0.69s；
- 百炼嵌入：通过，1.40s，返回 1024 维；
- 结论：环境已就绪。

顺带确认：百炼 Key 长度明显长于常见格式时脚本会提示，但**长度警告不等于 Key 无效**，最终以真实请求结果为准。

#### 经验

- 看到 `UnicodeEncodeError` 先找输出字符，不要先怀疑业务。
- 跨平台 CLI 尽量不用 emoji；必须用时显式设置输出编码。

---

### 4.4 LOG-01：loguru `enqueue=True` 与命名管道

#### 现象

日志初始化时报：

```text
PermissionError: [WinError 5] ...
```

位置在 loguru 创建 multiprocessing 队列/命名管道时。

#### 分析过程

1. 项目是本地单用户、单进程应用，本不需要异步日志队列；
2. `enqueue=True` 会让 loguru 通过 multiprocessing 管道把日志交给后台线程/进程；
3. DSH 沙箱禁止程序创建命名管道，因此初始化直接失败；
4. 这不是日志目录权限问题，改文件路径无效。

#### 根因

沙箱的 IPC 限制与 loguru 的 multiprocessing 日志模式冲突。

#### 解决

文件 sink 改为：

```python
enqueue=False
```

提交 `f017d08`。测试中把 `setup_logging` monkeypatch 掉，避免 pytest 捕获流被关闭后 loguru 仍持有旧 sink。

#### 验证

- CLI 正常启动并写 `logs/app.log`；
- 不再出现 WinError 5；
- 测试全绿。

#### 经验

单进程本地应用不需要 `enqueue=True`；在受限环境里，任何“后台队列/命名管道”都要先怀疑。

---

### 4.5 PDF-01：pdfminer DEBUG 把解析拖到 10 分钟

#### 现象

第一次真实建库：

- Python 进程 CPU 跑了 10 分钟；
- `logs/app.log` 涨到 **8.92 MB**；
- Chroma 里仍然是 `count=0`；
- 日志几乎全是：

```text
pdfminer.psparser:nexttoken ...
pdfminer.pdfinterp:execute ...
```

#### 分析过程

1. 先看进程：CPU 很高，说明不是网络等待，也不是 API 限流；
2. 看日志：pdfminer 在逐 token 打印，说明日志级别过低；
3. 看 `logging_config.py`：`basicConfig(level=0)` 接管了标准库日志，文件 sink 是 DEBUG；
4. pdfminer 的 DEBUG 量级与 PDF token 数成正比，100 页文档会产生海量 I/O；
5. 解析还没结束，根本轮不到嵌入，所以 Chroma 一直是 0。

#### 根因

第三方解析库的 DEBUG 日志没有被压制，日志 I/O 成为解析瓶颈。

#### 解决

在 `setup_logging` 的噪音列表中增加：

```python
("pdfminer", logging.WARNING),
("pdfplumber", logging.WARNING),
```

同时后台运行使用 `python -u`，避免 stdout 块缓冲看不到进度。

#### 验证

- 68 页 `lm358.pdf`：解析 23.50s；
- 113 页 TSLA 10-K：解析 29.45s；
- 日志不再暴涨；
- 后续 10 份文档建库约 4~5 分钟完成（含嵌入）。

#### 经验

- “解析慢”先看日志量和 CPU，不要先换 PDF 库。
- 接管标准库日志时，必须显式压制 pdfminer、httpx、chromadb 等噪音源。
- 关键结论：**日志级别也是性能参数**。

---

### 4.6 PDF-02：PDF 软换行导致 chunk 在行中截断

#### 现象

真实文档抽样时，chunk 结尾出现：

```text
... to pre-
... irically
... 个人信
```

中文法条甚至在“接收 / 处理者”中间断开。

#### 分析过程

1. 先怀疑分块器参数：`CHUNK_SIZE=500`、`overlap=80` 是否太小；
2. 看 PDF 文本层：PyMuPDF 的 `get_text("text")` 对每一视觉行输出一个 `\n`；
3. 分块器的分隔符列表把 `\n` 当成段落边界，于是每个视觉行都可能成为切点；
4. 论文是双栏排版，行尾还常见 `pre-\ntrained` 这种连字符换行；
5. 结论：不是 splitter 参数问题，是 loader 没有把“软换行”还原成段落。

#### 根因

PDF 的“视觉换行”被误当成“语义段落”，分块器在行尾切分。

#### 解决

在 loader 增加 `_join_wrapped_lines()`：

- 按空行分段落；
- 段内行尾是 `-`：直接拼接（`pre-` + `trained` → `pre-trained`）；
- 中文字符/中文标点相邻：不加空格；
- 其他情况加一个空格；
- 在 `_page_text_excluding_tables()` 的“无表格页”和“按 block 取正文”两条路径都应用；
- 分块器补充英文句末分隔符 `". "`、`"! "`、`"? "`。

#### 验证

修复后各 PDF 的 chunk 数变化（因为切点变了）：

| 文档 | 修复前 | 修复后 |
|---|---:|---:|
| attention | 96 | 102 |
| bert | 168 | 180 |
| lm358 | 229 | 237 |
| TSLA 10-K | 972 | 1063 |
| 个人信息保护法 | 31 | 31 |

随机 20 个 chunk 抽样：没有单词/句子中间截断；少量结束于标题/页脚，属正常边界。

#### 经验

- 分块质量差，先检查“进入 splitter 之前的文本”长什么样。
- 在 loader 做格式相关的换行归一化，比在 splitter 里加特判更干净。
- 表格段不能走行拼接，否则会破坏 Markdown 表格结构。

---

### 4.7 PDF-03：注意力图/结构图被识别成表格

#### 现象

`attention is all you need.pdf` 第 13–15 页出现 6 个“表格”，内容是：

```text
|  |  |  | It |  |  | is |  | ... |
```

列数 33–59，大量空单元格，单词被拆成单字符列；BERT 第 3/15/16 页也有类似的结构图碎片。

#### 分析过程

1. 先看原页图：第 13/14 页是注意力可视化图，不是表格；
2. pdfplumber 默认按线条检测，图里的连接线/文本框被当成表格网格；
3. 统计真假表格结构：

   | 类型 | 列数 | 数字占比 |
   |---|---:|---:|
   | attention 可视化图 | 33–59 | 0.00–0.11 |
   | TSLA 财报 | 22–31 | 0.40–0.62 |
   | lm358 小表 | 6 | 0.25 |

4. 先尝试“单元格密度 ≥20%”规则：attention 被过滤了，但 lm358 真实稀疏表从 55 张掉到 45 张，误伤严重，撤销；
5. 最终改用“列数上限 + 宽表数字占比”：既保留财报，又过滤注意力图。

#### 根因

pdfplumber 是**基于线条**的表格检测，不理解“这是图还是表”；注意力可视化图的线条和文字排布恰好满足表格网格特征。

#### 解决

在 `_is_useful_table()` 增加：

```python
_TABLE_MAX_COLS = 32
_TABLE_WIDE_COLS = 20
_TABLE_WIDE_MIN_NUMERIC = 0.15
```

规则：

- 行数 < 2、列数 < 2、非空单元格 < 4：拒绝；
- 列数 > 32：拒绝；
- 列数 > 20 且数字占比 < 15%：拒绝；
- 其余保留。

#### 验证

| 文档 | 表格 chunk | 结果 |
|---|---:|---|
| 2.技术参数.docx | 1 | 正常 |
| lm358.pdf | 55 | 正常，含小稀疏表 |
| TSLA 10-K | 55 | 正常，财报宽表保留 |
| handbook-872p.docx | 2 | 正常 |
| attention.pdf | 0 | 假表清零 |
| bert.pdf | 6 | 结构图碎片，已知限制 |

查询 `LM358 的电源电压范围是多少？` 能命中 `lm358.pdf` 的规格表，说明真实表格仍在。

#### 经验

- 不要用一个“看起来很美”的启发式就上线；必须拿真实文档回归。
- 过滤规则要同时看“误杀”和“漏放”，并记录取舍。
- BERT 的 6 个图注碎片是 pdfplumber 线条检测的边界，V1 接受，不为了它引入 OCR/版面分析。

---

### 4.8 VEC-01：Chroma 中文路径下 HNSW 不落盘

这是本轮最隐蔽、代价最大的问题。

#### 现象

建库时：

```text
写入 chunk: 1507
库内总量: 1507
```

但新进程执行 `manage_index.py --list`：

```text
向量库初始化失败: InternalError:
Error executing plan: Error sending backfill request to compactor:
Error constructing hnsw segment reader: Error loading hnsw index
```

检查目录，只看到：

```text
data/chroma/<segment>/index_metadata.pickle
```

没有 `data_level0.bin`、`header.bin`、`length.bin`、`link_lists.bin`。

#### 分析过程

1. **先怀疑 close**：Chroma 的 HNSW 需要显式关闭客户端；
2. 做小实验：20 条向量 `add` / `upsert`，关闭后目录里有 `.bin`，重开正常——说明 close 机制本身没问题；
3. 再怀疑数据量：1507 条本地向量一次性 `upsert` 也能正常落盘；
4. 做 A/B：把 `persist_directory` 放在 ASCII 临时目录 → 有 `.bin`；放在中文目录 → 只有 `sqlite3`/`metadata`；
5. 尝试 Windows 8.3 短路径：`个人知~1` 仍含中文，无效；
6. 尝试 `subst` 映射盘符 / 在 `D:\` 根目录建 ASCII 目录：沙箱拒绝；
7. 最终方案：建一个 **ASCII 目录联接**：

   ```text
   D:\rag_chroma_link  →  D:\个人知识库rag系统\data\chroma
   ```

   Chroma 看到的是 `D:\rag_chroma_link`，实际数据仍在项目 `data/chroma`；
8. 用 1507 条本地向量通过联接测试：`.bin` 全部生成，关闭后重开 count=1507，查询正常。

#### 根因

Chroma 1.5.9 的 HNSW 索引 sidecar 文件在**非 ASCII 路径**下没有正确写盘；SQLite 元数据正常，所以 `count()` 看起来是对的，但 HNSW 段加载失败。

`close()` 是必要条件，但不是充分条件；**路径才是这次的主因**。

#### 解决

1. 创建目录联接（只需一次）：

   ```powershell
   New-Item -ItemType Junction `
     -Path "D:\rag_chroma_link" `
     -Target "D:\个人知识库rag系统\data\chroma"
   ```

2. `.env`：

   ```dotenv
   CHROMA_DIR=D:/rag_chroma_link
   ```

3. `VectorStore` 增加 `close()`，`build_index.py` / `manage_index.py` 在 `finally` 中调用，确保 HNSW 完整落盘。
4. README / `.env.example` / 进度文档记录该限制，提醒不要删除联接。

#### 验证

- `data/chroma` 下出现 `data_level0.bin`（约 17 MB）、`header.bin`、`length.bin`、`link_lists.bin`；
- 新进程 `manage_index.py --list` 正常；
- 新进程查询正常；
- 测试 `test_close_flushes_and_can_reopen` 通过。

#### 经验

- **不要用 `count()` 证明持久化成功**；必须新进程打开并实际查询。
- HNSW 不是单个文件，`index_metadata.pickle` 存在不代表索引可用。
- Windows 中文路径是实际风险；能 ASCII 就 ASCII，不能就目录联接。
- `D:\rag_chroma_link` 不要删除/改名，否则下次会重新落到中文路径并损坏。

---

### 4.9 VEC-02：Chroma 客户端关闭与 langchain-chroma API 差异

#### 现象

- 建库进程结束后 HNSW 文件不完整；
- `langchain-chroma` 没有公开的 `upsert` / `count`；
- 调用 `collection.get(include=["ids"])` 报参数错误；
- 测试里 collection 名用 `"c"` 报“名称至少 3 个字符”。

#### 分析过程

1. `langchain_chroma.Chroma` 是对 `chromadb` 的封装，部分底层能力没有暴露；
2. 项目需要按 `chunk_id` 幂等 upsert，不能只用 `add_texts`；
3. 因此通过 `_store._collection` 拿到底层 collection，并集中封装；
4. `get()` 的 `include` 不允许 `"ids"`，但返回值天然包含 ids；
5. Chroma 对 collection 名有长度/字符集约束。

#### 根因

封装层 API 与底层 API 不完全一致；关闭时机也没有被 CLI 统一保证。

#### 解决

- `VectorStore` 内部统一使用 `self._collection.upsert(...)`，并在注释里说明为什么绕过公开 API；
- `get` 不再传 `"ids"`；
- 测试 collection 名使用合法名称；
- `VectorStore.close()` 封装 `_client.close()`，CLI 在 `finally` 中调用；
- 测试覆盖 `close → reopen`。

#### 验证

- 63 项测试通过；
- 建库、列表、查询在新进程均正常；
- HNSW sidecar 完整。

#### 经验

- 用封装库时，先确认它到底暴露了哪些能力；必要时用底层 API，但要把原因写在代码里。
- “进程内能用”不等于“持久化正确”，关闭与重开必须进测试。

---

### 4.10 QUOTA-01：1M 嵌入额度下的重建策略

#### 现象

- 用户只有 1M 百炼嵌入 token；
- 最初 10 份文档估算约 127 万 token，超过额度；
- 移除 War and Peace（约 87 万）后，9 份约 40 万；
- 但本轮为修 HNSW 路径、表格误判、PDF 行截断，多次重建，额度迅速接近用完；
- 主库全量 `--rebuild` 预计还要约 40 万，当时暂不能执行；
- 充值额度后已执行全量重建：4021 → 4021，耗时 369s。

#### 分析过程

1. 按文件字符数估算 token：英文约 4 字符/token，中文约 1 字/token（保守上限）；
2. 逐文件列 chunk 数和估算，先判断“能不能跑”，再决定是否移除超大文件；
3. 发现 `build_index` 本身按 `file_hash` 跳过已存在文件，重复运行不花钱；
4. 但 `--force` / `--rebuild` 会重新解析、重新嵌入，是纯消耗；
5. 修改 loader/splitter 后，chunk 数变化会导致 `index_chunks` 重写该文件，这也需要预算；
6. 因此修复期间只对受影响文档做删除/重索引，不做全量重建；
7. 额度恢复后再执行一次主库全量 `--rebuild`，作为 T-3.6 的最终验收。

#### 根因

嵌入额度是硬预算，而“重建”在代码语义上等于“全部重新嵌入”。

#### 解决

- 建库前先做逐文件 token 估算；
- 移除 War and Peace 这类超大文件；
- 日常增量建库依赖 `file_hash` 跳过；
- 只对受影响的文件 `delete_by_file_hash` + 重新建库；
- 修 loader/splitter 后先在本地 `load_document + split_document` 看 chunk 数，再决定是否入库；
- 额度恢复后执行主库全量 `--rebuild`，一次性验证“清空 → 重新解析 → 重新嵌入 → 数量一致”。

#### 验证

- 增量建库：`扫描 11 / 解析成功 10 / 新入库 2 / 已存在跳过 8`；
- 只重建 attention：`新入库 1 / 已存在跳过 7 / 写入 96`；
- 最终主库 10 文件 / 4021 chunk；
- 主库全量 `--rebuild`：清空 4021 → 重新写入 4021，耗时 369s，退出码 0；
- 独立小 collection 的 `--rebuild` 2 → 2 通过。

#### 经验

- 把 1M 当成硬上限；每次 `--force` / `--rebuild` 前先算账。
- 修复解析/分块后，chunk 数会变，不能假设“只改代码不花钱”。
- 生产库的全量重建应单独安排，不在调试循环里反复跑。

---

### 4.11 MODEL-01：DeepSeek 默认思考模式

#### 现象

`check_models.py` 调用 `deepseek-v4-flash`：

- 输入 35 tokens，输出 26 tokens，其中 **24 个是思考 token**；
- 回复只有“收到”；
- 如果 `max_tokens` 设得小，正文可能为空。

#### 分析过程

1. 先确认 HTTP 成功、模型名正确；
2. 看响应字段：存在 `reasoning_content`，说明模型默认开启思考；
3. 思考 token 也计费，且会增加首 token 延迟；
4. RAG 问答通常不需要长思考，尤其本地单用户场景。

#### 根因

`deepseek-v4-flash` 默认开启思考模式，不是代码 bug。

#### 解决

T-4.4 接入生成模型时二选一：

- 显式关闭思考模式（推荐，响应更快、成本更低）；
- 或明确接受额外开销，并把 `max_tokens` 调大，避免正文为空。

当前只在 `check_models.py` 输出告警，尚未改生成客户端。

#### 验证

`check_models` 能通过，但会打印思考模式告警；T-4.4 必须处理。

#### 经验

模型“能通”不等于“适合 RAG”；要同时看延迟、思考 token、空正文风险。

---

### 4.12 TEST-01：测试夹具与真实契约不一致

#### 现象

- T-1.4 合成表格测试失败，怀疑表格解析代码错了；
- T-3.5 测试用 `hash-a` 删除，CLI 却当成文件名，删除 0 个；
- T-3.5 CLI 测试上传目录里放了坏文件，期望退出 0，实际退出 1。

#### 分析过程

1. T-1.4：检查测试 PDF 的网格线，只有 2 个行间隔，但数据有 3 行；是测试几何不对，不是解析器；
2. T-3.5：CLI 用 `^[0-9a-fA-F]{64}$` 判断哈希，`hash-a` 不匹配，自然按文件名处理；
3. T-3.5：CLI 遇到坏文件返回 1 是正确行为，测试却期望 0；
4. 结论：三次都是测试契约写错，不是业务代码错。

#### 根因

测试夹具没有模拟真实输入约束（表格行数、64 位哈希、干净上传目录）。

#### 解决

- 修正合成表格的网格几何，保留 3 行数据；
- 测试哈希改为 64 位十六进制：`"a" * 64`、`"b" * 64`；
- CLI 测试使用干净上传目录，期望 0；坏文件场景单独测退出 1。

#### 验证

测试总数从 54 增至 63，全部通过。

#### 经验

- 测试失败先问“夹具是否真的符合契约”，不要立刻改实现。
- CLI 的输入契约（哈希格式、目录内容）要写进测试名/注释。

---

### 4.13 BUILD-01：后台建库“退出码 1”与 stdout 缓冲

#### 现象

- 后台执行建库，PowerShell 报 `NativeCommandError`，任务退出码 1；
- 但日志末尾明明写着“建库完成 / 处理失败 0”；
- 运行中长时间看不到进度。

#### 分析过程

1. 看日志：业务统计正常，不是解析/嵌入失败；
2. 看命令：使用了 `2>&1`，PowerShell 把 Python 的 stderr 日志包装成 ErrorRecord；
3. 看输出：Python stdout 在管道下是块缓冲，进度被憋住；
4. 结论：退出码 1 是 PowerShell 包装层，不是 `build_index.py` 的业务返回值。

#### 根因

- PowerShell 对 native stderr 的处理方式；
- Python 非交互 stdout 的块缓冲。

#### 解决

```powershell
.venv\Scripts\python.exe -u scripts/build_index.py --dir data/uploads *> build.log
```

- `-u`：不缓冲；
- `*>`：统一重定向，避免 PowerShell 把 stderr 当异常；
- 以日志中的“建库完成 / 处理失败 0 / 库内总量”判断业务结果，不只看退出码。

#### 验证

最终建库退出码 0，统计正确；进度可实时查看。

#### 经验

PowerShell 下跑 Python CLI，优先 `python -u` + 文件重定向；退出码与业务结果要分开判断。

---

## 五、最终成果

### 5.1 代码与测试

| 项 | 结果 |
|---|---|
| 修复提交 | `4e7b8b7 fix(T-3.6): 修复 Chroma 中文路径、PDF 行拼接与表格误判` |
| 推送 | `main -> origin/main` |
| 测试 | `63 passed` |
| 工作区 | clean |

涉及文件：

- [`app/core/loader.py`](../app/core/loader.py)：PDF 行拼接、表格过滤、已知限制注释
- [`app/core/splitter.py`](../app/core/splitter.py)：英文句末分隔符
- [`app/core/vectorstore.py`](../app/core/vectorstore.py)：`close()`、底层 collection 封装
- [`app/logging_config.py`](../app/logging_config.py)：压制 pdfminer/pdfplumber DEBUG
- [`scripts/build_index.py`](../scripts/build_index.py) / [`scripts/manage_index.py`](../scripts/manage_index.py)：CLI 结束时关闭 Chroma
- [`tests/test_loader.py`](../tests/test_loader.py)：表格过滤与行拼接回归测试
- [`tests/test_vectorstore.py`](../tests/test_vectorstore.py)：close → reopen 测试

### 5.2 真实文档结果

| 文档 | chunk | 页数 | 备注 |
|---|---:|---:|---|
| `10_项目说明.txt` | 13 | 0 | README 转 TXT，作为第 10 份 |
| `2.技术参数.docx` | 6 | 0 | 1 张表格 |
| `attention is all you need.pdf` | 102 | 15 | 假表格已清零 |
| `bert.pdf` | 180 | 16 | 6 个结构图碎片，已知限制 |
| `gbk.txt` | 1 | 0 | GBK 编码 |
| `handbook-872p.docx` | 2387 | 0 | 872 页手册 |
| `lm358.pdf` | 237 | 68 | 55 张真实表格 |
| `nasdaq-tsla-2025-10K-25570807.pdf` | 1063 | 113 | 55 张财报表格 |
| `UTF-8.txt` | 1 | 0 | UTF-8 编码 |
| `中华人民共和国个人信息保护法.pdf` | 31 | 21 | 中文法条 |
| **合计** | **4021** | — | 10 个文件 |

### 5.3 验收结果

| 验收项 | 结果 |
|---|---|
| 10 份混合格式解析成功率 | 10/10 = 100% |
| `count()` 与预期 | 4021，和逐文件合计一致 |
| 20 chunk 句子边界 | 无单词/句子中间截断；5 个结束于标题/页脚 |
| 真实表格 | 技术参数 1、lm358 55、TSLA 55、handbook 2 |
| 损坏文件 | 随机字节 PDF/DOCX 记录失败，正常文件继续 |
| 扫描件 | 有“疑似扫描件/无文本层”告警，不中断 |
| 删除/重索引 | attention、个人信息保护法均验证 |
| 独立 collection `--rebuild` | 2 → 2 |
| 主库全量 `--rebuild` | **已完成：4021 → 4021，369s，退出码 0** |

### 5.4 怎么复现验证

```powershell
# 1. 环境与模型
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
.venv\Scripts\python.exe scripts/check_models.py

# 2. 增量建库（已存在会按 file_hash 跳过）
.venv\Scripts\python.exe -u scripts/build_index.py --dir data/uploads

# 3. 查看文件 / chunk
.venv\Scripts\python.exe -u scripts/manage_index.py --list

# 4. 测试
.venv\Scripts\python.exe -m pytest -q

# 5. 确认真实 HNSW sidecar 存在
Get-ChildItem data\chroma -Recurse -File |
  Where-Object { $_.Extension -eq ".bin" } |
  Select-Object Name, Length
```

前提：

- `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` 已在系统环境变量中；
- `.env` 的 `CHROMA_DIR=D:/rag_chroma_link`；
- `D:\rag_chroma_link` 目录联接存在且指向 `data/chroma`。

---

## 六、已知限制与后续

| 限制 | 现状 | 后续 |
|---|---|---|
| BERT 结构图碎片被当表格 | 6 个 chunk | V1 接受；如要彻底解决需版面分析/图像分类 |
| 双栏 PDF 阅读顺序 | 行内截断已修，跨栏仍可能交错 | 后续可评估版面分析或按 block 排序 |
| 纯图片扫描件 | 无文本层，空结果 + 告警 | V1 不做 OCR；需要时单独接 OCR |
| Chroma 中文路径 | 用 ASCII 目录联接规避 | 换机器/迁移时必须重建联接 |
| 1M 嵌入额度 | 已充值并完成全量重建（4021→4021） | 后续按需重建，仍不反复 `--force` / `--rebuild` |
| DeepSeek 思考模式 | 尚未在生成客户端关闭 | T-4.4 显式关闭或接受开销 |
| T-3.6 任务状态 | 已完成（M1 出口验收） | 后续进入 Sprint 2 · T-4.1 |

---

## 七、给后来者的排查清单

遇到新问题，按这个顺序走：

- [ ] 记录完整命令、输入文件、报错、时间、是否稳定复现；
- [ ] 判断在“解析 / 分块 / 嵌入 / 存储 / 检索”哪一层；
- [ ] 做最小对照，一次只改一个变量；
- [ ] 看日志量、耗时、`count()`、目录 sidecar、进程是否退出；
- [ ] 用**新进程**验证持久化；
- [ ] 把失败模式补成回归测试；
- [ ] 修复后更新 README / 进度文档 / 已知限制；
- [ ] 涉及额度、ACL、凭据的操作，先备份、先算账、限定范围。

### 7.1 不要做的事

- 不要用 `count()` 单独证明 Chroma 持久化成功；
- 不要在中文路径下直接跑 Chroma HNSW；
- 不要为了过滤几种假表格，把真实稀疏表格一起误杀；
- 不要在调试循环里反复 `--force` / `--rebuild`；
- 不要把 API Key、ACL 报告、用户 SID 写进仓库；
- 不要用 `push --force` 解决 non-fast-forward。

---

## 八、问题记录模板

以后新增问题，直接复制：

```markdown
### ID：一句话标题

#### 现象
（完整报错 / 复现命令 / 影响范围）

#### 分析过程
1. 先查了什么
2. 做了什么对照
3. 排除了哪些假设

#### 根因
（一句话说清；区分“直接原因”和“根本原因”）

#### 解决
（代码 / 配置 / 命令；说明为什么不是别的方案）

#### 验证
（测试、真实数据、新进程、计数、日志）

#### 经验
（下次如何更快定位 / 如何避免）
```

---

> 这份文档会随着后续 Sprint 继续补充。新增问题时，优先记录**排查路径**，而不只是“最后改了哪一行”——
> 因为下一次的报错文本很可能不一样，但排查路径可以复用。
