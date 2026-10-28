import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import config, database
from app.repositories import drawings
from app.services import bom_extraction
from app.services import kit_checks as service
from tests.test_kit_check_service import extraction, row


class KitCheckRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
        ]
        for item in self.patches:
            item.start()
        from app.application import create_app
        self.client = TestClient(create_app())
        self.upload = root / "uploads"
        self.upload.mkdir(parents=True, exist_ok=True)
        self.ids = []
        for filename, code, title in (
            ("SK.CNC.0001-SK数控铣床.pdf", "SK.CNC.0001", "SK数控铣床"),
            ("SK.CNC.0002-工作台框.pdf", "SK.CNC.0002", "工作台框"),
            ("SK.CNC.0003-外框.pdf", "SK.CNC.0003", "外框"),
        ):
            stored = f"20261028100000_000001_{filename}"
            (self.upload / stored).write_bytes(b"%PDF-1.4\n")
            self.ids.append(drawings.create({
                "filename": stored,
                "original_filename": filename,
                "file_type": ".pdf",
                "file_size": 10,
                "upload_time": "2026-10-28 10:00:00",
                "title_text": f"{code} {title}",
                "tech_text": "",
                "all_text": "明细表 序号 代号 名称",
                "layout": "horizontal",
                "page_count": 1,
                "pages_processed": 1,
            }))
        self.extractions = {
            self.ids[0]: extraction([
                row("SK.CNC.0002", "工作台框", seq="1"),
                row("SK.CNC.0003", "外框", seq="2"),
            ], code="SK.CNC.0001", name="SK数控铣床"),
            self.ids[1]: extraction([row("SK.CNC.0003", "外框", seq="1")], code="SK.CNC.0002", name="工作台框"),
            self.ids[2]: extraction(status="no_table"),
        }

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def create_check(self):
        with patch.object(service, "submit") as submit:
            response = self.client.post("/kit-checks", json={"drawing_ids": self.ids, "mode": "all", "root_drawing_ids": []})
        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()
        return response.json()["id"]

    def run_sync(self, check_id):
        with patch.object(bom_extraction, "extract_drawing", side_effect=lambda drawing, force=False: self.extractions[int(drawing["id"])]):
            service.run_check(check_id)

    def test_upload_preserves_original_filename_and_page_metadata(self):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), "white").save(buffer, format="PNG")
        buffer.seek(0)
        ocr_result = {
            "title_block": "SK.CNC.0003 外框",
            "tech_block": "",
            "all_text": "第 1 页\n明细表 序号 代号 名称",
            "layout": "horizontal",
            "pages_processed": 1,
            "page_errors": [],
        }
        with patch("app.services.drawings.run_ocr", return_value=ocr_result):
            response = self.client.post(
                "/upload-drawing",
                files={"files": ("SK.CNC.0003-外框 图纸.PNG", buffer, "image/png")},
                data={"ocr_backend": "qwen"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        record = self.client.get(f"/drawings/{response.json()['files'][0]['id']}/preview")
        self.assertEqual(record.status_code, 200)
        listing = self.client.get("/drawings").json()["图纸信息"]
        stored = next(item for item in listing if item["id"] == response.json()["files"][0]["id"])
        from app.repositories import drawings as repo
        row = repo.get(stored["id"])
        self.assertEqual(row["original_filename"], "SK.CNC.0003-外框 图纸.PNG")
        self.assertEqual(row["page_count"], 1)
        self.assertEqual(row["pages_processed"], 1)
        self.assertTrue(row["batch_id"])

    def test_page_defaults_to_latest_upload_batch(self):
        old = drawings.create({
            "filename": "old.pdf", "original_filename": "old.pdf", "file_type": ".pdf",
            "file_size": 1, "upload_time": "2026-10-27 10:00:00", "title_text": "old",
            "tech_text": "", "all_text": "", "layout": "horizontal", "batch_id": "old-batch",
        })
        new = drawings.create({
            "filename": "new.pdf", "original_filename": "new.pdf", "file_type": ".pdf",
            "file_size": 1, "upload_time": "2026-10-28 10:00:00", "title_text": "new",
            "tech_text": "", "all_text": "", "layout": "horizontal", "batch_id": "new-batch",
        })
        html = self.client.get("/kit-check").text
        self.assertIn('const defaultBatchId = "new-batch"', html)
        self.assertIn('"batch_id": "old-batch"', html)
        self.assertIn('"batch_id": "new-batch"', html)
        self.assertNotEqual(old, new)

    def test_page_renders_controls_and_scope(self):
        response = self.client.get("/kit-check")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for text in ("检查全部装配图", "手动指定总成图", "本次检查集合", "drawing-checklist", "start-check", "export-csv"):
            self.assertIn(text, html)
        self.assertIn("齐套性检查", self.client.get("/home").text)

    def test_create_and_fetch_result(self):
        check_id = self.create_check()
        self.run_sync(check_id)
        response = self.client.get(f"/kit-checks/{check_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["assemblies"], 2)
        self.assertEqual(payload["summary"]["exists"], 3)
        self.assertEqual(payload["summary"]["missing"], 0)
        self.assertTrue(payload["summary"]["all_present"])
        self.assertTrue(payload["scope_note"].startswith("本次仅核对当前导入集合"))

    def test_manual_requires_root(self):
        response = self.client.post("/kit-checks", json={"drawing_ids": self.ids, "mode": "manual", "root_drawing_ids": []})
        self.assertEqual(response.status_code, 400)
        self.assertIn("总成图", response.json()["detail"])

    def test_empty_selection_rejected(self):
        response = self.client.post("/kit-checks", json={"drawing_ids": [], "mode": "all"})
        self.assertEqual(response.status_code, 400)

    def test_model_file_is_not_a_checkable_drawing(self):
        model_id = drawings.create({
            "filename": "model.sldprt", "original_filename": "SK.CNC.0007-立柱垫板.SLDPRT",
            "file_type": ".sldprt", "file_size": 10, "upload_time": "2026-10-28 10:00:00",
            "title_text": "", "tech_text": "", "all_text": "", "layout": "unknown",
        })
        response = self.client.post("/kit-checks", json={"drawing_ids": [model_id], "mode": "all"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("不是可检查的工程图", response.json()["detail"])
        html = self.client.get("/kit-check").text
        self.assertNotIn("立柱垫板.SLDPRT", html)

    def test_missing_drawing_rejected(self):
        response = self.client.post("/kit-checks", json={"drawing_ids": [9999], "mode": "all"})
        self.assertEqual(response.status_code, 400)

    def test_foreign_origin_write_rejected(self):
        response = self.client.post(
            "/kit-checks",
            json={"drawing_ids": self.ids, "mode": "all"},
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_export_csv_is_utf8_bom_with_chinese(self):
        check_id = self.create_check()
        self.run_sync(check_id)
        response = self.client.get(f"/kit-checks/{check_id}/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        text = response.content.decode("utf-8-sig")
        self.assertIn("所属装配图", text)
        self.assertIn("SK数控铣床", text)
        self.assertIn("存在", text)

    def test_edit_item_rematches(self):
        check_id = self.create_check()
        with patch.object(bom_extraction, "extract_drawing", side_effect=lambda drawing, force=False: self.extractions[int(drawing["id"])]):
            service.run_check(check_id)
        payload = self.client.get(f"/kit-checks/{check_id}").json()
        item = payload["groups"][0]["items"][0]
        response = self.client.patch(f"/kit-checks/{check_id}/items/{item['id']}", json={
            "code": "SK.CNC.0003", "name": "外框", "note": "", "quantity": "2"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"][0]["items"][0]["match_status"], "exists")

    def test_edit_item_wrong_check_404(self):
        check_id = self.create_check()
        self.run_sync(check_id)
        payload = self.client.get(f"/kit-checks/{check_id}").json()
        item = payload["groups"][0]["items"][0]
        response = self.client.patch(f"/kit-checks/{check_id + 99}/items/{item['id']}", json={"code": "SK.CNC.0003", "name": "外框"})
        self.assertEqual(response.status_code, 404)

    def test_retry_rejects_running(self):
        check_id = self.create_check()
        from app.repositories import kit_checks as repo
        repo.update(check_id, status="running")
        response = self.client.post(f"/kit-checks/{check_id}/retry")
        self.assertEqual(response.status_code, 409)

    def test_recent_list(self):
        self.create_check()
        response = self.client.get("/kit-checks/recent")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["checks"]), 1)


if __name__ == "__main__":
    unittest.main()
