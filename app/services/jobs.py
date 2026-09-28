"""Small, single-process job queue for long-running local OCR tasks.

The queue intentionally has one worker because the local model scheduler/GPU is
exclusive. Jobs are kept in memory for the current desktop process; the durable
OCR results remain in SQLite.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app import config
from app.services.drawings import save_upload


@dataclass
class Job:
    id: str
    total: int
    status: str = "queued"
    completed: int = 0
    current_file: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round((self.completed / self.total) * 100) if self.total else 100,
            "completed": self.completed,
            "total": self.total,
            "current_file": self.current_file,
            "results": self.results,
            "errors": self.errors,
        }


_jobs: dict[str, Job] = {}
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solidcog-ocr")


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex


def create(files: list[UploadFile], backend: str) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "请选择至少一个文件")
    if len(files) > config.MAX_BATCH_UPLOADS:
        raise HTTPException(413, f"单次最多上传 {config.MAX_BATCH_UPLOADS} 个文件")
    if backend not in {"qwen", "mineru"}:
        raise HTTPException(400, "OCR 后端必须是 qwen 或 mineru")

    staging = Path(tempfile.mkdtemp(prefix="solidcog-job-", dir=config.UPLOAD_DIR))
    staged: list[tuple[Path, str]] = []
    try:
        for index, upload in enumerate(files):
            name = upload.filename or f"drawing-{index + 1}"
            target = staging / f"{index:04d}{Path(name).suffix.lower()}"
            with target.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            staged.append((target, name))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    job = Job(_new_id(), len(staged))
    with _lock:
        _jobs[job.id] = job
    _executor.submit(_run, job.id, staged, backend, staging)
    return job.snapshot()


def get(job_id: str) -> dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在或已过期")
        return job.snapshot()


def _run(job_id: str, staged: list[tuple[Path, str]], backend: str, staging: Path) -> None:
    with _lock:
        job = _jobs[job_id]
        job.status = "processing"
    try:
        for path, original_name in staged:
            with _lock:
                job.current_file = original_name
            try:
                with path.open("rb") as handle:
                    upload = UploadFile(file=handle, filename=original_name)
                    result = save_upload(upload, backend)
                with _lock:
                    job.results.append(result)
            except Exception as exc:
                with _lock:
                    job.errors.append({"filename": original_name, "error": str(exc)})
            finally:
                with _lock:
                    job.completed += 1
        with _lock:
            job.status = "completed" if not job.errors else ("partial" if job.results else "failed")
            job.current_file = ""
    finally:
        shutil.rmtree(staging, ignore_errors=True)
