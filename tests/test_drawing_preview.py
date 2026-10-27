import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import config, database
from app.application import create_app
from app.repositories import drawings


class DrawingTitlePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.upload_dir = root / "uploads"
        self.upload_dir.mkdir()
        self.patches = [
            patch.object(config, "UPLOAD_DIR", self.upload_dir),
            patch.object(config, "PREVIEW_DIR", root / "previews"),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _drawing(self, filename, title_text="图名 | 面板8\n图号 | LJT01.01", file_type=".png"):
        path = self.upload_dir / filename
        return drawings.create({
            "filename": filename,
            "file_type": file_type,
            "file_size": path.stat().st_size if path.exists() else 1,
            "upload_time": "2026-10-27 12:00:00",
            "title_text": title_text,
            "tech_text": "1. 去毛刺。",
            "all_text": "",
            "layout": "horizontal",
        })

    def test_list_shows_title_block_name_and_filename(self):
        image = Image.new("RGB", (240, 160), "white")
        image.save(self.upload_dir / "encoded-001.png")
        self._drawing("encoded-001.png")
        response = self.client.get("/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn("图纸名称", response.text)
        self.assertIn("文件名", response.text)
        self.assertIn("面板8", response.text)
        self.assertIn("encoded-001.png", response.text)
        self.assertIn("/drawings/1/preview", response.text)
        self.assertEqual(self.client.get("/drawings").json()["图纸信息"][0]["图纸名称"], "面板8")

    def test_image_preview_is_resized_jpeg(self):
        image = Image.new("RGB", (1600, 1000), "white")
        image.save(self.upload_dir / "large.png")
        drawing_id = self._drawing("large.png")
        response = self.client.get(f"/drawings/{drawing_id}/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        preview = Image.open(io.BytesIO(response.content))
        self.assertLessEqual(preview.width, 640)
        self.assertLessEqual(preview.height, 480)

    def test_pdf_preview_renders_first_page(self):
        image = Image.new("RGB", (600, 420), "white")
        image.save(self.upload_dir / "sample.pdf", "PDF", resolution=72)
        drawing_id = self._drawing("sample.pdf", file_type=".pdf")
        response = self.client.get(f"/drawings/{drawing_id}/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertGreater(len(response.content), 500)

    def test_preview_missing_file_returns_404(self):
        drawing_id = self._drawing("missing.png")
        self.assertEqual(self.client.get(f"/drawings/{drawing_id}/preview").status_code, 404)
