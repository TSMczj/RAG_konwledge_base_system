"""文档管理 API（T-5.3）。

- 列表 / 上传 / 软删除 / 恢复 / 彻底删除
- 上传返回 job_id，后台串行索引，轮询 `/documents/uploads/{job_id}`
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.config import ConfigError, get_settings
from app.services.documents import (
    DocumentError,
    DocumentNotFound,
    DocumentService,
    InvalidDocument,
)
from app.services.jobs import job_manager

router = APIRouter(prefix="/documents", tags=["documents"])


def _service() -> DocumentService:
    try:
        return DocumentService(settings=get_settings())
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_MISSING", "message": str(exc)},
        ) from exc


@router.get("")
def list_documents(
    status: str = Query("active", pattern="^(active|removed|all)$"),
    query: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    """列出文档。"""
    try:
        return _service().list_documents(
            status=status, query=query, page=page, page_size=page_size
        )
    except InvalidDocument as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc


@router.post("/upload", status_code=202)
async def upload_documents(files: list[UploadFile] = File(...)) -> dict:
    """上传文件并创建后台索引任务。"""
    service = _service()
    saved: list[tuple[str, object]] = []
    try:
        for upload in files:
            data = await upload.read()
            path = service.save_upload(upload.filename or "", data)
            saved.append((upload.filename or path.name, path))
    except InvalidDocument as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc

    jobs = []
    for name, path in saved:
        job = job_manager.create("upload", file_name=name)
        job_manager.submit(job.job_id, lambda job_id, p=path: service.index_file(p, job_id))
        jobs.append({"job_id": job.job_id, "file_name": name, "state": job.state})
    return {"jobs": jobs}


@router.get("/uploads/{job_id}")
def upload_status(job_id: str) -> dict:
    """查询上传/索引任务状态。"""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "任务不存在"},
        )
    return job


@router.delete("/{document_id}")
def remove_document(document_id: str) -> dict:
    """移出知识库（软删除，保留文件与向量）。"""
    try:
        return _service().set_active(document_id, False)
    except DocumentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc


@router.post("/{document_id}/restore")
def restore_document(document_id: str) -> dict:
    """恢复已移出文档。"""
    try:
        return _service().set_active(document_id, True)
    except DocumentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc


@router.delete("/{document_id}/permanent")
def permanent_delete(document_id: str) -> dict:
    """彻底删除向量与原文件。"""
    try:
        return _service().permanent_delete(document_id)
    except DocumentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc
    except DocumentError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
