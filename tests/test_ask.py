"""T-4.6 问答 CLI 测试。"""

from __future__ import annotations

from types import SimpleNamespace

import scripts.ask as ask_module
from app.core.qa import Citation, NO_ANSWER


def _citation() -> Citation:
    return Citation(
        n=1,
        file_name="lm358.pdf",
        page=5,
        chunk_id="c1",
        kind="table",
        score=0.734,
        excerpt="Supply voltage 3 to 36 V",
    )


def _patch(monkeypatch, service) -> None:
    monkeypatch.setattr(ask_module, "setup_logging", lambda: None)
    monkeypatch.setattr(ask_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(ask_module, "QAService", lambda settings=None: service)


def test_cli_prints_answer_and_citations(monkeypatch, capsys) -> None:
    class Service:
        def answer(self, question, *, k=None):
            assert question == "LM358 的电源电压范围是多少？"
            assert k == 3
            return SimpleNamespace(
                question=question,
                answer="3 V 到 36 V [1]",
                citations=(_citation(),),
                elapsed_ms=1234,
                no_context=False,
            )

    _patch(monkeypatch, Service())
    exit_code = ask_module.main(["LM358 的电源电压范围是多少？", "--k", "3"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "回答：3 V 到 36 V [1]" in output
    assert "[1] lm358.pdf · 第 5 页 · table · 0.734" in output
    assert "耗时：1.23s" in output


def test_cli_prints_fallback_without_citations(monkeypatch, capsys) -> None:
    class Service:
        def answer(self, question, *, k=None):
            return SimpleNamespace(
                question=question,
                answer=NO_ANSWER,
                citations=(),
                elapsed_ms=100,
                no_context=True,
            )

    _patch(monkeypatch, Service())
    exit_code = ask_module.main(["库外问题"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert NO_ANSWER in output
    assert "知识库中没有可用依据" in output


def test_cli_rejects_empty_question(monkeypatch) -> None:
    _patch(monkeypatch, SimpleNamespace())
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    assert ask_module.main([]) == 1


def test_cli_reports_service_error(monkeypatch, capsys) -> None:
    class Service:
        def answer(self, question, *, k=None):
            raise RuntimeError("boom")

    _patch(monkeypatch, Service())
    exit_code = ask_module.main(["问题"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "问答失败" in output
