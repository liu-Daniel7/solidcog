import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.services import ocr
from app.services.images import load_pages


class OcrDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "drawing.png"
        Image.new("RGB", (10, 10), "white").save(self.image_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_qwen_ocr_without_external_call(self):
        page = {"title_block": "T", "tech_block": "R", "all_text": "A", "layout": "horizontal", "page": 1}
        with patch.object(ocr, "ocr_page", return_value=page):
            result = ocr.run_ocr(self.image_path)
        self.assertEqual(result["title_block"], "T")
        self.assertEqual(result["tech_block"], "R")
        self.assertEqual(result["all_text"], "第 1 页\nA")
        self.assertEqual(result["backend"], "qwen_vl")
        self.assertEqual(result["model"], "qwen3-vl-plus")
        self.assertEqual(result["pages_processed"], 1)

    def test_qwen_failure_does_not_create_empty_success(self):
        with patch.object(ocr, "ocr_page", side_effect=RuntimeError("API error")):
            result = ocr.run_ocr(self.image_path)
        self.assertIn("全部页面识别失败", result["error"])

    def test_pdf_renders_without_external_poppler(self):
        pdf_path = Path(self.temp_dir.name) / "drawing.pdf"
        Image.new("RGB", (20, 10), "white").save(pdf_path, "PDF")
        pages = load_pages(pdf_path, dpi=72, limit=1)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].size, (20, 10))


if __name__ == "__main__":
    unittest.main()
