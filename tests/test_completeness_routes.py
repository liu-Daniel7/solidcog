import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import config, database
from app.application import create_app
from app.repositories import completeness as repository
from app.repositories import drawings


class CompletenessRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.patches = [
            patch.object(config, "UPLOAD_DIR", self.root / "uploads"),
            patch.object(database, "DATABASE_PATH", self.root / "database.db"),
            patch.object(config, "PREVIEW_DIR", self.root / "previews"),
            patch.object(config, "MINERU_RESULT_DIR", self.root / "mineru"),
        ]
        for item in self.patches:
            item.start()
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (self.root / "mineru").mkdir()
        self.client = TestClient(create_app())
        self.drawing_id = self.create_drawing()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def create_drawing(self, filename="route.png", title=None):
        path = config.UPLOAD_DIR / filename
        image = Image.new("RGB", (700, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((100, 100, 190, 190), outline="black", width=3)
        image.save(path)
        title = title or (
            "图号: ROUTE-01\n图名: 路由测试\n材料: 45\n比例: 1:1\n"
            "版次: A\n日期: 2026-10-27\n"
            "序号\t图号\t名称\t数量\t材料\n1\tCHILD-01\t子件\t1\tQ235"
        )
        return drawings.create({
            "filename": filename,
            "file_type": ".png",
            "file_size": path.stat().st_size,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": title,
            "tech_text": "",
            "all_text": title,
            "layout": "horizontal",
        })

    @staticmethod
    def rules(dimensions=False):
        return {
            "required_title_fields": ["drawing_no", "drawing_name"],
            "bom_required_columns": ["sequence", "name", "quantity"],
            "dimension_check": dimensions,
        }

    def create_api_batch(self, name="接口批次"):
        response = self.client.post(
            "/api/completeness/batches",
            json={
                "name": name,
                "drawing_ids": [self.drawing_id],
                "start_drawing_ids": [self.drawing_id],
                "rules": self.rules(dimensions=True),
            },
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_entry_page_and_no_default_scope(self):
        response = self.client.get("/completeness")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="batch-form"', html)
        self.assertIn("不会默认把整个图纸库算作本次提交", html)
        self.assertIn("只检查本次选入图纸", html.replace("\n", ""))
        home = self.client.get("/home").text
        self.assertIn('href="/completeness"', home)
        self.assertIn("齐套性检查", home)
        self.assertIn("mobile-check-link", home)

    def test_form_create_requires_local_origin_and_renders_batch(self):
        payload = {
            "name": "<script>alert(1)</script>",
            "drawing_ids": [str(self.drawing_id)],
            "start_drawing_ids": [str(self.drawing_id)],
            "required_title_fields": ["drawing_no", "drawing_name"],
            "bom_required_columns": ["sequence", "name", "quantity"],
        }
        blocked = self.client.post(
            "/completeness/batches", data=payload, headers={"origin": "https://evil.example"}
        )
        self.assertEqual(blocked.status_code, 403)
        allowed = self.client.post(
            "/completeness/batches",
            data=payload,
            headers={"origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )
        self.assertEqual(allowed.status_code, 303)
        detail = self.client.get(allowed.headers["location"])
        self.assertEqual(detail.status_code, 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", detail.text)
        self.assertNotIn("<script>alert(1)</script>", detail.text)
        self.assertIn("检查范围已限定", detail.text)
        self.assertIn("开始检查", detail.text)

    def test_api_start_and_summary(self):
        batch_id = self.create_api_batch()
        response = self.client.post(
            f"/api/completeness/batches/{batch_id}/start",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["batch"]["status"], "completed")
        self.assertGreater(payload["summary"]["total"], 0)
        self.assertGreaterEqual(payload["summary"]["pending"], 1)  # dimension review
        listed = self.client.get("/api/completeness/batches").json()
        self.assertTrue(any(item["id"] == batch_id for item in listed["batches"]))
        self.assertEqual(
            self.client.get(f"/api/completeness/batches/{batch_id}").status_code, 200
        )

    def test_start_rejects_duplicate_inflight_request(self):
        batch_id = self.create_api_batch()
        repository.update_batch_fields(batch_id, status="running")
        response = self.client.post(
            f"/api/completeness/batches/{batch_id}/start",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 409)

    def test_form_start_and_csv_report(self):
        created = self.client.post(
            "/completeness/batches",
            data={
                "name": "=SUM(A1)",
                "drawing_ids": [str(self.drawing_id)],
                "required_title_fields": ["drawing_no", "drawing_name"],
                "bom_required_columns": ["sequence", "name", "quantity"],
            },
            headers={"origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )
        batch_id = int(created.headers["location"].rsplit("/", 1)[-1])
        started = self.client.post(
            f"/completeness/batches/{batch_id}/start",
            headers={"origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )
        self.assertEqual(started.status_code, 303)
        report = self.client.get(f"/completeness/batches/{batch_id}/report.csv")
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.text.startswith("\ufeff"))
        self.assertIn("text/csv", report.headers["content-type"])
        self.assertIn("filename*=UTF-8''", report.headers["content-disposition"])
        self.assertIn("'=SUM(A1)", report.text)
        blocked = self.client.get(
            f"/completeness/batches/{batch_id}/report.csv",
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(blocked.status_code, 403)

    def test_result_detail_review_and_image(self):
        batch_id = self.create_api_batch()
        payload = self.client.post(
            f"/api/completeness/batches/{batch_id}/start",
            headers={"origin": "http://127.0.0.1:8000"},
        ).json()
        dimension = next(
            item for item in payload["results"] if item["kind"] == "dimension_candidate"
        )
        result_url = f"/api/completeness/results/{dimension['id']}"
        detail = self.client.get(result_url)
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertTrue(body["image_url"].endswith("/image"))
        self.assertEqual(body["confirmed_status"], "unconfirmed")
        self.assertIn("evidence", body)
        image = self.client.get(body["image_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertGreater(len(image.content), 100)
        blocked_image = self.client.get(
            body["image_url"], headers={"origin": "https://evil.example"}
        )
        self.assertEqual(blocked_image.status_code, 403)
        reviewed = self.client.post(
            f"/api/completeness/results/{dimension['id']}/review",
            json={"confirmed_status": "confirmed", "note": "尺寸已人工核对"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["confirmed_status"], "confirmed")
        self.assertEqual(reviewed.json()["confirmed_note"], "尺寸已人工核对")
        blocked_review = self.client.post(
            f"/api/completeness/results/{dimension['id']}/review",
            json={"confirmed_status": "dismissed"},
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(blocked_review.status_code, 403)

    def test_rules_can_be_updated_and_version_increments(self):
        batch_id = self.create_api_batch(name="规则版本")
        response = self.client.post(
            f"/api/completeness/batches/{batch_id}/rules",
            json={
                "required_title_fields": ["drawing_no"],
                "bom_required_columns": ["name"],
                "dimension_check": False,
            },
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rules_version"], 2)
        self.assertEqual(repository.get_batch(batch_id)["rules_version"], 2)

    def test_deleting_drawing_during_run_is_rejected_then_invalidates(self):
        batch_id = self.create_api_batch(name="删除保护")
        started = self.client.post(
            f"/api/completeness/batches/{batch_id}/start",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(started.status_code, 200)
        repository.update_batch_fields(batch_id, status="running")
        protected = self.client.delete(
            f"/drawings/{self.drawing_id}",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(protected.status_code, 409)
        repository.update_batch_fields(batch_id, status="completed")
        deleted = self.client.delete(
            f"/drawings/{self.drawing_id}",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(repository.get_batch(batch_id)["status"], "stale")
        result_id = repository.list_results(batch_id)[0]["id"]
        unavailable = self.client.get(f"/api/completeness/results/{result_id}")
        self.assertEqual(unavailable.status_code, 200)
        image = self.client.get(f"/completeness/results/{result_id}/image")
        self.assertEqual(image.status_code, 404)

    def test_missing_batch_and_unsafe_rule_payloads(self):
        self.assertEqual(self.client.get("/completeness/batches/9999").status_code, 404)
        self.assertEqual(self.client.get("/api/completeness/batches/9999").status_code, 404)
        invalid = self.client.post(
            "/api/completeness/batches",
            json={
                "name": "bad",
                "drawing_ids": [self.drawing_id],
                "rules": {"required_title_fields": ["not_real"], "dimension_check": False},
            },
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(invalid.status_code, 400)
        empty = self.client.post(
            "/api/completeness/batches",
            json={"name": "empty", "drawing_ids": []},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(empty.status_code, 422)


if __name__ == "__main__":
    unittest.main()
