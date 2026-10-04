import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image

from app import config
from app.repositories import drawings as repository
from app.services.ocr import run_ocr
from app.services.images import RASTER_SUFFIXES


# OCR/model calls are expensive and share one local GPU/model scheduler.
# Bound admission at the application process so concurrent browser requests do
# not create an unbounded thread backlog or race the model lifecycle.
_OCR_GATE = threading.BoundedSemaphore(config.MAX_CONCURRENT_OCR)


def template_rows(rows: list[dict]) -> list[tuple]:
    return [
        (index, row["filename"], row["file_type"], row["file_size"], row["upload_time"], row["id"])
        for index, row in enumerate(rows, 1)
    ]


def save_upload(file: UploadFile, ocr_backend: str = "qwen") -> dict:
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
        drawing_id = repository.create({
            "filename": filename,
            "file_type": extension,
            "file_size": size,
            "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **values,
        })
        return {
            "id": drawing_id,
            "filename": filename,
            "original_filename": original_name,
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
    repository.delete(drawing_id)


def delete_all() -> None:
    for filename in repository.delete_all():
        (config.UPLOAD_DIR / filename).unlink(missing_ok=True)
