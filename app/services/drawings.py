import json
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image

from app import config
from app.repositories import drawings as repository
from app.repositories import kit_checks as kit_repository
from app.repositories import translations as translations_repository
from app.services import segments
from app.services.ocr import run_ocr
from app.services.images import RASTER_SUFFIXES, page_count


# OCR/model calls are expensive and share one local GPU/model scheduler.
# Bound admission at the application process so concurrent browser requests do
# not create an unbounded thread backlog or race the model lifecycle.
_OCR_GATE = threading.BoundedSemaphore(config.MAX_CONCURRENT_OCR)
LOGGER = logging.getLogger(__name__)


def _drawing_title(row: dict) -> str:
    """Return the title-block part name, independent of the uploaded filename."""
    return segments.extract_title(
        str(row.get("title_text") or ""),
        str(row.get("all_text") or ""),
    )


def template_rows(rows: list[dict]) -> list[tuple]:
    return [
        (
            index,
            row["filename"],
            row["file_type"],
            row["file_size"],
            row["upload_time"],
            row["id"],
            _drawing_title(row),
        )
        for index, row in enumerate(rows, 1)
    ]


def save_upload(file: UploadFile, ocr_backend: str = "qwen", batch_id: str | None = None) -> dict:
    original_name = file.filename or ""
    extension = Path(original_name).suffix.lower()
    if extension not in RASTER_SUFFIXES | {".pdf"}:
        raise HTTPException(400, f"支持 PDF、PNG、JPEG、TIFF、BMP 和 WebP，{original_name} 不是支持的格式")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > config.MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大，最大允许 50MB，{original_name} 超过限制")

    header = file.file.read(8)
    file.file.seek(0)
    if extension == ".pdf" and not header.startswith(b"%PDF"):
        raise HTTPException(400, f"{original_name} 内容不是有效的 PDF 文件")
    if extension == ".png" and header[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(400, f"{original_name} 内容不是有效的 PNG 图片")
    if extension in RASTER_SUFFIXES:
        try:
            with Image.open(file.file) as image:
                image.verify()
        except Exception as exc:
            raise HTTPException(400, f"{original_name} 内容不是有效的图片") from exc
        finally:
            file.file.seek(0)

    safe_name = "".join(char for char in original_name if char.isalnum() or char in "_-." )
    filename = f"{datetime.now():%Y%m%d%H%M%S_%f}_{safe_name}"
    path = config.UPLOAD_DIR / filename
    acquired = _OCR_GATE.acquire(blocking=False)
    if not acquired:
        raise HTTPException(429, "当前已有图纸正在进行 OCR，请稍后重试", headers={"Retry-After": "15"})
    try:
        with path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        result = run_ocr(path, ocr_backend)
        if result.get("error") and not result.get("all_text"):
            raise HTTPException(502, result["error"])
        if result.get("page_errors"):
            result["all_text"] = str(result.get("all_text") or "") + "\n\n[识别提示]\n" + "\n".join(result["page_errors"])
        values = {
            key: str(result.get(source, ""))
            for key, source in (("title_text", "title_block"), ("tech_text", "tech_block"), ("all_text", "all_text"), ("layout", "layout"))
        }
        try:
            total_pages = int(page_count(path))
        except Exception:
            total_pages = int(result.get("pages_total") or 0)
        pages_processed = int(result.get("pages_processed") or 0)
        if total_pages:
            pages_processed = min(total_pages, pages_processed)
        if not pages_processed and total_pages:
            # Older OCR backends did not expose pages_processed. A non-empty
            # single-page result is safe to treat as processed; multi-page
            # results remain unknown and will be reported as partially checked.
            pages_processed = 1 if total_pages == 1 and values["all_text"].strip() else 0
        drawing_id = repository.create({
            "filename": filename,
            "original_filename": original_name,
            "batch_id": batch_id or "",
            "file_type": extension,
            "file_size": size,
            "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_count": total_pages,
            "pages_processed": pages_processed,
            "ocr_errors": json.dumps(result.get("page_errors") or [], ensure_ascii=False),
            **values,
        })
        try:
            segments.ensure_segments(drawing_id)
        except Exception:
            LOGGER.exception("Unable to build translation segments for drawing %s", drawing_id)
        return {
            "id": drawing_id,
            "filename": filename,
            "original_filename": original_name,
            "batch_id": batch_id or "",
            "file_size": size,
            "ocr_backend": result.get("backend", ocr_backend),
            "page_errors": result.get("page_errors") or [],
        }
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        _OCR_GATE.release()


def delete(drawing_id: int) -> None:
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到该图纸")
    (config.UPLOAD_DIR / drawing["filename"]).unlink(missing_ok=True)
    translations_repository.delete_for_drawing(drawing_id)
    kit_repository.delete_extraction(drawing_id)
    repository.delete(drawing_id)


def delete_all() -> None:
    filenames = repository.delete_all()
    translations_repository.delete_all()
    kit_repository.delete_all_extractions()
    for filename in filenames:
        (config.UPLOAD_DIR / filename).unlink(missing_ok=True)
