"""检索命中率评估（对应 T-4.7）。

用法：
    .venv\\Scripts\\python.exe scripts/eval_retrieval.py
    .venv\\Scripts\\python.exe scripts/eval_retrieval.py --k 5 --threshold 0.85
    .venv\\Scripts\\python.exe scripts/eval_retrieval.py --json

判定：某问题的 Top-K 中只要出现一个 expected_sources，即算命中。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, get_settings
from app.core.retriever import Retriever
from app.logging_config import logger, setup_logging

DEFAULT_QUESTIONS = PROJECT_ROOT / "tests" / "eval_questions.json"


def _percentile(values: list[float], ratio: float) -> float:
    """线性插值百分位；用于 P50/P95。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("测试集必须是非空 JSON 数组")
    return data


def evaluate(questions: list[dict], *, k: int, retriever: Retriever) -> dict:
    """执行评估，返回逐题结果与汇总。"""
    records: list[dict] = []
    for index, item in enumerate(questions, start=1):
        question = str(item.get("question", "")).strip()
        expected = item.get("expected_sources") or []
        if isinstance(expected, str):
            expected = [expected]
        expected_set = {str(name) for name in expected}
        if not question:
            raise ValueError(f"第 {index} 条问题为空")

        result = retriever.retrieve(question, k=k)
        top_files = [entry.file_name for entry in result.items]
        rank = next(
            (pos for pos, name in enumerate(top_files, start=1) if name in expected_set),
            None,
        )
        records.append(
            {
                "id": item.get("id") or f"Q{index:02d}",
                "question": question,
                "expected_sources": sorted(expected_set),
                "top_sources": top_files,
                "hit": rank is not None,
                "rank": rank,
                "elapsed_ms": result.elapsed_ms,
            }
        )

    hits = sum(1 for record in records if record["hit"])
    latencies = [record["elapsed_ms"] for record in records]
    return {
        "k": k,
        "total": len(records),
        "hits": hits,
        "hit_rate": hits / len(records) if records else 0.0,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "max_ms": max(latencies) if latencies else 0.0,
        "records": records,
    }


def _print_report(report: dict) -> None:
    for record in report["records"]:
        mark = "HIT " if record["hit"] else "MISS"
        rank = f"rank={record['rank']}" if record["hit"] else "rank=-"
        print(
            f"[{record['id']}] {mark:<5} {rank:<8} "
            f"{record['elapsed_ms']:>7.1f}ms  {record['question']}"
        )
        if not record["hit"]:
            print(f"      期望: {', '.join(record['expected_sources'])}")
            print(f"      实际: {', '.join(record['top_sources'][:5])}")

    print()
    print("=" * 68)
    print(
        f"Top-{report['k']} 命中: {report['hits']}/{report['total']} "
        f"= {report['hit_rate'] * 100:.1f}%"
    )
    print(
        f"检索耗时: P50 {report['p50_ms']:.1f}ms · "
        f"P95 {report['p95_ms']:.1f}ms · max {report['max_ms']:.1f}ms"
    )
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="RAG 检索 Top-K 命中率评估")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    setup_logging()

    try:
        settings = get_settings()
    except ConfigError as err:
        print("配置检查未通过：")
        print(err)
        return 1

    try:
        questions = _load_questions(args.questions)
        retriever = Retriever(settings=settings)
        report = evaluate(questions, k=args.k, retriever=retriever)
    except Exception as exc:  # noqa: BLE001
        logger.error("评估失败: {}: {}", type(exc).__name__, exc)
        print(f"评估失败：{type(exc).__name__}: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)

    return 0 if report["hit_rate"] >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
