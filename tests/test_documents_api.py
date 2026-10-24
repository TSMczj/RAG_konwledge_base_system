"""T-5.3 文档 API 路由测试（不访问真实模型）。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.documents as documents_api
import app.api.index as index_api
from app.main import app
from app.services.jobs import job_manager

client = TestClient(app)


class FakeService:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def save_upload(self, filename: str, data: bytes) -> Path:
        path = self.tmp_path / filename
        path.write_bytes(data)
        return path

    def index_file(self, path: Path, job_id: str) -> None:
        job_manager.update(
            job_id,
            state="ready",
            percent=100,
            stage="已入库",
            message="ok",
        )

    def list_documents(self, *, status="active", query="", page=1, page_size=50):
        return {
            "items": [{"id": "hash-a", "file_name": "note.txt", "status": status}],
            "total": 1,
            "counts": {"active": 1, "removed": 0, "chunks": 1},
        }

    def set_active(self, document_id: str, active: bool) -> dict:
        return {"id": document_id, "status": "active" if active else "removed"}

    def permanent_delete(self, document_id: str) -> dict:
        return {"id": document_id, "deleted_chunks": 1, "file_deleted": True}

    def rebuild(self, job_id: str) -> None:
        job_manager.update(job_id, state="ready", percent=100, stage="完成")


def _patch(monkeypatch, tmp_path: Path) -> FakeService:
    service = FakeService(tmp_path)
    monkeypatch.setattr(documents_api, "_service", lambda: service)
    monkeypatch.setattr(index_api, "_service", lambda: service)
    return service


def _wait_job(job_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/v1/documents/uploads/{job_id}")
        if response.status_code == 200 and response.json()["state"] in {"ready", "failed", "skipped"}:
            return response.json()
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_upload_creates_job_and_lists(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/documents/upload",
        files=[("files", ("note.txt", b"hello", "text/plain"))],
    )
    assert response.status_code == 202
    job = response.json()["jobs"][0]
    assert job["file_name"] == "note.txt"

    status = _wait_job(job["job_id"])
    assert status["state"] == "ready"

    listing = client.get("/api/v1/documents")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["file_name"] == "note.txt"


def test_remove_restore_permanent(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)

    assert client.delete("/api/v1/documents/hash-a").json()["status"] == "removed"
    assert client.post("/api/v1/documents/hash-a/restore").json()["status"] == "active"
    assert client.delete("/api/v1/documents/hash-a/permanent").json()["deleted_chunks"] == 1


def test_upload_status_not_found(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    assert client.get("/api/v1/documents/uploads/missing").status_code == 404


def test_rebuild_endpoint(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    response = client.post("/api/v1/index/rebuild")
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/api/v1/index/jobs/{job_id}").json()
        if status["state"] == "ready":
            break
        time.sleep(0.01)
    assert status["state"] == "ready"
