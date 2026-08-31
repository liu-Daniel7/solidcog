import shutil
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app import config
from app.repositories import drawings as repository
from app.services.ocr import run_ocr


def template_rows(rows: list[dict]) -> list[tuple]:
    return [
        (index, row["filename"], row["file_type"], row["file_size"], row["upload_time"], row["id"])
        for index, row in enumerate(rows, 1)
    ]


def save_upload(file: UploadFile, ocr_backend: str = "qwen") -> dict:
    original_name = file.filename or ""
    extension = Path(original_name).suffix.lower()
    if extension not in {".pdf", ".png"}:
        raise HTTPException(400, f"只允许上传 PDF 或 PNG 格式文件，{original_name} 不是支持的格式")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > config.MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大，最大允许 50MB，{original_name} 超过限制")

    safe_name = "".join(char for char in original_name if char.isalnum() or char in "_-." )
    filename = f"{datetime.now():%Y%m%d%H%M%S_%f}_{safe_name}"
    path = config.UPLOAD_DIR / filename
    try:
        with path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        result = run_ocr(path, ocr_backend)
        if result.get("error") and not result.get("all_text"):
            raise HTTPException(502, result["error"])
        values = {
            key: str(result.get(source, ""))[:100000]
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
        }
    except Exception:
        path.unlink(missing_ok=True)
        raise


def delete(drawing_id: int) -> None:
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到该图纸")
    (config.UPLOAD_DIR / drawing["filename"]).unlink(missing_ok=True)
    repository.delete(drawing_id)


def delete_all() -> None:
    for filename in repository.delete_all():
        (config.UPLOAD_DIR / filename).unlink(missing_ok=True)
