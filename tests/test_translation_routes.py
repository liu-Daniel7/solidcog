import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, database
from app.application import create_app
from app.repositories import drawings
from app.repositories import translations as translation_rows


class TranslationRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patches = [
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(database, "DATABASE_PATH", root / "test.sqlite"),
            patch.object(config, "QWEN_API_KEY", "sk-test-1234567890"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app())
        self.drawing_id = drawings.create({
            "filename": "测试图纸.pdf",
            "file_type": ".pdf",
            "file_size": 1,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": "图名 | 面板8\n图号 | LJT01.01",
            "tech_text": "1. 去毛刺，锐边倒钝。",
            "all_text": "折弯图",
            "layout": "horizontal",
        })

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_translation_page_renders_without_exposing_key(self):
        response = self.client.get(f"/translate/{self.drawing_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="translation-content"', response.text)
        self.assertIn('id="translate-missing"', response.text)
        self.assertNotIn("sk-test-1234567890", response.text)

    def test_get_creates_segments_without_calling_model(self):
        with patch("app.services.qwen_mt.translate_text") as translate:
            response = self.client.get(f"/drawings/{self.drawing_id}/translations?target_lang=en")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["target_lang"], "en")
        self.assertGreaterEqual(response.json()["summary"]["total"], 3)
        self.assertEqual(response.json()["summary"]["missing"], response.json()["summary"]["total"])
        translate.assert_not_called()

    def test_post_is_local_origin_protected(self):
        payload = {"target_lang": "en", "force": False}
        with patch("app.routers.translations.translation.translate_drawing", return_value={"ok": True}) as translate:
            allowed = self.client.post(
                f"/drawings/{self.drawing_id}/translations",
                json=payload,
                headers={"origin": "http://127.0.0.1:8000"},
            )
            blocked = self.client.post(
                f"/drawings/{self.drawing_id}/translations",
                json=payload,
                headers={"origin": "https://evil.example"},
            )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(blocked.status_code, 403)
        translate.assert_called_once()

    def test_delete_cleans_segments_and_translations(self):
        self.client.get(f"/drawings/{self.drawing_id}/translations?target_lang=en")
        self.assertGreater(len(translation_rows.list_segments(self.drawing_id)), 0)
        response = self.client.delete(
            f"/drawings/{self.drawing_id}",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(translation_rows.list_segments(self.drawing_id), [])

    def test_csv_route_returns_bom_and_download_name(self):
        with patch(
            "app.routers.translations.translation.export_csv",
            return_value=("\ufeff序号,区域,位置/字段,原文,译文,状态\n1,标题栏,图名,面板8,Panel 8,已翻译\n", "测试图纸.pdf.zh-en.csv"),
        ):
            response = self.client.get(f"/drawings/{self.drawing_id}/translations.csv?target_lang=en")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertTrue(response.text.startswith("\ufeff"))
        self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
