import base64
import io
from pathlib import Path

import requests
from fastapi import HTTPException
from PIL import Image

from app import config
from app.services.images import load_pages


_session = requests.Session()
_session.trust_env = False


def _preview(path: Path) -> Image.Image:
    pages = load_pages(path, dpi=180, limit=1)
    if not pages:
        raise HTTPException(422, "无法生成图纸预览")
    image = pages[0]
    image.thumbnail((1536, 1536))
    return image.convert("RGB")


def _encoded_preview(path: Path) -> str:
    buffer = io.BytesIO()
    _preview(path).save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def health() -> dict:
    try:
        response = _session.get(f"{config.MECHVL_BASE_URL}/health", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise HTTPException(503, "MechVL 本地服务未启动，请先在 WSL2 中启动服务") from exc


def analyze(path: Path, question: str, ocr_context: str) -> str:
    payload = {
        "question": question,
        "ocr_context": ocr_context,
        "image_base64": _encoded_preview(path),
    }
    try:
        response = _session.post(
            f"{config.MECHVL_BASE_URL}/analyze",
            json=payload,
            timeout=config.MECHVL_TIMEOUT_SECONDS,
        )
        if response.status_code == 409:
            raise HTTPException(409, "MechVL 正在处理另一张图纸，请稍后重试")
        response.raise_for_status()
        return str(response.json().get("answer", "")).strip()
    except HTTPException:
        raise
    except requests.Timeout as exc:
        raise HTTPException(504, "MechVL 分析超时，请降低图纸分辨率后重试") from exc
    except requests.RequestException as exc:
        raise HTTPException(503, "无法连接 MechVL 本地服务，请检查 WSL2 服务状态") from exc
