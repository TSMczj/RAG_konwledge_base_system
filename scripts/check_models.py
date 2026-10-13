"""模型连通性自检脚本（T-0.6）。

一次性验证：
  1. 两个 API Key 是否已正确放入系统环境变量；
  2. DeepSeek 生成模型是否可调用；
  3. 阿里云百炼嵌入模型是否可调用，返回维度是否符合配置；
  4. 若百炼端点不对，自动探测可用端点并给出应写入 .env 的值。

用法（在项目根目录执行）::

    .venv\\Scripts\\python.exe scripts/check_models.py

退出码：0 = 全部通过；1 = 有检查未通过。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 让脚本能 import 到 app 包（脚本位于 <root>/scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import openai  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.config import ConfigError, get_settings  # noqa: E402
from app.logging_config import logger, setup_logging  # noqa: E402

# 百炼可能的 OpenAI 兼容端点（用于自动探测）
DASHSCOPE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("阿里云百炼 · 北京地域", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("阿里云百炼 · 新加坡地域（国际站旧域名）", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
)

LINE = "=" * 68


def _hr(title: str) -> None:
    print()
    print(LINE)
    print(title)
    print(LINE)


def _key_heuristic(name: str, value: str) -> str | None:
    """对密钥格式做启发式检查，返回可疑提示（正常则返回 None）。"""
    n = len(value)
    if len(set(value)) <= 3:
        return f"内容几乎全是重复字符（长度 {n}），疑似占位符或粘贴错误"
    if not value.startswith("sk-"):
        return f"不以 'sk-' 开头（长度 {n}），可能贴错了内容"
    if n < 30:
        return f"长度仅 {n}，短于常见格式（通常 35 位左右）"
    if n > 60:
        return (
            f"长度 {n}，明显长于百炼/DeepSeek 常见的 35 位格式 —— "
            f"若下面报 401，请优先排查这个 Key 是否粘错"
        )
    return None


def check_deepseek(cfg) -> bool:
    """验证 DeepSeek 生成模型。"""
    _hr("[1/2] DeepSeek 生成模型")
    key = cfg.deepseek_api_key.get_secret_value()
    print(f"  端点  : {cfg.deepseek_base_url}")
    print(f"  模型  : {cfg.llm_model}")

    client = OpenAI(api_key=key, base_url=cfg.deepseek_base_url, timeout=120.0, max_retries=0)
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=cfg.llm_model,
            messages=[{"role": "user", "content": "只回复两个字：收到"}],
            max_tokens=256,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - 需要把各类错误都转成可读诊断
        _report_failure(exc, "DeepSeek 生成模型")
        return False

    elapsed = time.perf_counter() - started
    choice = resp.choices[0]
    answer = (choice.message.content or "").strip()
    reasoning = getattr(choice.message, "reasoning_content", None)
    usage = resp.usage

    print()
    print(f"  ✅ 通过   耗时 {elapsed:.2f}s")
    print(f"  回复    : {answer!r}")
    print(f"  结束原因: {choice.finish_reason}")
    if usage:
        line = f"  用量    : 输入 {usage.prompt_tokens} tokens / 输出 {usage.completion_tokens} tokens"
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None
        if reasoning_tokens:
            line += f"（其中思考 {reasoning_tokens}）"
        print(line)

    if reasoning:
        print()
        print("  ⚠️ 该模型默认开启了「思考模式」（返回了 reasoning_content）。")
        print("     对 RAG 问答的影响：响应更慢、token 消耗更高、小 max_tokens 下正文可能为空。")
        print("     → T-4.4 接入生成模型时需显式关闭思考模式，或明确接受其额外开销。")
    elif not answer:
        print()
        print("  ⚠️ 返回正文为空但未检测到思考内容，请检查 max_tokens 是否过小或模型是否被限流。")

    logger.info("check_models: DeepSeek 通过 model={} 耗时={:.2f}s", cfg.llm_model, elapsed)
    return True


def check_dashscope(cfg) -> bool:
    """验证百炼嵌入模型；端点不对时自动探测。"""
    _hr("[2/2] 阿里云百炼 嵌入模型")
    key = cfg.dashscope_api_key.get_secret_value()

    attempts: list[tuple[str, str]] = [(f"配置值", cfg.dashscope_base_url)]
    for label, url in DASHSCOPE_CANDIDATES:
        if url.rstrip("/") != cfg.dashscope_base_url.rstrip("/"):
            attempts.append((label, url))

    last_error: Exception | None = None
    for idx, (label, base_url) in enumerate(attempts, start=1):
        print(f"  尝试 {idx}/{len(attempts)}  [{label}] {base_url}")
        client = OpenAI(api_key=key, base_url=base_url, timeout=60.0, max_retries=0)
        started = time.perf_counter()
        try:
            resp = client.embeddings.create(
                model=cfg.embedding_model,
                input=["这是一段用于验证嵌入接口连通性的测试文本。"],
                dimensions=cfg.embedding_dim,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"           ✗ {_one_line_error(exc)}")
            continue

        elapsed = time.perf_counter() - started
        dim = len(resp.data[0].embedding)
        dim_ok = dim == cfg.embedding_dim
        print()
        print(f"  ✅ 通过   耗时 {elapsed:.2f}s")
        print(f"  返回向量维度 : {dim}" + ("" if dim_ok else f"  ⚠️ 与配置的 {cfg.embedding_dim} 不一致！"))
        print(f"  可用端点     : {base_url}")
        if base_url.rstrip("/") != cfg.dashscope_base_url.rstrip("/"):
            print()
            print("  ⚠️ 当前 .env 里的 DASHSCOPE_BASE_URL 不对，请改成上面这个可用端点。")
        logger.info(
            "check_models: 百炼嵌入通过 model={} dim={} 耗时={:.2f}s endpoint={}",
            cfg.embedding_model, dim, elapsed, base_url,
        )
        return dim_ok

    _report_failure(last_error, "阿里云百炼嵌入模型")
    return False


def _one_line_error(exc: Exception) -> str:
    """把异常压成一行便于阅读的描述。"""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    text = str(exc).replace("\n", " ").strip()
    if len(text) > 160:
        text = text[:157] + "..."
    return f"[{name}{f' {status}' if status else ''}] {text}"


def _report_failure(exc: Exception | None, what: str) -> None:
    """输出失败诊断与可能原因。"""
    print()
    print(f"  ❌ 未通过")
    if exc is None:
        print("     （没有捕获到具体异常）")
        return
    print(f"  错误  : {_one_line_error(exc)}")

    status = getattr(exc, "status_code", None)
    causes: list[str] = []
    if isinstance(exc, openai.AuthenticationError) or status == 401:
        causes = [
            "API Key 无效或已过期",
            "Key 复制时带了空格 / 换行 / 引号",
            "Key 与环境变量名不匹配（比如把百炼的 Key 填进了 DEEPSEEK_API_KEY）",
            "Key 属于另一个地域，但端点填的是本地域（百炼分北京 / 新加坡）",
        ]
    elif isinstance(exc, openai.NotFoundError) or status == 404:
        causes = [
            "模型名拼写错误或该账号没有此模型权限",
            "base_url 路径不对（必须带 /compatible-mode/v1 后缀）",
            "端点地域与 Key 所属地域不一致",
        ]
    elif isinstance(exc, openai.RateLimitError) or status == 429:
        causes = ["触发限流，稍后重试", "账号余额不足 / 未开通该服务"]
    elif isinstance(exc, openai.APIConnectionError):
        causes = [
            "网络不通（本机可能需要代理，或代理配置失效）",
            "base_url 域名写错",
        ]
    else:
        causes = ["请把上面的原始错误信息一并排查"]

    print(f"  可能原因：")
    for c in causes:
        print(f"    · {c}")
    logger.warning("check_models: {} 未通过 - {}", what, _one_line_error(exc))


def main() -> int:
    setup_logging()

    _hr("RAG 本地知识库问答系统 · 模型连通性自检")

    # ---------- 配置加载 ----------
    try:
        cfg = get_settings()
    except ConfigError as err:
        print()
        print("  ❌ 配置检查未通过：")
        print()
        for line in str(err).splitlines():
            print(f"  {line}")
        return 1

    print()
    print("  [配置]")
    print(f"    生成模型     : {cfg.llm_model}")
    print(f"    嵌入模型     : {cfg.embedding_model}（{cfg.embedding_dim} 维）")
    print(f"    DeepSeek 端点: {cfg.deepseek_base_url}")
    print(f"    百炼端点     : {cfg.dashscope_base_url}")

    # ---------- 密钥格式启发式检查 ----------
    print()
    print("  [密钥]")
    suspicious = False
    for env_name, secret in (
        ("DEEPSEEK_API_KEY", cfg.deepseek_api_key),
        ("DASHSCOPE_API_KEY", cfg.dashscope_api_key),
    ):
        raw = secret.get_secret_value()
        print(f"    {env_name:<20} 已设置（{len(raw)} 字符）")
        hint = _key_heuristic(env_name, raw)
        if hint:
            suspicious = True
            print(f"      ⚠️ {hint}")
    if not suspicious:
        print("    格式检查：无明显异常")

    # ---------- 连通性 ----------
    results = [check_deepseek(cfg), check_dashscope(cfg)]

    _hr("结论")
    if all(results):
        print("  ✅ 两项检查全部通过，环境已就绪，可以进入 Sprint 1。")
        logger.info("check_models: 全部通过")
        return 0

    print("  ❌ 存在未通过的检查，请按上面的可能原因逐项排查。")
    print("     修好后再跑一次本脚本即可。")
    logger.error("check_models: 存在未通过项 results={}", results)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
