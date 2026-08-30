from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def load_pages(path: Path, dpi: int, limit: int | None = None) -> list[Image.Image]:
    if path.suffix.lower() == ".png":
        with Image.open(path) as image:
            return [image.convert("RGB")]
    if path.suffix.lower() != ".pdf":
        raise ValueError("不支持的文件类型")

    pages = []
    document = pdfium.PdfDocument(path)
    try:
        count = min(len(document), limit) if limit is not None else len(document)
        for page_number in range(count):
            page = document[page_number]
            try:
                pages.append(page.render(scale=dpi / 72).to_pil().convert("RGB"))
            finally:
                page.close()
    finally:
        document.close()
    return pages
