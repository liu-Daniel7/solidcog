from pathlib import Path

import pypdfium2 as pdfium
from fastapi import HTTPException
from PIL import Image, ImageOps

from app import config
from app.repositories import drawings as drawings_repository


PREVIEW_MAX_WIDTH = 640
PREVIEW_MAX_HEIGHT = 480
PREVIEW_QUALITY = 82


def _render_image(source: Path) -> Image.Image:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, mask=image.getchannel("A"))
    canvas.thumbnail((PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT), Image.Resampling.LANCZOS)
    return canvas


def _render_pdf(source: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(source))
    try:
        if len(document) == 0:
            raise ValueError("PDF has no pages")
        page = document[0]
        width, _ = page.get_size()
        scale = PREVIEW_MAX_WIDTH / max(1, width)
        image = page.render(scale=scale).to_pil().convert("RGB")
        page.close()
    finally:
        document.close()
    image.thumbnail((PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT), Image.Resampling.LANCZOS)
    return image


def render_preview(source: Path, target: Path) -> Path:
    if not source.exists():
        raise HTTPException(404, "图纸文件不存在")
    try:
        if source.suffix.lower() == ".pdf":
            image = _render_pdf(source)
        else:
            image = _render_image(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=PREVIEW_QUALITY, optimize=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(404, "无法生成图纸预览") from exc
    return target


def get_preview(drawing_id: int) -> Path:
    drawing = drawings_repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    source = config.UPLOAD_DIR / str(drawing["filename"])
    if not source.exists():
        raise HTTPException(404, "图纸文件不存在")
    # Include mtime in the cache key so a replaced local file is not shown stale.
    try:
        stamp = source.stat().st_mtime_ns
    except OSError:
        stamp = 0
    target = config.PREVIEW_DIR / f"{drawing_id}-{stamp}.jpg"
    if target.exists() and target.stat().st_size:
        return target
    return render_preview(source, target)
