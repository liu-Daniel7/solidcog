"""macOS MinerU service backed by mineru-llama-cpp and Metal."""
from __future__ import annotations

import io
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.services.drawing_ocr_images import iter_overlapping_tiles
from app.services.images import rgb_page


def _project_path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default))).expanduser()
    return value if value.is_absolute() else ROOT / value

MODEL = _project_path("MINERU_MODEL_PATH", ROOT / "models/mineru/mineru-text-q8.gguf")

MMPROJ = _project_path("MINERU_MMPROJ_PATH", ROOT / "models/mineru/mineru-vision-f16.gguf")
engine = None
client = None


def _load() -> None:
    global engine, client
    if not MODEL.is_file() or not MMPROJ.is_file():
        raise RuntimeError(f"MinerU GGUF 文件不存在: {MODEL}, {MMPROJ}")
    from mineru_llama_cpp import Engine
    from mineru_vl_utils import MinerUClient

    engine = Engine(
        MODEL, MMPROJ,
        n_ctx_seq=int(os.getenv("MINERU_CONTEXT", "8192")),
        n_gpu_layers=int(os.getenv("MINERU_GPU_LAYERS", "99")),
        n_parallel=1,
        n_threads=int(os.getenv("MINERU_THREADS", "-1")),
    )
    client = MinerUClient(backend="llama-cpp-engine", llama_cpp_engine=engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load()
    yield


app = FastAPI(title="SolidCog MinerU for macOS", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ready", "backend": "llama.cpp/Metal"}


def _images(data: bytes, suffix: str) -> list[Image.Image]:
    if suffix.lower() == ".pdf":
        document = pdfium.PdfDocument(data)
        scale = max(72, int(os.getenv("MINERU_PDF_DPI", "300"))) / 72
        images = []
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        images.append(bitmap.to_pil().convert("RGB"))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            return images
        finally:
            document.close()
    with Image.open(io.BytesIO(data)) as image:
        return [rgb_page(frame) for frame in ImageSequence.Iterator(image)]


def _extract_drawing(image: Image.Image, page_index: int) -> tuple[list[dict], list[str]]:
    """Keep global layout context while recovering small text inside drawing images."""
    base = [dict(block) for block in client.two_step_extract(image)]
    warnings = []
    tile_size = max(512, int(os.getenv("MINERU_OCR_TILE_SIZE", "2200")))
    blocks = list(base)
    # Ordinary document layout often labels an entire view as an image and does
    # not OCR its dimensions. Extract those regions explicitly as text.
    for box, tile in iter_overlapping_tiles(image, tile_size=tile_size):
        try:
            local = [dict(block) for block in client.two_step_extract(tile)] if max(image.size) > tile_size else base
            image_regions = [
                {**block, "type": "text", "content": ""}
                for block in local
                if block.get("type") in {"image", "image_block"} and block.get("bbox")
            ]
            if image_regions and hasattr(client, "extract_with_layout"):
                local = [block for block in local if block.get("type") not in {"image", "image_block"}]
                local.extend(dict(block) for block in client.extract_with_layout(tile, image_regions))
            for block in local:
                bbox = block.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                x0, y0, x1, y1 = box
                mapped = [(x0 + bbox[0] * (x1-x0))/image.width,
                          (y0 + bbox[1] * (y1-y0))/image.height,
                          (x0 + bbox[2] * (x1-x0))/image.width,
                          (y0 + bbox[3] * (y1-y0))/image.height]
                candidate = {**block, "bbox": mapped, "page_idx": page_index}
                text = str(candidate.get("content") or candidate.get("text") or "").strip()
                if not text or text.upper() in {"[NO TEXT]", "[NONE]", "[EMPTY]"}:
                    continue
                # Deduplicate only spatially matching text; the same dimension
                # at a different location must survive.
                if any(str(old.get("content") or old.get("text") or "").strip() == text
                       and _overlaps(old.get("bbox"), mapped) for old in blocks):
                    continue
                blocks.append(candidate)
        except Exception as exc:
            warnings.append(f"第 {page_index + 1} 页区域 {box} 识别失败: {exc}")
        finally:
            tile.close()
    for block in blocks:
        block["page_idx"] = page_index
    return blocks, warnings


def _overlaps(first, second) -> bool:
    if not isinstance(first, (list, tuple)) or len(first) != 4:
        return False
    intersection = max(0, min(first[2], second[2])-max(first[0], second[0])) * max(0, min(first[3], second[3])-max(first[1], second[1]))
    smaller = min((first[2]-first[0])*(first[3]-first[1]), (second[2]-second[0])*(second[3]-second[1]))
    return smaller > 0 and intersection / smaller >= 0.6


def _block_markdown(block: dict[str, Any]) -> str:
    kind = str(block.get("type") or block.get("category") or block.get("label") or "").lower()
    value = block.get("content", block.get("text", block.get("html", "")))
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    text = str(value or "").strip()
    if not text:
        return ""
    if kind in {"title", "header"}:
        return f"# {text}"
    if kind in {"section_header", "heading"}:
        return f"## {text}"
    if kind in {"list", "list_item"}:
        return "\n".join(line if line.lstrip().startswith(("-", "*")) else f"- {line}" for line in text.splitlines())
    return text


def _to_markdown(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("md_content", "markdown", "md"):
            if result.get(key):
                return str(result[key]).strip()
        for key in ("content_list", "blocks", "layout", "result"):
            if key in result:
                return _to_markdown(result[key])
        return _block_markdown(result)
    if isinstance(result, list):
        return "\n\n".join(filter(None, (_to_markdown(item) for item in result)))
    return str(result or "").strip()


@app.post("/file_parse")
async def file_parse(files: UploadFile = File(...)):
    if client is None:
        raise HTTPException(503, "MinerU 尚未加载")
    data = await files.read()
    try:
        pages = _images(data, Path(files.filename or "image.png").suffix)
        raw_pages, markdown_pages, warnings = [], [], []
        for index, page in enumerate(pages):
            try:
                result, page_warnings = _extract_drawing(page, index)
                warnings.extend(page_warnings)
            finally:
                page.close()
            raw_pages.append(result)
            markdown_pages.append(_to_markdown(result))
        markdown = "\n\n---\n\n".join(markdown_pages).strip()
        name = Path(files.filename or "document").stem
        return {
            "backend": "llama-cpp-engine",
            "model": "MinerU2.5-Pro-2605-1.2B",
            "results": {name: {"md_content": markdown, "content_list": raw_pages,
                               "pages_processed": len(raw_pages), "page_errors": warnings}},
        }
    except Exception as exc:
        raise HTTPException(500, f"MinerU 解析失败: {exc}") from exc
