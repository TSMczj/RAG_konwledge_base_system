"""进程内多轮会话历史（T-6.1 / FR-10）。

V1 只保存在内存，进程重启即清；按 session_id 保存最近 N 轮。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class Turn:
    """一轮已完成的问答。"""

    question: str
    answer: str
    rewritten_query: str = ""


class SessionStore:
    """线程安全的内存会话表。"""

    def __init__(self) -> None:
        self._turns: dict[str, deque[Turn]] = {}
        self._lock = Lock()

    def history(self, session_id: str) -> tuple[Turn, ...]:
        with self._lock:
            return tuple(self._turns.get(session_id, ()))

    def append(
        self,
        session_id: str,
        *,
        question: str,
        answer: str,
        rewritten_query: str = "",
        max_turns: int = 5,
    ) -> None:
        if not session_id or not question.strip():
            return
        limit = max(1, int(max_turns))
        with self._lock:
            turns = self._turns.setdefault(session_id, deque(maxlen=limit))
            turns.append(
                Turn(
                    question=question.strip(),
                    answer=answer.strip(),
                    rewritten_query=rewritten_query.strip(),
                )
            )

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._turns.pop(session_id, None)


session_store = SessionStore()

__all__ = ["SessionStore", "Turn", "session_store"]
