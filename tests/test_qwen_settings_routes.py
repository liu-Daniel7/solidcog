import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, database
from app.application import create_app


class QwenSettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.env_path = root / ".env"
        self.env_path.write_text("LLAMA_SERVER_PATH=/tmp/llama-server\n", encoding="utf-8")
        self.patches = [
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(config, "ENV_PATH", self.env_path),
            patch.object(config, "QWEN_API_KEY", ""),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
            patch.dict(os.environ, {}, clear=False),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_status_save_and_clear_without_leaking_key(self):
        status = self.client.get("/settings/qwen")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["configured"])

        response = self.client.post(
            "/settings/qwen",
            json={"api_key": "sk-test-1234567890"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["configured"])
        self.assertEqual(response.json()["masked_key"], "••••••••••••")
        self.assertNotIn("sk-test-1234567890", response.text)
        self.assertEqual(config.QWEN_API_KEY, "sk-test-1234567890")

        page = self.client.get("/home")
        self.assertNotIn("sk-test-1234567890", page.text)
        self.assertIn('id="qwen-key-form"', page.text)
        self.assertIn('type="password"', page.text)

        cleared = self.client.delete(
            "/settings/qwen",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.json()["configured"])
        self.assertNotIn("QWEN_API_KEY", self.env_path.read_text(encoding="utf-8"))

    def test_test_endpoint_requires_saved_key(self):
        response = self.client.post(
            "/settings/qwen/test",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 400)

    def test_cross_origin_key_write_is_rejected(self):
        response = self.client.post(
            "/settings/qwen",
            json={"api_key": "sk-test-1234567890"},
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(config.QWEN_API_KEY, "")

    def test_shared_key_tests_both_models(self):
        self.client.post(
            "/settings/qwen",
            json={"api_key": "sk-test-1234567890"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        vl = {"ok": True, "model": "qwen3-vl-plus", "message": "连接正常"}
        mt = {"ok": True, "model": "qwen-mt-plus", "message": "连接正常"}
        with patch("app.services.qwen.test_connection", return_value=vl) as vl_test, \
             patch("app.services.qwen_mt.test_connection", return_value=mt) as mt_test:
            response = self.client.post(
                "/settings/qwen/test",
                headers={"origin": "http://127.0.0.1:8000"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(response.json()["qwen_vl"]["ok"])
        self.assertTrue(response.json()["qwen_mt"]["ok"])
        vl_test.assert_called_once()
        mt_test.assert_called_once()


if __name__ == "__main__":
    unittest.main()
