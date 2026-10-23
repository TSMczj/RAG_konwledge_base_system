"""Qwen 嵌入客户端测试（T-3.1）。

全部使用假客户端，不产生真实 API 费用；覆盖分批、顺序、重试退避、
维度校验与查询向量参数。
"""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from app.config import reset_settings_cache
from app.core.embedder import (
    EMBEDDING_MAX_ATTEMPTS,
    EmbeddingError,
    QwenEmbedder,
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    reset_settings_cache()
    yield
    reset_settings_cache()


class FakeEmbeddings:
    """记录调用参数并按输入顺序返回固定维度向量的假实现。"""

    def __init__(self, dim: int = 1024, reverse: bool = False) -> None:
        self.dim = dim
        self.reverse = reverse
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        batch = list(kwargs["input"])
        self.calls.append(batch)
        self.kwargs.append(kwargs)

        start = sum(len(call) for call in self.calls[:-1])
        data = [
            SimpleNamespace(
                index=index,
                embedding=[float(start + index)] * self.dim,
            )
            for index in range(len(batch))
        ]
        if self.reverse:
            data.reverse()
        return SimpleNamespace(data=data)


class FakeClient:
    def __init__(self, embeddings: FakeEmbeddings) -> None:
        self.embeddings = embeddings


class FlakyEmbeddings(FakeEmbeddings):
    """前 ``failures`` 次抛可重试异常，之后恢复正常。"""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    def create(self, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise openai.APIConnectionError(request=object())
        return super().create(**kwargs)


class BrokenEmbeddings(FakeEmbeddings):
    """直接抛不可重试异常。"""

    def create(self, **kwargs):
        raise ValueError("bad input")


def _embedder(embeddings: FakeEmbeddings) -> QwenEmbedder:
    return QwenEmbedder(client=FakeClient(embeddings))


def test_batches_are_limited_to_ten_and_order_is_preserved() -> None:
    fake = FakeEmbeddings()
    texts = [f"文本{i}" for i in range(25)]

    vectors = _embedder(fake).embed_texts(texts)

    assert [len(batch) for batch in fake.calls] == [10, 10, 5]
    assert len(vectors) == 25
    assert all(len(vector) == 1024 for vector in vectors)
    # 假实现把全局序号写进第一个分量，验证跨批次顺序没有乱
    assert [vector[0] for vector in vectors] == [float(i) for i in range(25)]


def test_reversed_response_is_sorted_by_index() -> None:
    fake = FakeEmbeddings(reverse=True)
    vectors = _embedder(fake).embed_texts([f"t{i}" for i in range(5)])

    assert [vector[0] for vector in vectors] == [float(i) for i in range(5)]


def test_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FlakyEmbeddings(failures=2)
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.embedder.time.sleep", sleeps.append)

    vectors = _embedder(fake).embed_texts(["x"])

    assert len(vectors) == 1
    assert fake.attempts == 3
    assert sleeps == [1.0, 2.0]


def test_retry_exhausted_raises_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FlakyEmbeddings(failures=EMBEDDING_MAX_ATTEMPTS)
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.embedder.time.sleep", sleeps.append)

    with pytest.raises(EmbeddingError, match="嵌入失败"):
        _embedder(fake).embed_texts(["x"])

    assert fake.attempts == EMBEDDING_MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0]


def test_non_retryable_error_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.embedder.time.sleep", sleeps.append)

    with pytest.raises(EmbeddingError, match="ValueError"):
        _embedder(BrokenEmbeddings()).embed_texts(["x"])

    assert sleeps == []


def test_dimension_mismatch_raises() -> None:
    fake = FakeEmbeddings(dim=512)
    with pytest.raises(EmbeddingError, match="维度不符"):
        _embedder(fake).embed_texts(["x"])


def test_empty_input_returns_empty_list() -> None:
    assert _embedder(FakeEmbeddings()).embed_texts([]) == []


def test_empty_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="为空"):
        _embedder(FakeEmbeddings()).embed_texts(["ok", "   "])


def test_query_passes_text_type() -> None:
    fake = FakeEmbeddings()
    vector = _embedder(fake).embed_query("问题")

    assert len(vector) == 1024
    assert fake.kwargs[0]["extra_body"] == {"text_type": "query"}


def test_batch_size_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "7")
    reset_settings_cache()

    fake = FakeEmbeddings()
    _embedder(fake).embed_texts([f"t{i}" for i in range(25)])

    assert [len(batch) for batch in fake.calls] == [7, 7, 7, 4]
