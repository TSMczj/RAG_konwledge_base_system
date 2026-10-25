"""聊天 SSE 接口（T-5.4 / FR-08、FR-09）。

事件顺序：`sources → delta* → done`；异常时发送 `error`。
生成后如果没有有效 `[n]`，`done.answer` 会替换为固定兜底话术。
"""

from __future__ import annotations

import json
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import ConfigError, get_settings
from app.core.prompt import build_prompt
from app.core.qa import NO_ANSWER, QAService, parse_citations
from app.core.rewriter import QueryRewriter
from app.logging_config import logger
from app.services.sessions import session_store

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None


def _service() -> QAService:
    try:
        return QAService(settings=get_settings())
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_MISSING", "message": str(exc)},
        ) from exc


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _history_messages(history) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in history:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})
    return messages


def _events(service: QAService, request: ChatRequest):
    started = perf_counter()
    session_id = request.session_id or f"sess_{uuid4().hex[:16]}"
    retrieval_ms = 0.0
    ttft_ms = 0.0
    usage: dict | None = None
    rewritten_query = request.question

    try:
        history = session_store.history(session_id)
        max_turns = getattr(service.settings, "max_history_turns", 5)
        rewritten_query = QueryRewriter(
            service.llm,
            max_history_turns=max_turns,
        ).rewrite(request.question, history)

        retrieval = service.retriever.retrieve(rewritten_query)
        retrieval_ms = retrieval.elapsed_ms
        prompt = build_prompt(
            rewritten_query,
            retrieval.items,
            max_context_tokens=service.settings.max_context_tokens,
            history=_history_messages(history),
        )
        sources = [item.to_source(n) for n, item in enumerate(prompt.used_sources, start=1)]
        yield _sse("sources", {"sources": sources})

        if not prompt.used_sources:
            session_store.append(
                session_id,
                question=request.question,
                answer=NO_ANSWER,
                rewritten_query=rewritten_query,
                max_turns=max_turns,
            )
            yield _sse("delta", {"text": NO_ANSWER})
            yield _sse(
                "done",
                {
                    "session_id": session_id,
                    "answer": NO_ANSWER,
                    "rewritten_query": rewritten_query,
                    "usage": None,
                    "fallback": True,
                    "timings": {
                        "retrieval_ms": retrieval_ms,
                        "ttft_ms": 0.0,
                        "total_ms": (perf_counter() - started) * 1000,
                    },
                },
            )
            return

        parts: list[str] = []
        first_token = True
        for delta in service.llm.stream_chat(
            prompt.messages,
            temperature=service.settings.temperature,
        ):
            if delta.usage:
                usage = delta.usage
            if not delta.text:
                continue
            if first_token:
                ttft_ms = (perf_counter() - started) * 1000
                first_token = False
            parts.append(delta.text)
            yield _sse("delta", {"text": delta.text})

        answer = "".join(parts).strip()
        citations = parse_citations(answer, prompt.used_sources)
        fallback = not bool(citations)
        if fallback:
            answer = NO_ANSWER
        session_store.append(
            session_id,
            question=request.question,
            answer=answer,
            rewritten_query=rewritten_query,
            max_turns=max_turns,
        )
        yield _sse(
            "done",
            {
                "session_id": session_id,
                "answer": answer,
                "rewritten_query": rewritten_query,
                "usage": usage,
                "fallback": fallback,
                "timings": {
                    "retrieval_ms": retrieval_ms,
                    "ttft_ms": ttft_ms,
                    "total_ms": (perf_counter() - started) * 1000,
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 —— 流式错误必须变成 error 事件
        logger.exception("聊天 SSE 失败: {}: {}", type(exc).__name__, exc)
        yield _sse(
            "error",
            {
                "code": "INTERNAL",
                "message": f"{type(exc).__name__}: {exc}",
                "retryable": False,
                "request_id": session_id,
            },
        )
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()


@router.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """流式问答。"""
    service = _service()
    return StreamingResponse(
        _events(service, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
