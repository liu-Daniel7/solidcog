"""macOS MinerU service backed by mineru-llama-cpp and Metal."""
from __future__ import annotations

import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


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
        scale = int(os.getenv("MINERU_PDF_DPI", "180")) / 72
        return [page.render(scale=scale).to_pil().convert("RGB") for page in document]
    image = Image.open(io.BytesIO(data))
    image.load()
    return [image.convert("RGB")]


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
        raw_pages, markdown_pages = [], []
        for page in pages:
            result = client.two_step_extract(page)
            raw_pages.append(result)
            markdown_pages.append(_to_markdown(result))
        markdown = "\n\n---\n\n".join(markdown_pages).strip()
        name = Path(files.filename or "document").stem
        return {
            "backend": "llama-cpp-engine",
            "model": "MinerU2.5-Pro-2605-1.2B",
            "results": {name: {"md_content": markdown, "content_list": raw_pages}},
        }
    except Exception as exc:
        raise HTTPException(500, f"MinerU 解析失败: {exc}") from exc
