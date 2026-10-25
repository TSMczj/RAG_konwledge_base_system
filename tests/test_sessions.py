"""T-6.1 内存会话历史测试。"""

from __future__ import annotations

from app.services.sessions import SessionStore


def test_history_keeps_recent_turns_and_trims() -> None:
    store = SessionStore()
    for index in range(7):
        store.append(
            "s1",
            question=f"问题 {index}",
            answer=f"回答 {index}",
            max_turns=5,
        )

    history = store.history("s1")
    assert len(history) == 5
    assert history[0].question == "问题 2"
    assert history[-1].question == "问题 6"


def test_sessions_are_isolated_and_clearable() -> None:
    store = SessionStore()
    store.append("s1", question="问题", answer="回答")
    assert store.history("s2") == ()
    store.clear("s1")
    assert store.history("s1") == ()
