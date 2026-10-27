import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageStat

from app import config, database
from app.application import create_app
from app.repositories import completeness as repository
from app.repositories import drawings
from app.services import completeness
from app.services import drawings as drawing_service


class CompletenessServiceTests(unittest.TestCase):
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
        create_app()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def add_drawing(
        self,
        filename,
        title_text,
        *,
        all_text=None,
        circles=False,
        color="white",
    ):
        path = config.UPLOAD_DIR / filename
        image = Image.new("RGB", (900, 600), color)
        draw = ImageDraw.Draw(image)
        if circles:
            draw.ellipse((150, 150, 240, 240), outline="black", width=3)
            draw.ellipse((350, 220, 470, 290), outline="black", width=3)
        image.save(path)
        return drawings.create(
            {
                "filename": filename,
                "file_type": ".png",
                "file_size": path.stat().st_size,
                "upload_time": "2026-10-27 10:00:00",
                "title_text": title_text,
                "tech_text": "",
                "all_text": title_text if all_text is None else all_text,
                "layout": "horizontal",
            }
        )

    @staticmethod
    def title(number, name="零件", material="45", extra=""):
        return (
            f"图号: {number}\n图名: {name}\n材料: {material}\n比例: 1:1\n"
            f"版次: A\n日期: 2026-10-27\n{extra}"
        )

    @staticmethod
    def bom(rows):
        return "序号\t图号\t名称\t数量\t材料\t备注\n" + "\n".join(rows)

    def rules(self, *, dimensions=False, title_fields=None, bom_columns=None):
        return {
            "required_title_fields": title_fields or ["drawing_no", "drawing_name"],
            "bom_required_columns": bom_columns
            or ["sequence", "drawing_no", "name", "quantity"],
            "dimension_check": dimensions,
        }

    def test_scope_recursive_reference_duplicate_cycle_and_missing(self):
        assembly = self.add_drawing(
            "assembly.png",
            self.title("ASSY-01", "总装", extra=self.bom([
                "1\tPART-01\t支架\t2\tQ235\t",
                "2\tSUB-01\t子装\t1\t45\t",
                "3\tPART-01\t支架重复\t2\tQ235\t",
            ])),
        )
        sub = self.add_drawing(
            "sub.png",
            self.title("SUB-01", "子装", extra=self.bom([
                "1\tMISS-01\t缺失件\t1\tQ235\t",
                "2\tASSY-01\t总装\t1\t45\t",
            ])),
        )
        part = self.add_drawing("part.png", self.title("PART-01", "支架"))
        # Same number exists outside the batch: it must not satisfy the reference.
        self.add_drawing("outside.png", self.title("MISS-01", "库外图纸"))
        batch_id = completeness.create_batch(
            "递归测试",
            [assembly, sub, part],
            [assembly],
            self.rules(),
        )
        payload = completeness.run_batch(batch_id)
        self.assertEqual(payload["batch"]["status"], "completed")
        self.assertEqual(payload["summary"]["failed"], 0)

        missing = [
            item for item in payload["results"]
            if item["kind"] == "reference" and item["status"] == "missing"
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["source_filename"], "sub.png")
        self.assertEqual(missing[0]["target_filename"], "")
        self.assertEqual(missing[0]["field_value"], "MISS-01")
        self.assertEqual(missing[0]["page_number"], 1)
        self.assertEqual(missing[0]["table_index"], 1)
        self.assertEqual(missing[0]["row_index"], 2)
        self.assertEqual(missing[0]["column_index"], 2)
        self.assertIn("仅在本批次已选图纸内匹配", missing[0]["evidence"]["scope_note"])

        references = {
            item["field_value"]: item for item in payload["results"] if item["kind"] == "reference"
        }
        self.assertEqual(references["PART-01"]["status"], "passed")
        self.assertEqual(references["PART-01"]["evidence"]["duplicate_count"], 2)
        self.assertEqual(references["SUB-01"]["status"], "passed")
        self.assertTrue(references["SUB-01"]["evidence"]["cycle"])
        self.assertIn("循环引用", references["SUB-01"]["detail"])
        self.assertEqual(references["ASSY-01"]["status"], "passed")
        self.assertTrue(references["ASSY-01"]["evidence"]["cycle"])
        self.assertTrue(any(
            item["kind"] == "reference_scan"
            and item["source_filename"] == "part.png"
            and item["status"] == "pending"
            for item in payload["results"]
        ))
        # Selection scope is explicit and results remain finite despite the cycle.
        self.assertEqual(payload["batch"]["scope_note"], "仅检查本批次选入的 3 张图纸；图纸库中未选入的文件不参与检查或匹配。")

    def test_ambiguous_same_number_is_pending_not_passed(self):
        parent = self.add_drawing(
            "parent.png",
            self.title("PARENT-01", "上级", extra=self.bom([
                "1\tDUP-01\t重号件\t1\t45\t",
            ])),
        )
        self.add_drawing("dup1.png", self.title("DUP-01", "重号一"))
        self.add_drawing("dup2.png", self.title("DUP-01", "重号二"))
        batch_id = completeness.create_batch(
            "同号测试",
            [parent, *[row["id"] for row in drawings.list_all("ASC") if row["filename"] in {"dup1.png", "dup2.png"}]],
            [parent],
            self.rules(),
        )
        payload = completeness.run_batch(batch_id)
        result = next(item for item in payload["results"] if item["kind"] == "reference")
        self.assertEqual(result["status"], "pending")
        self.assertIn("多张同图号", result["detail"])
        self.assertFalse(result["target_filename"])
        self.assertEqual(len(result["evidence"]["candidate_drawings"]), 2)

    def test_reference_to_inferred_target_number_is_pending(self):
        parent = self.add_drawing(
            "infer-parent.png",
            self.title("INFER-P", "推断上级", extra=self.bom([
                "1\tINFER-01\t推断零件\t1\t45\t",
            ])),
        )
        target = self.add_drawing("infer-target.png", "INFER-01\n推断零件\n45\n1:1")
        batch_id = completeness.create_batch(
            "推断目标",
            [parent, target],
            [parent],
            self.rules(title_fields=["drawing_name"]),
        )
        payload = completeness.run_batch(batch_id)
        reference = next(item for item in payload["results"] if item["kind"] == "reference")
        self.assertEqual(reference["status"], "pending")
        self.assertIn("版式/模式推断", reference["detail"])
        self.assertEqual(len(reference["evidence"]["candidate_drawings"]), 1)

    def test_inferred_title_values_never_become_passed_claims(self):
        drawing_id = self.add_drawing("inferred.png", "LJT99.01\n推断零件\n45\n1:2")
        batch_id = completeness.create_batch(
            "推断标题栏",
            [drawing_id],
            [drawing_id],
            self.rules(title_fields=["drawing_no", "drawing_name", "material", "scale"]),
        )
        payload = completeness.run_batch(batch_id)
        title_fields = [item for item in payload["results"] if item["kind"] == "title_field"]
        self.assertTrue(title_fields)
        self.assertTrue(all(item["status"] == "pending" for item in title_fields))
        self.assertTrue(all(item["code"] in {"title_inferred", "title_unknown"} for item in title_fields))

    def test_legacy_title_segment_supplies_drawing_number_fallback(self):
        parent = self.add_drawing(
            "legacy-parent.png",
            self.title("LEGACY-PARENT", "旧上级", extra=self.bom([
                "1\tLEGACY-01\t旧零件\t1\t45\t",
            ])),
        )
        child = self.add_drawing("legacy-child.png", "旧零件图\n材料: 45\n比例: 1:1")
        from app.repositories import translations as translations_repository
        translations_repository.create_segments(child, [{
            "segment_key": "title:099",
            "section": "title",
            "ordinal": 99,
            "label": "图号",
            "source_text": "LEGACY-01",
            "source_hash": "legacy-hash",
        }])
        batch_id = completeness.create_batch(
            "旧数据图号",
            [parent, child],
            [parent],
            self.rules(title_fields=["drawing_name"]),
        )
        payload = completeness.run_batch(batch_id)
        reference = next(item for item in payload["results"] if item["kind"] == "reference")
        self.assertEqual(reference["status"], "passed")
        self.assertEqual(reference["target_filename"], "legacy-child.png")

    def test_missing_reference_is_pending_when_another_selected_number_is_unreadable(self):
        parent = self.add_drawing(
            "parent-unknown.png",
            self.title("PARENT-02", "上级", extra=self.bom([
                "1\tMISS-02\t缺失件\t1\tQ235\t",
            ])),
        )
        unnamed = self.add_drawing("unnamed.png", "零件图\n材料: 45\n比例: 1:1")
        batch_id = completeness.create_batch(
            "未识别图号",
            [parent, unnamed],
            [parent],
            self.rules(title_fields=["drawing_name"]),
        )
        payload = completeness.run_batch(batch_id)
        reference = next(item for item in payload["results"] if item["kind"] == "reference")
        self.assertEqual(reference["status"], "pending")
        self.assertIn("未识别到标题栏图号", reference["detail"])
        self.assertEqual(len(reference["evidence"]["unidentified_drawings"]), 1)

    def test_title_blank_bom_cell_missing_and_optional_columns_ignored(self):
        drawing_id = self.add_drawing(
            "checks.png",
            "图号: CHECK-01\n图名: 检查件\n材料:\n比例: 1:1\n版次: A\n日期: 2026-10-27\n" + self.bom([
                "1\t\t支架\t2\tQ235\t",
                "2\t\t\t3\tQ235\t可选备注",
            ]),
        )
        batch_id = completeness.create_batch(
            "空值测试",
            [drawing_id],
            [drawing_id],
            self.rules(
                title_fields=["drawing_no", "drawing_name", "material"],
                bom_columns=["sequence", "name", "quantity"],
            ),
        )
        payload = completeness.run_batch(batch_id)
        title_missing = [
            item for item in payload["results"]
            if item["kind"] == "title_field" and item["field_name"] == "材料"
        ]
        self.assertEqual(len(title_missing), 1)
        self.assertEqual(title_missing[0]["status"], "missing")
        self.assertIn("标签", title_missing[0]["detail"])
        bom_missing = [
            item for item in payload["results"]
            if item["kind"] == "bom_cell" and item["status"] == "missing"
        ]
        self.assertEqual(len(bom_missing), 1)
        self.assertEqual(bom_missing[0]["row_index"], 3)
        self.assertEqual(bom_missing[0]["column_index"], 3)
        self.assertEqual(bom_missing[0]["field_name"], "名称")
        self.assertFalse(any(
            item.get("field_name") == "备注" for item in payload["results"]
        ))
        self.assertFalse(any(
            item.get("kind") == "title_field" and item.get("field_name") == "重量"
            for item in payload["results"]
        ))
        reference_scan = next(
            item for item in payload["results"] if item["kind"] == "reference_scan"
        )
        self.assertEqual(reference_scan["status"], "pending")
        self.assertEqual(reference_scan["code"], "empty_drawing_numbers")
        # Drawing number cells are blank, but that column was not required for
        # this batch, so no cell finding should be emitted for it.
        self.assertFalse(any(
            item["kind"] == "bom_cell" and item["field_name"] == "图号/代号"
            for item in payload["results"]
        ))

    def test_dimension_check_is_review_only_and_never_passes_whole_drawing(self):
        drawing_id = self.add_drawing("holes.png", self.title("HOLE-01"), circles=True)
        batch_id = completeness.create_batch(
            "尺寸测试",
            [drawing_id],
            [drawing_id],
            self.rules(dimensions=True),
        )
        source_before = drawings.get(drawing_id)
        payload = completeness.run_batch(batch_id)
        dimension_items = [
            item for item in payload["results"] if item["kind"].startswith("dimension")
        ]
        self.assertTrue(dimension_items)
        self.assertTrue(all(item["status"] == "pending" for item in dimension_items))
        candidates = [
            item for item in dimension_items if item["kind"] == "dimension_candidate"
        ]
        self.assertTrue(candidates)
        self.assertTrue(all(item["bbox"] for item in candidates))
        self.assertTrue(any("需人工复核" in item["detail"] for item in candidates))
        summary_item = next(item for item in dimension_items if item["kind"] == "dimension_scan")
        self.assertIn("不能证明整张图尺寸齐全", summary_item["detail"])
        # Saved original OCR remains byte-for-byte semantically unchanged.
        source_after = drawings.get(drawing_id)
        self.assertEqual(source_before["title_text"], source_after["title_text"])
        self.assertEqual(source_before["all_text"], source_after["all_text"])
        self.assertTrue(repository.list_pages(drawing_id))

    def test_low_confidence_table_is_pending_and_skips_blank_cell_claims(self):
        drawing_id = self.add_drawing("low.png", self.title("LOW-01"))
        drawing = drawings.get(drawing_id)
        fake_table = {
            "page_number": 1,
            "table_index": 0,
            "bbox": None,
            "confidence": 0.25,
            "source": "测试低置信度",
            "header_row": 0,
            "columns": {"sequence": 0, "drawing_no": 1, "name": 2, "quantity": 3},
            "data_rows": [{"row": 1, "cells": ["1", "", "支架", ""]}],
            "row_count": 2,
            "column_count": 4,
            "plain_text": "序号 | 图号 | 名称 | 数量\n1 |  | 支架 |",
        }
        pages = [{"page_number": 1, "tables": [], "blocks": [], "text": "..."}]
        with patch.object(completeness.ocr, "bom_tables", return_value=[fake_table]), patch.object(
            completeness.ocr, "extract_reference_candidates", return_value=[]
        ):
            results, _, _ = completeness._bom_results(drawing, pages, self.rules())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "pending")
        self.assertEqual(results[0]["code"], "bom_low_confidence")
        self.assertFalse(any(item["kind"] == "bom_cell" for item in results))

    def test_empty_ocr_fails_without_becoming_a_missing_claim(self):
        drawing_id = self.add_drawing("empty.png", "")
        batch_id = completeness.create_batch(
            "空OCR", [drawing_id], [drawing_id], self.rules()
        )
        payload = completeness.run_batch(batch_id)
        self.assertGreaterEqual(payload["summary"]["failed"], 1)
        self.assertEqual(payload["summary"]["missing"], 0)
        self.assertTrue(any(
            item["kind"] == "ocr" and item["status"] == "failed"
            for item in payload["results"]
        ))
        self.assertTrue(any(
            item["kind"] == "title_scan" and item["status"] == "pending"
            for item in payload["results"]
        ))

    def test_rerun_replaces_results_rules_version_and_review_state(self):
        drawing_id = self.add_drawing("one.png", self.title("ONE-01"))
        batch_id = completeness.create_batch("重跑", [drawing_id], [drawing_id], self.rules())
        first = completeness.run_batch(batch_id)
        first_count = first["summary"]["total"]
        first_id = first["results"][0]["id"]
        self.assertEqual(
            completeness.review_result(first_id, "confirmed", "现场已确认")["confirmed_status"],
            "confirmed",
        )
        second = completeness.run_batch(batch_id)
        self.assertEqual(second["summary"]["total"], first_count)
        self.assertFalse(any(item["id"] == first_id for item in second["results"]))
        self.assertEqual(completeness.update_rules(batch_id, self.rules(dimensions=True)), 2)
        stale = repository.get_batch(batch_id)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["rules_version"], 2)

    def test_delete_drawing_invalidates_batch_and_snapshots(self):
        drawing_id = self.add_drawing("gone.png", self.title("GONE-01"))
        batch_id = completeness.create_batch("删除", [drawing_id], [drawing_id], self.rules())
        payload = completeness.run_batch(batch_id)
        self.assertTrue(payload["results"])
        drawing_service.delete(drawing_id)
        batch = repository.get_batch(batch_id)
        self.assertEqual(batch["status"], "stale")
        results = repository.list_results(batch_id)
        self.assertTrue(results)
        self.assertTrue(all(item["status"] == "pending" for item in results))
        self.assertTrue(all(item["code"] == "invalidated" for item in results))
        self.assertEqual(repository.list_pages(drawing_id), [])
        self.assertEqual(repository.get_members(batch_id), [])

    def test_csv_formula_safety_summary_and_batch_delete(self):
        drawing_id = self.add_drawing("=evil.png", self.title("SAFE-01"))
        batch_id = completeness.create_batch("=SUM(A1)", [drawing_id], [drawing_id], self.rules())
        completeness.run_batch(batch_id)
        content, filename = completeness.export_csv(batch_id)
        self.assertTrue(content.startswith("\ufeff"))
        self.assertIn("'=SUM(A1)", content)
        self.assertIn("'=evil.png", content)
        self.assertIn("汇总", content)
        self.assertIn("已通过", content)
        self.assertTrue(filename.startswith("齐套性检查-"))
        completeness.delete_batch(batch_id)
        self.assertIsNone(repository.get_batch(batch_id))
        self.assertEqual(repository.list_results(batch_id), [])
        self.assertEqual(repository.list_pages(drawing_id), [])

    def test_result_image_uses_requested_page_not_first_page(self):
        path = config.UPLOAD_DIR / "two-pages.tiff"
        Image.new("RGB", (400, 300), "red").save(
            path, save_all=True, append_images=[Image.new("RGB", (400, 300), "blue")]
        )
        drawing_id = drawings.create({
            "filename": "two-pages.tiff",
            "file_type": ".tiff",
            "file_size": path.stat().st_size,
            "upload_time": "2026-10-27 10:00:00",
            "title_text": self.title("PAGE-02"),
            "tech_text": "",
            "all_text": self.title("PAGE-02"),
            "layout": "horizontal",
        })
        batch_id = completeness.create_batch("第二页", [drawing_id], [drawing_id], self.rules())
        repository.replace_results(batch_id, [{
            "kind": "title_field",
            "status": "missing",
            "title": "第二页证据",
            "source_drawing_id": drawing_id,
            "source_filename": "two-pages.tiff",
            "page_number": 2,
            "bbox": [0.2, 0.2, 0.8, 0.8],
            "confidence": 0.9,
            "evidence": {"test": True},
            "rules_version": 1,
        }])
        result_id = repository.list_results(batch_id)[0]["id"]
        image_path = completeness.result_image(result_id)
        with Image.open(image_path) as image:
            stat = ImageStat.Stat(image.convert("RGB"))
        self.assertGreater(stat.mean[2], 180)  # blue
        self.assertLess(stat.mean[0], 80)


if __name__ == "__main__":
    unittest.main()
