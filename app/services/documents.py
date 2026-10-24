"""文档管理服务（T-5.3）。

职责：文件落盘、文档列表、软删除/恢复、彻底删除、单文件索引、全量重建。
API 层只做 HTTP 映射；索引任务由 :mod:`app.services.jobs` 串行执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import Settings, get_settings
from app.core.loader import load_document
from app.core.splitter import split_document
from app.core.vectorstore import IndexedFile, VectorStore
from app.services.jobs import JobManager, job_manager

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class DocumentError(RuntimeError):
    """文档管理错误基类。"""


class DocumentNotFound(DocumentError):
    """文件/文档不存在。"""


class InvalidDocument(DocumentError):
    """不支持的文件或非法输入。"""


class DocumentService:
    """本地文档管理服务。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store_factory: Callable[[Settings], VectorStore] = VectorStore,
        jobs: JobManager | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._store_factory = store_factory
        self._jobs = jobs or job_manager

    def _open_store(self) -> VectorStore:
        return self._store_factory(self._settings)

    def save_upload(self, filename: str, data: bytes) -> Path:
        """安全保存上传文件并返回路径。"""
        safe_name = Path(filename or "").name
        if not safe_name or safe_name in {".", ".."}:
            raise InvalidDocument("文件名非法")
        if Path(safe_name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise InvalidDocument("只支持 PDF / DOCX / TXT")
        if len(data) > MAX_UPLOAD_BYTES:
            raise InvalidDocument("文件超过 20 MB")

        self._settings.ensure_directories()
        target = self._settings.upload_dir / safe_name
        target.write_bytes(data)
        return target

    def _file_to_dict(self, item: IndexedFile) -> dict:
        path = Path(item.file_path) if item.file_path else None
        size = path.stat().st_size if path and path.is_file() else 0
        return {
            "id": item.file_hash,
            "file_name": item.source_file,
            "doc_type": item.doc_type,
            "pages": item.pages,
            "chunks": item.chunks,
            "size": size,
            "status": "active" if item.active else "removed",
            "created_at": item.created_at,
            "removed_at": None,
        }

    def list_documents(
        self,
        *,
        status: str = "active",
        query: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """列出文档；status 为 active / removed / all。"""
        if status not in {"active", "removed", "all"}:
            raise InvalidDocument("status 必须是 active / removed / all")
        if page < 1 or page_size < 1 or page_size > 200:
            raise InvalidDocument("分页参数非法")

        store = self._open_store()
        try:
            files = store.list_files()
        finally:
            store.close()

        active = [item for item in files if item.active]
        removed = [item for item in files if not item.active]
        query_lower = query.strip().lower()
        if query_lower:
            active = [item for item in active if query_lower in item.source_file.lower()]
            removed = [item for item in removed if query_lower in item.source_file.lower()]

        selected = active if status == "active" else removed if status == "removed" else active + removed
        selected.sort(key=lambda item: (item.source_file.lower(), item.file_hash))
        start = (page - 1) * page_size
        page_items = selected[start:start + page_size]

        return {
            "items": [self._file_to_dict(item) for item in page_items],
            "total": len(selected),
            "page": page,
            "page_size": page_size,
            "counts": {
                "active": len(active),
                "removed": len(removed),
                "chunks": sum(item.chunks for item in active),
            },
        }

    def set_active(self, document_id: str, active: bool) -> dict:
        """软删除/恢复；document_id 当前为 file_hash。"""
        store = self._open_store()
        try:
            changed = store.set_file_active(document_id, active)
        finally:
            store.close()
        if changed == 0:
            raise DocumentNotFound(document_id)
        return {
            "id": document_id,
            "status": "active" if active else "removed",
            "removed_at": None if active else "",
            "changed_chunks": changed,
        }

    def permanent_delete(self, document_id: str) -> dict:
        """彻底删除向量 + 原文件。"""
        store = self._open_store()
        try:
            files = {item.file_hash: item for item in store.list_files()}
            item = files.get(document_id)
            if item is None:
                raise DocumentNotFound(document_id)
            deleted = store.delete_by_file_hash(document_id)
        finally:
            store.close()

        file_deleted = False
        path = Path(item.file_path) if item.file_path else None
        if path:
            try:
                resolved = path.resolve()
                upload_root = self._settings.upload_dir.resolve()
                if resolved.is_relative_to(upload_root) and resolved.is_file():
                    resolved.unlink()
                    file_deleted = True
            except OSError:
                file_deleted = False

        return {
            "id": document_id,
            "deleted_chunks": deleted,
            "file_deleted": file_deleted,
        }

    def index_file(self, path: Path, job_id: str) -> None:
        """单个文件的后台索引任务。"""
        store = self._open_store()
        try:
            self._jobs.update(
                job_id,
                state="parsing",
                percent=10,
                stage="解析中",
                file_name=path.name,
                message="正在解析文档",
            )
            document = load_document(path)
            if not document.ok:
                raise DocumentError(document.error or "解析失败")

            chunks = split_document(document, settings=self._settings)
            if not chunks:
                raise DocumentError("文档没有可入库文本")

            self._jobs.update(
                job_id,
                state="splitting",
                percent=40,
                stage="分块中",
                total=len(chunks),
                unit="chunk",
                message=f"已生成 {len(chunks)} 个 chunk",
            )
            self._jobs.update(
                job_id,
                state="embedding",
                percent=55,
                stage="嵌入中",
                document_id=document.file_hash,
                message="正在生成向量",
            )
            result = store.index_chunks(chunks)
            self._jobs.update(
                job_id,
                state="skipped" if result.skipped else "ready",
                percent=100,
                stage="已存在，跳过" if result.skipped else "已入库",
                document_id=document.file_hash,
                current=len(chunks),
                total=len(chunks),
                message="文件已存在，跳过" if result.skipped else f"已写入 {result.indexed} 个 chunk",
            )
        finally:
            store.close()

    def rebuild(self, job_id: str) -> None:
        """全量重建：保留已移出文件，不重新激活它们。"""
        store = self._open_store()
        try:
            files = store.list_files()
            active_hashes = {item.file_hash for item in files if item.active}
            store.delete_all()

            candidates = sorted(
                path for path in self._settings.upload_dir.iterdir()
                if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
            )
            total = len(candidates) or 1
            self._jobs.update(
                job_id,
                state="parsing",
                percent=5,
                stage="重建中",
                total=total,
                unit="file",
                message="已清空索引，开始重建",
            )

            for index, path in enumerate(candidates, start=1):
                document = load_document(path)
                if not document.ok:
                    continue
                if document.file_hash not in active_hashes:
                    self._jobs.update(job_id, current=index, message=f"跳过已移出：{path.name}")
                    continue
                chunks = split_document(document, settings=self._settings)
                if not chunks:
                    continue
                store.index_chunks(chunks)
                self._jobs.update(
                    job_id,
                    current=index,
                    percent=5 + int(90 * index / total),
                    stage="重建中",
                    message=f"已重建 {path.name}",
                )

            self._jobs.update(
                job_id,
                state="ready",
                percent=100,
                stage="完成",
                current=len(candidates),
                total=len(candidates),
                message="索引重建完成",
            )
        finally:
            store.close()


__all__ = [
    "ALLOWED_SUFFIXES",
    "DocumentError",
    "DocumentNotFound",
    "DocumentService",
    "InvalidDocument",
    "MAX_UPLOAD_BYTES",
]
