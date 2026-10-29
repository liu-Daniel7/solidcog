"""Cross-platform RapidOCR adapter for the coordinate scanner.

RapidOCR uses ONNX Runtime and is available on both macOS and Windows with the
same model family.  Keeping this adapter as the primary scanner means the
coordinate/BOM pipeline behaves the same on both platforms; macOS Vision is
only a fallback for installations where RapidOCR is not available.
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image

LOGGER = logging.getLogger(__name__)

_engine: Any = None


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def _box_bbox(box) -> list[float] | None:
    try:
        points = [(float(point[0]), float(point[1])) for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def scan_image(image: Image.Image) -> list[dict]:
    """Return cross-platform OCR blocks with normalized top-left bboxes."""
    if not available():
        return []
    try:
        import numpy as np
    except Exception as exc:
        LOGGER.warning("RapidOCR numpy unavailable: %s", exc)
        return []
    try:
        engine = _get_engine()
        array = np.asarray(image.convert("RGB"))
        result, _ = engine(array)
    except Exception as exc:
        LOGGER.warning("RapidOCR failed: %s", exc)
        return []
    blocks: list[dict] = []
    width, height = image.size
    if not width or not height:
        return []
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box, text = item[0], item[1]
        confidence = float(item[2]) if len(item) > 2 else 0.0
        text = str(text or "").strip()
        bbox = _box_bbox(box)
        if not text or not bbox or bbox[2] <= 0 or bbox[3] <= 0:
            continue
        blocks.append({
            "text": text,
            "bbox": [
                round(bbox[0] / width, 8),
                round(bbox[1] / height, 8),
                round(bbox[2] / width, 8),
                round(bbox[3] / height, 8),
            ],
            "confidence": confidence,
            "orientation": 1,
        })
    return blocks
