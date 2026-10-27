import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import completeness_ocr as ocr


class CompletenessOcrTests(unittest.TestCase):
    def test_drawing_number_patterns_and_standard_exclusions(self):
        self.assertEqual(ocr.normalize_drawing_number(" ｂｊ－sg2－1－09a－12 "), "BJ-SG2-1-09A-12")
        self.assertEqual(ocr.extract_drawing_numbers("图号（LJT03.16）"), ["LJT03.16"])
        self.assertEqual(ocr.extract_drawing_numbers("PART-01 / SUB-01"), ["PART-01", "SUB-01"])
        self.assertEqual(ocr.extract_drawing_numbers("GB/T 75—1985"), [])
        self.assertEqual(ocr.extract_drawing_numbers("M10×12"), [])
        self.assertEqual(ocr.extract_drawing_numbers("HT200"), [])
        self.assertEqual(ocr.extract_drawing_numbers("2026-10-27"), [])
        self.assertEqual(ocr.extract_drawing_numbers("2026-10-27", allow_numeric=True), [])
        self.assertEqual(ocr.extract_drawing_numbers("A-01"), ["A-01"])
        self.assertEqual(ocr.extract_drawing_numbers("1001"), [])
        self.assertEqual(ocr.extract_drawing_numbers("1001", allow_numeric=True), ["1001"])

    def test_explicit_title_fields_and_blank_value(self):
        parsed = ocr.parse_title_fields(
            "图号: T-100\n图名: 支架\n材料:\n比例: 1:2\n版次: B\n日期: 2026-10-27"
        )
        self.assertEqual(parsed["fields"]["drawing_no"]["value"], "T-100")
        self.assertEqual(parsed["fields"]["drawing_name"]["value"], "支架")
        self.assertNotIn("material", parsed["fields"])
        self.assertTrue(ocr.field_label_present("材料:\n比例: 1:2", "material"))
        self.assertEqual(parsed["quality"], "high")

    def test_title_table_cell_relationships_repair_merged_layout(self):
        html = """
        <table>
          <tr><td>A</td><td colspan="2">CFC</td><td>初版</td><td></td></tr>
          <tr><td>版次REV.</td><td>状态STATUS</td><td colspan="2">说明DESCRIPTION</td><td>日期DATE</td></tr>
          <tr><td>安徽某公司</td><td colspan="2">图名DRAWING NAME</td><td>面板8</td><td></td></tr>
          <tr><td>项目PROJECT</td><td colspan="2">中广核</td><td>图号DRAWING NO.</td><td>BJ-SG2-1-09A-12</td></tr>
          <tr><td>质量/kgMASS</td><td>0.18</td><td>材料MATERIAL</td><td>06Cr19Ni10</td><td>比例SCALE</td></tr>
          <tr><td>1:5</td><td></td><td></td><td></td><td></td></tr>
        </table>
        """
        table = ocr.parse_html_table(html)
        pages = [{"page_number": 2, "tables": [table], "blocks": [], "text": ""}]
        parsed = ocr.parse_title_fields_from_pages(pages)
        self.assertEqual(parsed["page_number"], 2)
        self.assertEqual(parsed["fields"]["revision"]["value"], "A")
        self.assertEqual(parsed["fields"]["drawing_no"]["value"], "BJ-SG2-1-09A-12")
        self.assertEqual(parsed["fields"]["drawing_name"]["value"], "面板8")
        self.assertEqual(parsed["fields"]["material"]["value"], "06Cr19Ni10")
        # 比例 value is on the following row in this deliberately awkward layout;
        # it must not be fabricated.
        self.assertNotIn("scale", parsed["fields"])

    def test_text_bom_preserves_empty_cells_columns_and_row_numbers(self):
        text = """总装图
序号\t代号\t名称\t数量\t材料\t备注
1\tPART-01\t支架\t2\tQ235\t
2\t\t螺栓\t4\tQ235\t可选备注
3\tGB/T 75—1985\t螺钉M10×12\t8\tQ235\t
标记\t处数\t分区\t更改文件号\t签名\t年月日"""
        tables = ocr._tables_from_text(text, source="测试 OCR 文本", confidence=0.74)
        self.assertEqual(len(tables), 1)
        pages = [{"page_number": 3, "tables": tables, "blocks": [], "text": text}]
        detected = ocr.bom_tables(pages)
        self.assertEqual(len(detected), 1)
        table = detected[0]
        self.assertEqual(table["columns"]["drawing_no"], 1)
        self.assertEqual(len(table["data_rows"]), 3)
        self.assertEqual(table["data_rows"][1]["cells"][1], "")
        refs = ocr.extract_reference_candidates(pages)
        self.assertEqual([ref["normalized_value"] for ref in refs], ["PART-01"])
        self.assertEqual(refs[0]["row_index"], 1)

    def test_repeated_bom_crops_with_reversed_rows_are_deduplicated(self):
        full = ocr._tables_from_text(
            "序号\t代号\t名称\t数量\t材料\n1\tPART-01\t支架\t2\tQ235\n"
            "2\tSUB-01\t子装\t1\t45",
            source="整页",
            confidence=0.74,
        )[0]
        partial = ocr._tables_from_text(
            "序号\t代号\t名称\t数量\n2\tSUB-01\t子装\t1\n"
            "1\tPART-01\t支架\t2",
            source="局部",
            confidence=0.74,
        )[0]
        deduped = ocr._dedupe_tables([full, partial])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["row_count"], 3)

    def test_low_confidence_table_requires_review(self):
        table = {"confidence": 0.42, "source": "x"}
        requires, reason = ocr.table_requires_review(table)
        self.assertTrue(requires)
        self.assertIn("置信度", reason)

    def test_qwen_multipage_title_table_is_not_duplicated_onto_page_one(self):
        title = (
            "图号: MULTI-01\n图名: 多页\n序号\t图号\t名称\t数量\n"
            "1\tCHILD-01\t子件\t2"
        )
        all_text = (
            "第 1 页\n[整页]\n总图 MULTI-01\n\n"
            "第 2 页\n[整页]\n序号\t图号\t名称\t数量\n"
            "1\tCHILD-01\t子件\t2"
        )
        pages, warnings = ocr.extract_saved_evidence(
            {"id": 1, "filename": "multi.png", "title_text": title, "all_text": all_text}
        )
        self.assertEqual([page["page_number"] for page in pages], [1, 2])
        self.assertEqual(pages[0]["tables"], [])
        self.assertEqual(len(pages[1]["tables"]), 1)
        self.assertFalse(warnings)
        refs = ocr.extract_reference_candidates(pages)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["page_number"], 2)

    def test_saved_mineru_artifact_preserves_page_table_and_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = {
                "backend": "mineru",
                "model": "test-model",
                "content_list": [[
                    {
                        "type": "table",
                        "bbox": [0.1, 0.2, 0.8, 0.9],
                        "content": (
                            "<table><tr><th>序号</th><th>代号</th><th>名称</th><th>数量</th></tr>"
                            "<tr><td>1</td><td>PART-01</td><td>支架</td><td>2</td></tr></table>"
                        ),
                    },
                    {"type": "text", "bbox": [0.1, 0.1, 0.3, 0.15], "content": "总装图"},
                ]],
                "page_errors": [],
            }
            (root / "drawing.json").write_text(json.dumps(artifact), encoding="utf-8")
            drawing = {
                "id": 9,
                "filename": "drawing.pdf",
                "title_text": "图号: PART-01",
                "all_text": "总装图",
            }
            with patch.object(config, "MINERU_RESULT_DIR", root):
                pages, warnings = ocr.extract_saved_evidence(drawing)
        self.assertFalse(warnings)
        self.assertEqual(pages[0]["page_number"], 1)
        text_block = next(block for block in pages[0]["blocks"] if block["text"] == "总装图")
        self.assertEqual(text_block["bbox"], [0.1, 0.1, 0.3, 0.15])
        table = pages[0]["tables"][0]
        self.assertEqual(table["bbox"], [0.1, 0.2, 0.8, 0.9])
        cells = {(cell["row"], cell["col"]): cell["text"] for cell in table["cells"]}
        self.assertEqual(cells[(0, 1)], "代号")
        self.assertEqual(cells[(1, 1)], "PART-01")


if __name__ == "__main__":
    unittest.main()
