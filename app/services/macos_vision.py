"""Native macOS Vision OCR adapter.

The kit checker needs text *and* coordinates.  Asking a VLM for every page
again would throw away the useful multi-scale scan we can get from the
platform OCR engine.  This adapter uses Apple's Vision framework through a
tiny Swift helper, keeps all coordinates normalized to the page, and falls
back cleanly when the helper is unavailable (non-macOS, CI, or tests).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from app.services.kit_rules import is_valid_code

LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "platforms" / "macos" / "vision_ocr" / "main.swift"
_CACHE_DIR = Path.home() / "Library" / "Caches" / "SolidCog"
_BINARY = _CACHE_DIR / "vision_ocr"


def available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("swiftc"))


def _ensure_binary() -> Path | None:
    if not available():
        return None
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not _BINARY.exists() or _BINARY.stat().st_mtime < SOURCE.stat().st_mtime:
            subprocess.run(
                [
                    shutil.which("swiftc") or "swiftc",
                    "-O",
                    str(SOURCE),
                    "-o",
                    str(_BINARY),
                    "-framework",
                    "Vision",
                    "-framework",
                    "AppKit",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        return _BINARY
    except Exception as exc:
        LOGGER.warning("macOS Vision OCR helper unavailable: %s", exc)
        return None


def _run(image_path: Path, orientation: int) -> list[dict]:
    binary = _ensure_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [str(binary), str(image_path), str(orientation)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(completed.stdout or "[]")
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            bbox = item.get("bbox")
            if not text or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                box = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            if box[2] <= 0 or box[3] <= 0:
                continue
            result.append({
                "text": text,
                "bbox": box,
                "confidence": float(item.get("confidence") or 0),
                "orientation": orientation,
            })
        return result
    except Exception as exc:
        LOGGER.warning("macOS Vision OCR failed: %s", exc)
        return []


def _score(blocks: list[dict]) -> float:
    if not blocks:
        return -1.0
    text = "\n".join(block["text"] for block in blocks)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    codes = sum(
        1 for block in blocks
        if any(is_valid_code(match.group(0)) for match in re.finditer(r"[A-Za-z][A-Za-z0-9._-]{2,}", block["text"]))
    )
    replacements = text.count("\ufffd") + text.count("?")
    confidence = sum(float(block.get("confidence") or 0) for block in blocks)
    return codes * 8 + chinese * 0.15 + confidence - replacements * 4


def _dedupe(blocks: list[dict]) -> list[dict]:
    result: list[dict] = []
    for block in sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        duplicate = False
        for existing in result:
            if existing["text"] != block["text"]:
                continue
            x0, y0, w0, h0 = existing["bbox"]
            x1, y1, w1, h1 = block["bbox"]
            ix = max(0.0, min(x0 + w0, x1 + w1) - max(x0, x1))
            iy = max(0.0, min(y0 + h0, y1 + h1) - max(y0, y1))
            intersection = ix * iy
            union = w0 * h0 + w1 * h1 - intersection
            if union and intersection / union >= 0.8:
                duplicate = True
                break
        if not duplicate:
            result.append(block)
    return result


def scan_image(image: Image.Image) -> list[dict]:
    """Return recognized text blocks with top-left normalized [x,y,w,h]."""
    if not available():
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = Path(handle.name)
    try:
        image.convert("RGB").save(path, format="PNG")
        blocks = _run(path, 1)
        if _score(blocks) < 8:
            # The page may be stored upside down.  Vision's orientation handling
            # is cheap; only pay for a second pass when the first pass looks weak.
            alternate = _run(path, 3)
            if _score(alternate) > _score(blocks):
                blocks = alternate
        return _dedupe(blocks)
    finally:
        path.unlink(missing_ok=True)
