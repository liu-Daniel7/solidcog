from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageSequence

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def rgb_page(image: Image.Image) -> Image.Image:
    oriented = ImageOps.exif_transpose(image).convert("RGBA")
    background = Image.new("RGBA", oriented.size, "white")
    background.alpha_composite(oriented)
    return background.convert("RGB")


def page_count(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        document = pdfium.PdfDocument(path)
        try:
            return len(document)
        finally:
            document.close()
    with Image.open(path) as image:
        return getattr(image, "n_frames", 1)


def load_page(path: Path, page_number: int, dpi: int = 140) -> Image.Image:
    """Render one 1-based page/frame without loading the whole document."""
    if page_number < 1:
        raise ValueError("页码必须从 1 开始")
    if path.suffix.lower() in RASTER_SUFFIXES:
        with Image.open(path) as image:
            for index, frame in enumerate(ImageSequence.Iterator(image), 1):
                if index == page_number:
                    return rgb_page(frame)
        raise ValueError("页码超出图片页数")
    if path.suffix.lower() != ".pdf":
        raise ValueError("不支持的文件类型")

    document = pdfium.PdfDocument(path)
    try:
        if page_number > len(document):
            raise ValueError("页码超出 PDF 页数")
        page = document[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def load_pages(path: Path, dpi: int, limit: int | None = None) -> list[Image.Image]:
    if path.suffix.lower() in RASTER_SUFFIXES:
        with Image.open(path) as image:
            pages = []
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if limit is not None and index >= limit:
                    break
                pages.append(rgb_page(frame))
            return pages
    if path.suffix.lower() != ".pdf":
        raise ValueError("不支持的文件类型")

    pages = []
    document = pdfium.PdfDocument(path)
    try:
        count = min(len(document), limit) if limit is not None else len(document)
        for page_number in range(count):
            page = document[page_number]
            try:
                bitmap = page.render(scale=dpi / 72)
                try:
                    pages.append(bitmap.to_pil().convert("RGB"))
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()
    return pages
