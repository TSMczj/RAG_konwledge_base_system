"""本地后台任务管理（T-5.3）。

单进程、单 worker：上传/重建任务串行执行，避免并发写 Chroma。
状态保存在内存，进程重启后丢弃；服务器阶段可替换为 Redis/worker。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable
from uuid import uuid4

from app.logging_config import logger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """一个索引任务。"""

    job_id: str
    kind: str
    state: str = "queued"
    percent: int = 0
    stage: str = "排队中"
    current: int = 0
    total: int = 0
    unit: str = "file"
    message: str = ""
    error: str | None = None
    document_id: str | None = None
    file_name: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    """线程安全的内存任务表。"""

    def __init__(self, max_workers: int = 1) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="rag-job",
        )

    def create(self, kind: str, *, file_name: str = "", total: int = 0) -> Job:
        job = Job(
            job_id=f"job_{uuid4().hex[:16]}",
            kind=kind,
            file_name=file_name,
            total=total,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = _now()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def submit(self, job_id: str, fn: Callable[[str], None]) -> None:
        self._executor.submit(self._run, job_id, fn)

    def _run(self, job_id: str, fn: Callable[[str], None]) -> None:
        """执行任务并把结果落到 job 表。

        任务失败以前只写 job.error，不写日志；重建失败时前端只看到一句
        “重建索引失败”，服务端没有任何堆栈，无法定位。这里补上开始/失败日志。
        """
        job = self.get(job_id) or {}
        logger.info(
            "任务开始: {} kind={} file={}",
            job_id, job.get("kind", ""), job.get("file_name", ""),
        )
        try:
            self.update(job_id, state="running", stage="准备中", percent=1)
            fn(job_id)
        except Exception as exc:  # noqa: BLE001 —— 任务失败要落到 job
            logger.opt(exception=exc).error(
                "任务失败: {} kind={} file={} error={}",
                job_id, job.get("kind", ""), job.get("file_name", ""),
                type(exc).__name__,
            )
            self.update(
                job_id,
                state="failed",
                stage="失败",
                percent=0,
                error=f"{type(exc).__name__}: {exc}",
                message="任务失败",
            )
        else:
            finished = self.get(job_id) or {}
            logger.info(
                "任务结束: {} kind={} state={} message={}",
                job_id, finished.get("kind", ""), finished.get("state", ""),
                finished.get("message", ""),
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


job_manager = JobManager()
