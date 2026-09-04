import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, database
from app.application import create_app
from app.repositories import drawings
from app.services import model_scheduler


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patches = [
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def create_record(filename="sample.pdf"):
        return drawings.create({
            "filename": filename, "file_type": ".pdf", "file_size": 10,
            "upload_time": "2026-08-29 12:00:00", "title_text": "标题",
            "tech_text": "技术要求", "all_text": "全文", "layout": "horizontal",
        })

    def test_core_routes(self):
        record_id = self.create_record()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/home").status_code, 200)
        self.assertEqual(self.client.get("/search?keyword=技术").status_code, 200)
        self.assertEqual(self.client.get("/drawings").json()["图纸数量"], 1)
        self.assertEqual(self.client.get(f"/ocr/{record_id}").json()["标题栏"], "标题")
        self.assertEqual(self.client.get(f"/view-ocr/{record_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/export-ocr/{record_id}").status_code, 200)
        self.assertEqual(self.client.get("/status").status_code, 404)
        self.assertEqual(self.client.get("/drawings-list").status_code, 404)
        self.assertEqual(self.client.post("/chat", json={"prompt": "test"}).status_code, 404)
        self.assertEqual(self.client.post("/analyze-drawing", json={}).status_code, 404)

    def test_repository_delete(self):
        record_id = self.create_record("x.pdf")
        self.assertEqual(drawings.get(record_id)["filename"], "x.pdf")
        self.assertTrue(drawings.delete(record_id))
        self.assertIsNone(drawings.get(record_id))

    def test_cross_origin_write_rejected(self):
        self.create_record("a.pdf")
        response = self.client.post(
            "/delete-all-drawings",
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/drawings").json()["图纸数量"], 1)

    def test_local_origin_write_allowed(self):
        self.create_record("a.pdf")
        response = self.client.post(
            "/delete-all-drawings",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/drawings").json()["图纸数量"], 0)

    def test_delete_endpoints_no_longer_get(self):
        self.assertEqual(self.client.get("/delete-all-drawings").status_code, 405)
        self.assertEqual(self.client.get("/delete-drawing/1").status_code, 405)

    def test_local_model_routes(self):
        ready = {"state": "mineru_ready", "current_mode": "mineru"}
        with patch.object(model_scheduler, "status", return_value=ready):
            self.assertEqual(self.client.get("/local-model/status").json(), ready)
        with patch.object(model_scheduler, "switch", return_value=ready) as switch:
            response = self.client.post("/local-model/switch/mineru")
        self.assertEqual(response.status_code, 200)
        switch.assert_called_once_with("mineru")


if __name__ == "__main__":
    unittest.main()
