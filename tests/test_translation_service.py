import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import config, database
from app.repositories import drawings as drawings_repository
from app.services import translation
from app.services.segments import ensure_segments


class TranslationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patches = [
            patch.object(database, "DATABASE_PATH", root / "test.sqlite"),
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(config, "QWEN_API_KEY", "sk-test-1234567890"),
            patch.object(config, "QWEN_MT_MODEL", "qwen-mt-plus"),
            patch.object(config, "TRANSLATION_MAX_WORKERS", 2),
        ]
        for item in self.patches:
            item.start()
        database.init_database()
        self.drawing_id = drawings_repository.create({
            "filename": "test.pdf",
            "file_type": ".pdf",
            "file_size": 1,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": "图名 | 面板8\n图号 | BJ-SG2-1-09A-12",
            "tech_text": "1. 去毛刺，锐边倒钝，内角R1.0。\n2. 表面不得有划痕，长度15.4mm。",
            "all_text": "折弯图\n下料图",
            "layout": "horizontal",
        })
        ensure_segments(self.drawing_id)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_long_text_is_split_before_qwen_mt(self):
        long_text = "去毛刺。" * 900
        chunks = translation._chunk_text(long_text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= translation.MAX_TRANSLATION_CHARS for chunk in chunks))
        self.assertEqual("".join(chunks).replace(" ", "")[:20], long_text[:20])

    def test_model_change_invalidates_cached_rows(self):
        with patch.object(translation.qwen_mt, "translate_text", return_value={"text": "Panel 8", "input_tokens": 1, "output_tokens": 1}):
            translation.translate_drawing(self.drawing_id)
        with patch.object(config, "QWEN_MT_MODEL", "qwen-mt-flash"):
            payload = translation.get_translation_payload(self.drawing_id)
        self.assertGreater(payload["summary"]["missing"], 0)

    def test_source_change_refreshes_segments_and_invalidates_translation(self):
        with patch.object(translation.qwen_mt, "translate_text", return_value={"text": "Panel 8", "input_tokens": 1, "output_tokens": 1}):
            translation.translate_drawing(self.drawing_id)
        with database.connect() as connection:
            connection.execute(
                "UPDATE drawings SET title_text=? WHERE id=?",
                ("图名 | 新面板", self.drawing_id),
            )
            connection.commit()
        refreshed = ensure_segments(self.drawing_id)
        self.assertTrue(any("新面板" in segment["source_text"] for segment in refreshed))
        payload = translation.get_translation_payload(self.drawing_id)
        self.assertGreater(payload["summary"]["missing"], 0)

    def test_long_segment_is_split_into_multiple_mt_requests(self):
        drawing_id = drawings_repository.create({
            "filename": "long.pdf",
            "file_type": ".pdf",
            "file_size": 1,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": "",
            "tech_text": "1. " + ("去毛刺。" * 900),
            "all_text": "",
            "layout": "horizontal",
        })
        ensure_segments(drawing_id)
        calls = []

        def fake_translate(text, **kwargs):
            calls.append(text)
            return {"text": "Deburr.", "input_tokens": 10, "output_tokens": 3}

        with patch.object(translation.qwen_mt, "translate_text", side_effect=fake_translate):
            payload = translation.translate_drawing(drawing_id)
        self.assertGreater(len(calls), 1)
        self.assertEqual(payload["summary"]["failed"], 0)

    def test_translation_is_stored_and_reused_from_cache(self):
        calls = []

        def fake_translate(text, **kwargs):
            calls.append((text, kwargs))
            return {"text": f"EN: {text}", "input_tokens": 10, "output_tokens": 5}

        with patch.object(translation.qwen_mt, "translate_text", side_effect=fake_translate):
            first = translation.translate_drawing(self.drawing_id)
            second = translation.translate_drawing(self.drawing_id)

        self.assertGreaterEqual(len(first["segments"]), 3)
        self.assertGreater(first["summary"]["translated"], 0)
        self.assertEqual(len(calls), first["summary"]["translated"])
        self.assertGreater(second["summary"]["cached"], 0)
        self.assertEqual(len(calls), first["summary"]["translated"])
        self.assertTrue(all(segment["translated_text"] for segment in first["segments"]))
        self.assertTrue(any(any("去毛刺" in term["source"] for term in kwargs["terms"]) for _, kwargs in calls))

    def test_force_retranslates_cached_rows(self):
        calls = []

        def fake_translate(text, **kwargs):
            calls.append(text)
            return {"text": f"EN: {text}", "input_tokens": 1, "output_tokens": 1}

        with patch.object(translation.qwen_mt, "translate_text", side_effect=fake_translate):
            translation.translate_drawing(self.drawing_id)
            count = len(calls)
            translation.translate_drawing(self.drawing_id, force=True)
        self.assertGreater(len(calls), count)

    def test_code_only_segments_are_skipped_without_api_calls(self):
        drawing_id = drawings_repository.create({
            "filename": "code.pdf",
            "file_type": ".pdf",
            "file_size": 1,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": "图号 | BJ-SG2-1-09A-12\n图幅 | A3",
            "tech_text": "",
            "all_text": "",
            "layout": "horizontal",
        })
        ensure_segments(drawing_id)
        with patch.object(translation.qwen_mt, "translate_text") as translate:
            payload = translation.translate_drawing(drawing_id)
        translate.assert_not_called()
        self.assertEqual(payload["summary"]["skipped"], 2)
        self.assertEqual(payload["summary"]["translated"], 0)

    def test_missing_key_returns_actionable_error(self):
        with patch.object(config, "QWEN_API_KEY", ""):
            with self.assertRaises(HTTPException) as caught:
                translation.translate_drawing(self.drawing_id)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("处理设置", str(caught.exception.detail))

    def test_placeholder_loss_marks_row_for_review(self):
        with patch.object(translation.qwen_mt, "translate_text", return_value={"text": "EN", "input_tokens": 1, "output_tokens": 1}):
            payload = translation.translate_drawing(self.drawing_id)
        self.assertGreater(payload["summary"]["needs_review"], 0)

    def test_csv_neutralizes_formula_like_text(self):
        drawing_id = drawings_repository.create({
            "filename": "formula.pdf",
            "file_type": ".pdf",
            "file_size": 1,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": "图名 | =HYPERLINK(\"https://evil.example\")",
            "tech_text": "",
            "all_text": "",
            "layout": "horizontal",
        })
        ensure_segments(drawing_id)
        content, _ = translation.export_csv(drawing_id)
        self.assertIn("'=HYPERLINK", content)

    def test_csv_has_bom_and_bilingual_columns(self):
        with patch.object(translation.qwen_mt, "translate_text", return_value={"text": "Deburr", "input_tokens": 1, "output_tokens": 1}):
            translation.translate_drawing(self.drawing_id)
        content, filename = translation.export_csv(self.drawing_id)
        self.assertTrue(content.startswith("\ufeff"))
        self.assertIn("序号,区域,位置/字段,原文,译文,状态", content)
        self.assertIn("test.pdf", filename)
        self.assertTrue(filename.endswith(".zh-en.csv"))


if __name__ == "__main__":
    unittest.main()
