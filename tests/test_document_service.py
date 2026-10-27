"""T-5.3 文档服务测试：软删除/恢复/彻底删除/单文件索引。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.core.loader import compute_file_hash
from app.core.vectorstore import VectorStore
from app.services.documents import DocumentError, DocumentService, InvalidDocument
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


def test_reupload_same_name_replaces_old_version(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.save_upload("same.txt", "第一版内容。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=first.name)
    service.index_file(first, job.job_id)
    first_id = service.list_documents()["items"][0]["id"]

    second = service.save_upload("same.txt", "第二版内容。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=second.name)
    service.index_file(second, job.job_id)

    listed = service.list_documents(status="all")
    assert listed["total"] == 1
    assert listed["items"][0]["file_name"] == "same.txt"
    assert listed["items"][0]["id"] == compute_file_hash(second)
    assert listed["items"][0]["id"] != first_id

    store = VectorStore(service._settings, embedding_function=FakeEmbeddings())
    try:
        assert store.count() == 1
    finally:
        store.close()


def test_rebuild_reindexes_active_file_missing_from_index(tmp_path: Path) -> None:
    """文件在磁盘上、但向量已丢失时，重建必须把它补回来。

    旧实现用「重建前索引里的 active 哈希」当白名单；一次失败的重建会把索引
    清空，下一次重建就会把仍在磁盘上的文件当成“已移出”永久跳过。
    """
    service = _service(tmp_path)
    path = service.save_upload("lost.txt", "索引丢了但文件还在。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=path.name)
    service.index_file(path, job.job_id)

    store = VectorStore(service._settings, embedding_function=FakeEmbeddings())
    store.delete_by_file_hash(compute_file_hash(path))
    store.close()
    assert service.list_documents()["total"] == 0

    job = service._jobs.create("rebuild")
    service.rebuild(job.job_id)

    listed = service.list_documents()
    assert listed["total"] == 1
    assert listed["items"][0]["file_name"] == "lost.txt"


def test_rebuild_aborts_and_keeps_old_index_when_a_file_fails(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.save_upload("keep.txt", "旧索引必须保留。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=path.name)
    service.index_file(path, job.job_id)

    (service._settings.upload_dir / "bad.pdf").write_bytes(b"not a pdf")
    job = service._jobs.create("rebuild")
    with pytest.raises(DocumentError):
        service.rebuild(job.job_id)

    # 解析失败发生在写入之前，旧文档仍然可检索。
    assert service.list_documents()["total"] == 1


def test_rebuild_keeps_soft_removed_document_removed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.save_upload("removed.txt", "已移出的文档。".encode("utf-8"))
    job = service._jobs.create("upload", file_name=path.name)
    service.index_file(path, job.job_id)
    document_id = service.list_documents()["items"][0]["id"]
    service.set_active(document_id, False)

    job = service._jobs.create("rebuild")
    service.rebuild(job.job_id)

    assert service.list_documents()["total"] == 0
    assert service.list_documents(status="removed")["total"] == 1


def test_rebuild_preserves_removed_vectors_for_restore(tmp_path: Path) -> None:
    """有活跃文档参与重建时，已移出文档的向量也不能被当成垃圾清掉。"""
    service = _service(tmp_path)
    keep = service.save_upload("keep.txt", "活跃文档。".encode("utf-8"))
    removed = service.save_upload("removed.txt", "已移出文档。".encode("utf-8"))
    for path in (keep, removed):
        job = service._jobs.create("upload", file_name=path.name)
        service.index_file(path, job.job_id)

    removed_id = next(
        item["id"]
        for item in service.list_documents()["items"]
        if item["file_name"] == "removed.txt"
    )
    service.set_active(removed_id, False)

    job = service._jobs.create("rebuild")
    service.rebuild(job.job_id)

    assert service.list_documents()["total"] == 1
    assert service.list_documents(status="removed")["total"] == 1
    # 恢复必须仍然可用，说明向量被保留而不是只剩一条列表记录。
    service.set_active(removed_id, True)
    assert service.list_documents()["total"] == 2
