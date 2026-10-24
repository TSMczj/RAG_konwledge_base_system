"""T-5.3 文档服务测试：软删除/恢复/彻底删除/单文件索引。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.core.vectorstore import VectorStore
from app.services.documents import DocumentService, InvalidDocument
from app.services.jobs import JobManager


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        deepseek_api_key="test-key",
        dashscope_api_key="test-key",
        chroma_dir=tmp_path / "chroma",
        upload_dir=tmp_path / "uploads",
        collection_name="documents-test",
    )


def _service(tmp_path: Path, jobs: JobManager | None = None) -> DocumentService:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    return DocumentService(
        settings=settings,
        store_factory=lambda cfg: VectorStore(cfg, embedding_function=FakeEmbeddings()),
        jobs=jobs or JobManager(),
    )


def test_save_upload_rejects_unsupported_file(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(InvalidDocument):
        service.save_upload("bad.exe", b"x")


def test_index_list_soft_delete_restore_and_permanent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.save_upload("note.txt", "这是一个测试文档。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=path.name)
    service.index_file(path, job.job_id)

    listed = service.list_documents()
    assert listed["total"] == 1
    item = listed["items"][0]
    document_id = item["id"]
    assert item["status"] == "active"
    assert item["chunks"] >= 1

    removed = service.set_active(document_id, False)
    assert removed["status"] == "removed"
    assert service.list_documents()["total"] == 0
    assert service.list_documents(status="removed")["total"] == 1

    service.set_active(document_id, True)
    assert service.list_documents()["total"] == 1

    deleted = service.permanent_delete(document_id)
    assert deleted["deleted_chunks"] >= 1
    assert deleted["file_deleted"] is True
    assert not path.exists()
    assert service.list_documents()["total"] == 0


def test_rebuild_restores_active_documents(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_upload("a.txt", "重建测试文档。".encode("utf-8"))
    for path in service._settings.upload_dir.iterdir():
        job = service._jobs.create("upload", file_name=path.name)
        service.index_file(path, job.job_id)

    job = service._jobs.create("rebuild")
    service.rebuild(job.job_id)

    assert service.list_documents()["total"] == 1
    assert job.job_id in service._jobs._jobs
