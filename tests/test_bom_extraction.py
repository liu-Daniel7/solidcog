import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import config, database
from app.repositories import drawings
from app.services import bom_extraction


def model_row(code="SK.CNC.0003", name="外框", seq="1", note="", qty="2", raw=None):
    return {
        "seq": seq,
        "code": code,
        "name": name,
        "note": note,
        "qty": qty,
        "raw_text": raw or f"{seq} {code} {name} {note} {qty}".strip(),
    }


def model_table(rows, has_table=True, issues=None, code="SK.CNC.0002", name="工作台框"):
    return {
        "has_table": has_table,
        "table_kind": "assembly_bom" if has_table else "none",
        "title_block_code": code,
        "title_block_name": name,
        "rows": rows,
        "issues": issues or [],
    }


class BomExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.upload = root / "uploads"
        self.upload.mkdir()
        self.patches = [
            patch.object(config, "UPLOAD_DIR", self.upload),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
        ]
        for item in self.patches:
            item.start()
        from app.database import init_database
        init_database()
        self.path = self.upload / "assembly.pdf"
        Image.new("RGB", (300, 220), "white").save(self.path, "PDF")
        self.drawing = {
            "id": 1,
            "filename": self.path.name,
            "original_filename": "SK.CNC.0002-工作台框.pdf",
            "file_type": ".pdf",
            "page_count": 1,
            "pages_processed": 1,
            "title_text": "SK.CNC.0002 工作台框",
            "all_text": "明细表 序号 代号 名称",
        }

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_model_rows_are_structured_and_title_block_is_removed(self):
        table = model_table([
            model_row("SK.CNC.0003", "外框", seq="1"),
            model_row("SK.CNC.0002", "工作台框", seq="2"),  # current drawing itself
            model_row("", "", seq="3", note="", qty=""),  # empty row ignored
        ])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([row["code"] for row in result["rows"]], ["SK.CNC.0003"])
        self.assertEqual(result["table_code"], "SK.CNC.0002")
        self.assertEqual(result["table_name"], "工作台框")

    def test_repeated_header_and_continuation_page_duplicates_are_merged(self):
        self.path.unlink()
        Image.new("RGB", (300, 220), "white").save(
            self.path, "PDF", save_all=True, append_images=[Image.new("RGB", (300, 220), "white")]
        )
        page1 = model_table([model_row("SK.CNC.0003", "外框", seq="1")])
        page2 = model_table([model_row("SK.CNC.0003", "外框", seq="1")])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", side_effect=[page1, page2]):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["pages"], [1, 2])

    def test_multi_page_repeated_identical_rows_do_not_double_count(self):
        self.path.unlink()
        Image.new("RGB", (300, 220), "white").save(
            self.path, "PDF", save_all=True, append_images=[Image.new("RGB", (300, 220), "white")]
        )
        page = model_table([model_row("SK.CNC.0003", "外框", seq="1")])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=page):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["pages_total"], 2)
        self.assertEqual(len(result["rows"]), 1)

    def test_conflicting_same_sequence_is_review(self):
        table = model_table([
            model_row("SK.CNC.0003", "外框", seq="1"),
            model_row("SK.CNC.0004", "后框", seq="1"),
        ])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(all(row["extraction_status"] == "review" for row in result["rows"]))
        self.assertTrue(all("冲突" in row["extraction_issue"] for row in result["rows"]))

    def test_part_table_is_not_treated_as_assembly_bom(self):
        table = model_table([model_row("M20", "螺纹", seq="1")])
        table["table_kind"] = "part_table"
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "no_table")
        self.assertEqual(result["rows"], [])

    def test_no_table_is_distinct_from_failed(self):
        empty = model_table([], has_table=False, code="SK.CNC.0002", name="工作台框")
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=empty):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "no_table")
        self.assertEqual(result["rows"], [])

    def test_table_flagged_but_no_rows_is_failed(self):
        empty = model_table([], has_table=True)
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=empty):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "failed")

    def test_page_limit_is_reported_as_partial(self):
        self.path.unlink()
        Image.new("RGB", (300, 220), "white").save(
            self.path, "PDF", save_all=True,
            append_images=[Image.new("RGB", (300, 220), "white"), Image.new("RGB", (300, 220), "white")],
        )
        table = model_table([model_row("SK.CNC.0003", "外框", seq="1")])
        with patch.object(config, "QWEN_OCR_MAX_PAGES", 1), \
             patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["pages_total"], 3)
        self.assertEqual(result["pages_processed"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("未处理页面" in issue or "上限" in issue for issue in result["issues"]))

    def test_page_render_failure_is_reported_without_unpacking_error(self):
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction, "load_pages", side_effect=RuntimeError("render failed")):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("render failed" in issue for issue in result["issues"]))

    def test_api_failure_is_failed_and_metadata_survives(self):
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", side_effect=RuntimeError("模型超时")):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["pages_total"], 1)
        self.assertEqual(result["pages_processed"], 0)
        self.assertTrue(any("模型超时" in issue for issue in result["issues"]))

    def test_image_only_pdf_without_model_is_failed_not_no_table(self):
        self.drawing["all_text"] = ""
        with patch.object(bom_extraction, "qwen_configured", return_value=False):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["pages_processed"], 0)
        self.assertTrue(any("未配置 Qwen 视觉模型" in issue for issue in result["issues"]))

    def test_ocr_text_fallback_keeps_rows_but_marks_review(self):
        self.drawing["all_text"] = "第 1 页\n明细表 序号 代号 名称 备注 数量\n1 SK.CNC.0003 外框 2 1\n"
        with patch.object(bom_extraction, "qwen_configured", return_value=False):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["source"], "ocr_text")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["rows"][0]["code"], "SK.CNC.0003")
        self.assertEqual(result["rows"][0]["extraction_status"], "review")

    def test_cache_reuses_identical_extraction(self):
        table = model_table([model_row("SK.CNC.0003", "外框")])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table) as extract:
            first = bom_extraction.extract_drawing(self.drawing)
            second = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(extract.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["rows"], second["rows"])

    def test_cache_invalidates_when_ocr_text_changes(self):
        table = model_table([model_row("SK.CNC.0003", "外框")])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table) as extract:
            bom_extraction.extract_drawing(self.drawing)
            self.drawing["all_text"] = "新的OCR文本"
            bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(extract.call_count, 2)

    def test_title_code_removes_filename_extension_and_separator(self):
        self.drawing["title_text"] = ""
        self.assertEqual(bom_extraction._derive_title_code(self.drawing), "SK.CNC.0002")
        self.drawing["original_filename"] = "SK.CNC.0003-外框.pdf"
        self.assertEqual(bom_extraction._derive_title_code(self.drawing), "SK.CNC.0003")

    def test_title_name_strips_own_code(self):
        self.assertEqual(bom_extraction.drawing_title_name(self.drawing), "工作台框")

    def test_title_code_from_filename_is_not_a_child(self):
        filtered = bom_extraction._filter_header_and_title([
            model_row("SK.CNC.0002", "工作台框", seq=""),
            model_row("SK.CNC.0003", "外框", seq="1"),
        ], "SK.CNC.0002")
        self.assertEqual([row["code"] for row in filtered], ["SK.CNC.0003"])

    def test_garbage_code_rows_are_kept_as_failed_not_missing(self):
        table = model_table([model_row("$$", "$$", seq="")])
        with patch.object(bom_extraction, "qwen_configured", return_value=True), \
             patch.object(bom_extraction.qwen, "extract_bom_page", return_value=table):
            result = bom_extraction.extract_drawing(self.drawing)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["rows"][0]["extraction_issue"])


if __name__ == "__main__":
    unittest.main()
