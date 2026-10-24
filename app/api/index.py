"""索引重建与任务状态接口（T-5.3）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import ConfigError, get_settings
from app.services.documents import DocumentService
from app.services.jobs import job_manager

router = APIRouter(prefix="/index", tags=["index"])


def _service() -> DocumentService:
    try:
        return DocumentService(settings=get_settings())
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_MISSING", "message": str(exc)},
        ) from exc


@router.post("/rebuild", status_code=202)
def rebuild_index() -> dict:
    """创建全量重建任务；已移出文档不会被重新激活。"""
    service = _service()
    job = job_manager.create("rebuild", total=0)
    job_manager.submit(job.job_id, service.rebuild)
    return {"job_id": job.job_id, "state": "queued", "scope": "active"}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    """查询重建任务状态。"""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "任务不存在"},
        )
    return job
