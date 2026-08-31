import base64
import io
from pathlib import Path

import requests
from fastapi import HTTPException
from PIL import Image

from app import config
from app.services import model_scheduler
from app.services.images import load_pages


_session = requests.Session()
_session.trust_env = False


def _preview(path: Path) -> Image.Image:
    pages = load_pages(path, dpi=180, limit=1)
    if not pages:
        raise HTTPException(422, "无法生成图纸预览")
    image = pages[0]
    image.thumbnail((1024, 1024))
    return image.convert("RGB")


def _encoded_preview(path: Path) -> str:
    buffer = io.BytesIO()
    _preview(path).save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def health() -> dict:
    status = model_scheduler.status()
    if status.get("state") != "mechvl_ready":
        raise HTTPException(503, "MechVL 尚未就绪，可通过本地模型转换器启动")
    return status


def analyze(path: Path, question: str, ocr_context: str) -> str:
    payload = {
        "question": question,
        "ocr_context": ocr_context,
        "image_base64": _encoded_preview(path),
    }
    response = model_scheduler.analyze_with_mechvl(payload)
    return str(response.get("answer", "")).strip()
