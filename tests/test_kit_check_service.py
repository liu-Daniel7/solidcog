import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import config, database
from app.repositories import drawings, kit_checks
from app.services import kit_checks as service
from app.services import bom_extraction


def extraction(rows=None, status="ok", pages_total=1, pages_processed=1, issues=None, code="", name=""):
    return {
        "status": status,
        "rows": rows or [],
        "pages_total": pages_total,
        "pages_processed": pages_processed,
        "issues": issues or [],
        "table_code": code,
        "table_name": name,
        "source": "vision",
    }


def row(code, name, note="", seq="", qty="1", status="ok", issue=""):
    return {
        "page_number": 1,
        "row_index": 1,
        "sequence_no": seq,
        "code": code,
        "name": name,
        "note": note,
        "quantity": qty,
        "raw_text": f"{code} {name} {note} {qty}".strip(),
        "position": {"page": 1},
        "extraction_status": status,
        "extraction_issue": issue,
    }


class KitCheckServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(config, "UPLOAD_DIR", root / "uploads"),
            patch.object(database, "DATABASE_PATH", root / "database.db"),
        ]
        for item in self.patches:
            item.start()
        from app.database import init_database
        init_database()
        self.root = root
        self.ids = {}
        self._create("SK.CNC.0001-SK数控铣床.pdf", "SK.CNC.0001", "SK数控铣床")
        self._create("SK.CNC.0002-工作台框.pdf", "SK.CNC.0002", "工作台框")
        self._create("SK.CNC.0003-外框.pdf", "SK.CNC.0003", "外框")
        self._create("SK.CNC.0004-后框.pdf", "SK.CNC.0004", "后框")
        self._create("SK.CNC.0005-内框.pdf", "SK.CNC.0005", "内框")
        self.extractions = {
            self.ids["SK.CNC.0001"]: extraction([
                row("SK.CNC.0002", "工作台框", seq="1"),
                row("SK.CNC.0003", "外框", seq="2", qty="2"),
                row("SK0001", "螺钉", "标准件", seq="3", qty="4"),
                row("SK.CNC.0005", "内框", "无图", seq="4"),
                row("SK.CNC.0099", "缺失件", "", seq="5"),
            ], code="SK.CNC.0001", name="SK数控铣床"),
            self.ids["SK.CNC.0002"]: extraction([
                row("SK.CNC.0003", "外框", seq="1", qty="2"),
                row("SK.CNC.0004", "后框", seq="2", qty="2"),
                row("SK.CNC.0001", "SK数控铣床", seq="3"),
            ], code="SK.CNC.0002", name="工作台框"),
            self.ids["SK.CNC.0003"]: extraction(status="no_table"),
            self.ids["SK.CNC.0004"]: extraction(status="no_table"),
            self.ids["SK.CNC.0005"]: extraction(status="no_table"),
        }

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def _create(self, filename, code, title):
        drawing_id = drawings.create({
            "filename": f"20261028100000_000001_{filename}",
            "original_filename": filename,
            "file_type": Path(filename).suffix,
            "file_size": 100,
            "upload_time": "2026-10-28 10:00:00",
            "title_text": f"{code} {title}",
            "tech_text": "",
            "all_text": "明细表 序号 代号 名称",
            "layout": "horizontal",
            "page_count": 1,
            "pages_processed": 1,
        })
        self.ids[code] = drawing_id
        return drawing_id

    def _run(self, ids, mode="all", roots=None, force=None):
        check = service.create_check(ids, mode=mode, root_ids=roots or [], start=False)
        with patch.object(bom_extraction, "extract_drawing", side_effect=lambda drawing, force=False: self.extractions[int(drawing["id"])]):
            service.run_check(check["id"], force_drawing_ids=force or [])
        return service.build_payload(check["id"])

    def test_all_mode_groups_and_counts(self):
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0003"], self.ids["SK.CNC.0004"], self.ids["SK.CNC.0005"]])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["summary"]["assemblies"], 2)
        self.assertEqual(payload["summary"]["exists"], 5)  # root: 0002/0003; child: 0003/0004/0001
        self.assertEqual(payload["summary"]["missing"], 1)
        self.assertEqual(payload["summary"]["exempt"], 2)
        self.assertFalse(payload["summary"]["all_present"])
        titles = {group["title"] for group in payload["groups"]}
        self.assertEqual(titles, {"SK数控铣床", "工作台框"})
        # Same part appears in both assembly groups, not collapsed.
        occurrences = [
            item["code"]
            for group in payload["groups"]
            for item in group["items"]
            if item["code"] == "SK.CNC.0003"
        ]
        self.assertEqual(occurrences.count("SK.CNC.0003"), 2)

    def test_missing_required_pdf_marks_missing(self):
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0004"], self.ids["SK.CNC.0005"]])
        root_group = next(group for group in payload["groups"] if group["code"] == "SK.CNC.0001")
        item = next(item for item in root_group["items"] if item["code"] == "SK.CNC.0003")
        self.assertEqual(item["match_status"], "missing")
        self.assertEqual(item["status_label"], "不存在")

    def test_deleting_exempt_file_does_not_add_missing(self):
        payload = self._run([self.ids["SK.CNC.0001"]])
        self.assertEqual(payload["summary"]["missing"], 3)  # 0002/0003/0099 are required but not selected
        self.assertEqual(payload["summary"]["exempt"], 2)
        exempt = payload["groups"][0]["exempt_items"]
        self.assertEqual({item["code"] for item in exempt}, {"SK0001", "SK.CNC.0005"})

    def test_manual_mode_expands_found_subassembly_and_cycle(self):
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0003"], self.ids["SK.CNC.0004"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        self.assertEqual(payload["summary"]["assemblies"], 2)
        child = next(group for group in payload["groups"] if group["code"] == "SK.CNC.0002")
        self.assertTrue(child["references"])
        # The child references the root back, but traversal stops at the visited root.
        cycle_item = next(item for item in child["items"] if item["code"] == "SK.CNC.0001")
        self.assertEqual(cycle_item["match_status"], "exists")
        self.assertIn("循环引用", cycle_item["extraction_issue"])

    def test_manual_missing_subassembly_does_not_invent_children(self):
        payload = self._run([self.ids["SK.CNC.0001"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        codes = {item["code"] for item in payload["groups"][0]["items"]}
        self.assertIn("SK.CNC.0099", codes)
        self.assertEqual(payload["summary"]["assemblies"], 1)

    def test_candidate_same_code_different_name_is_review_not_exists(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0003", "外框"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        # Replace the selected exact file with a same-code/different-name file.
        self.extractions[self.ids["SK.CNC.0003"]] = extraction(status="no_table")
        drawings.create({
            "filename": "20261028100000_000002_SK.CNC.0003-内框.pdf",
            "original_filename": "SK.CNC.0003-内框.pdf",
            "file_type": ".pdf",
            "file_size": 100,
            "upload_time": "2026-10-28 10:00:00",
            "title_text": "SK.CNC.0003 内框",
            "tech_text": "",
            "all_text": "",
            "layout": "horizontal",
        })
        # Use only root + the mismatched candidate in the selected set.
        candidate_payload = self._run([self.ids["SK.CNC.0001"], 6], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        root_group = candidate_payload["groups"][0]
        item = next(item for item in root_group["items"] if item["code"] == "SK.CNC.0003")
        self.assertEqual(item["match_status"], "candidate")
        self.assertTrue(item["review"])
        self.assertFalse(candidate_payload["summary"]["all_present"])

    def test_partial_pages_prevent_complete_claim(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0002", "工作台框"),
        ], status="partial", pages_total=2, pages_processed=1, issues=["第 2 页识别失败"], code="SK.CNC.0001", name="SK数控铣床")
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"]])
        self.assertEqual(payload["status"], "partial")
        self.assertFalse(payload["summary"]["complete"])
        self.assertFalse(payload["summary"]["all_present"])
        self.assertTrue(payload["drawing_issues"])

    def test_failed_table_with_problem_rows_keeps_rows_visible(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("", "无法辨认", "", seq="1", status="review", issue="代号缺失"),
        ], status="failed", issues=["表格文字无法辨认"], code="SK.CNC.0001", name="SK数控铣床")
        payload = self._run([self.ids["SK.CNC.0001"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        self.assertEqual(len(payload["groups"]), 1)
        item = payload["groups"][0]["items"][0]
        self.assertEqual(item["match_status"], "unchecked")
        self.assertTrue(item["review"])
        self.assertFalse(payload["summary"]["all_present"])
        self.assertFalse(payload["summary"]["complete"])

    def test_extraction_failure_keeps_other_groups(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction(status="failed", issues=["模型超时"])
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"]])
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["summary"]["assemblies"], 1)
        self.assertFalse(payload["summary"]["all_present"])

    def test_item_edit_rematches(self):
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0003"], self.ids["SK.CNC.0004"]])
        root_group = next(group for group in payload["groups"] if group["code"] == "SK.CNC.0001")
        item = next(item for item in root_group["items"] if item["code"] == "SK.CNC.0099")
        # Correct the missing code to a file that is in the selected set.
        service.update_item(item["id"], {"code": "SK.CNC.0004", "name": "后框", "note": "", "quantity": "1"})
        updated = service.build_payload(payload["id"])
        item = next(item for group in updated["groups"] for item in group["items"] if item["id"] == item["id"])
        self.assertEqual(item["match_status"], "exists")

    def test_all_present_true_when_all_required_files_exist(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0002", "工作台框"),
            row("SK.CNC.0003", "外框"),
            row("SK0001", "螺钉", "标准件"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        self.extractions[self.ids["SK.CNC.0002"]] = extraction([
            row("SK.CNC.0003", "外框"),
        ], code="SK.CNC.0002", name="工作台框")
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0003"]])
        self.assertTrue(payload["summary"]["all_present"])
        self.assertTrue(payload["summary"]["complete"])
        self.assertEqual(payload["summary"]["missing"], 0)

    def test_only_model_reference_is_missing(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0007", "立柱垫板"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        # A model file with the matching stem is present but is not a drawing.
        drawings.create({
            "filename": "20261028100000_000003_SK.CNC.0007-立柱垫板.SLDPRT",
            "original_filename": "SK.CNC.0007-立柱垫板.SLDPRT",
            "file_type": ".sldprt",
            "file_size": 100,
            "upload_time": "2026-10-28 10:00:00",
            "title_text": "",
            "tech_text": "",
            "all_text": "",
            "layout": "unknown",
        })
        payload = self._run([self.ids["SK.CNC.0001"], 6], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        item = next(item for group in payload["groups"] for item in group["items"] if item["code"] == "SK.CNC.0007")
        self.assertEqual(item["match_status"], "missing")

    def test_empty_note_on_screw_is_still_checked(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK0001", "螺钉", ""),
        ], code="SK.CNC.0001", name="SK数控铣床")
        payload = self._run([self.ids["SK.CNC.0001"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        item = payload["groups"][0]["items"][0]
        self.assertEqual(item["match_status"], "missing")
        self.assertIsNone(item["exempt_reason"])

    def test_non_standard_note_is_checked_and_reviewed(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK0001", "螺钉", "非标准件"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        payload = self._run([self.ids["SK.CNC.0001"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        item = payload["groups"][0]["items"][0]
        self.assertEqual(item["match_status"], "missing")
        self.assertIsNone(item["exempt_reason"])
        self.assertTrue(item["review"])

    def test_invalid_code_is_not_hidden_by_exempt_note(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("", "无代号标准件", "标准件"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        payload = self._run([self.ids["SK.CNC.0001"]], mode="manual", roots=[self.ids["SK.CNC.0001"]])
        item = payload["groups"][0]["items"][0]
        self.assertIsNone(item["exempt_reason"])
        self.assertEqual(item["match_status"], "unchecked")
        self.assertTrue(item["review"])
        self.assertFalse(payload["summary"]["all_present"])

    def test_manual_probes_child_even_without_ocr_header_hint(self):
        child = self.ids["SK.CNC.0002"]
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0002", "工作台框"),
            row("SK.CNC.0003", "外框"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        with closing(database.connect()) as connection:
            with connection:
                connection.execute("UPDATE drawings SET all_text='' WHERE id=?", (child,))
        payload = self._run(
            [self.ids["SK.CNC.0001"], child, self.ids["SK.CNC.0003"]],
            mode="manual", roots=[self.ids["SK.CNC.0001"]],
        )
        child_group = next(group for group in payload["groups"] if group["code"] == "SK.CNC.0002")
        self.assertTrue(child_group["references"])
        self.assertGreaterEqual(len(child_group["items"]), 1)

    def test_deleting_selected_drawing_invalidates_old_complete_result(self):
        self.extractions[self.ids["SK.CNC.0001"]] = extraction([
            row("SK.CNC.0002", "工作台框"),
            row("SK.CNC.0003", "外框"),
        ], code="SK.CNC.0001", name="SK数控铣床")
        self.extractions[self.ids["SK.CNC.0002"]] = extraction([
            row("SK.CNC.0003", "外框"),
        ], code="SK.CNC.0002", name="工作台框")
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"], self.ids["SK.CNC.0003"]])
        self.assertTrue(payload["summary"]["all_present"])
        # Remove one selected drawing after the check completed.
        drawings.delete(self.ids["SK.CNC.0002"])
        updated = service.build_payload(payload["id"])
        self.assertFalse(updated["summary"]["complete"])
        self.assertFalse(updated["summary"]["all_present"])
        self.assertGreaterEqual(updated["summary"]["missing_selected_files"], 1)
        self.assertTrue(any(issue.get("status") == "missing" for issue in updated["drawing_issues"]))

    def test_csv_has_bom_and_chinese_headers(self):
        payload = self._run([self.ids["SK.CNC.0001"], self.ids["SK.CNC.0002"]])
        text = service.export_csv(payload["id"])
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn("所属装配图", text)
        self.assertIn("SK数控铣床", text)
        self.assertIn("不存在", text)

    def test_empty_selection_rejected(self):
        with self.assertRaises(Exception):
            service.create_check([], mode="all")


if __name__ == "__main__":
    unittest.main()
