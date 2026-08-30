from pathlib import Path

from app import config
from app.services.images import load_pages
from app.services.qwen import ocr_page


def _error(detail: str) -> dict:
    return {
        "title_block": detail,
        "tech_block": "",
        "all_text": "",
        "layout": "unknown",
        "backend": "qwen_vl",
        "error": detail,
    }


def run_ocr(file_path: str | Path) -> dict:
    path = Path(file_path)
    try:
        images = load_pages(path, dpi=400, limit=config.QWEN_OCR_MAX_PAGES)
        pages = []
        errors = []
        for page_number, image in enumerate(images, 1):
            try:
                pages.append(ocr_page(image, page_number))
            except Exception as exc:
                errors.append(f"第 {page_number} 页: {exc}")
        if not pages:
            return _error("Qwen3-VL OCR 全部页面识别失败: " + "; ".join(errors))
        return {
            "title_block": "\n\n".join(page["title_block"] for page in pages if page["title_block"]),
            "tech_block": "\n\n".join(page["tech_block"] for page in pages if page["tech_block"]),
            "all_text": "\n\n".join(
                f"第 {page['page']} 页\n{page['all_text']}" for page in pages if page["all_text"]
            ),
            "layout": pages[0]["layout"] if pages else "unknown",
            "backend": "qwen_vl",
            "model": config.QWEN_VL_MODEL,
            "pages_processed": len(pages),
            "page_errors": errors,
        }
    except Exception as exc:
        return _error(f"OCR识别失败: {exc}")
