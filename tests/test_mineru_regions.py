import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import mineru


def table(text, bbox=None, page=0):
    block = {"type": "table", "content": text, "page_idx": page}
    if bbox is not None:
        block["bbox"] = bbox
    return block


class MineruRegionTests(unittest.TestCase):
    def test_top_left_title_table_uses_fields_and_coordinates_only_assist(self):
        title = "<table><tr><td>图号</td><td>AB-12</td></tr><tr><td>材料</td><td>Q235</td></tr></table>"
        other = "<table><tr><td>序号</td><td>数量</td></tr></table>"
        raw = {"content_list": [[
            table(title, [0.02, 0.03, 0.28, 0.16]),
            table(other, [0.55, 0.02, 0.9, 0.2]),
        ]]}
        result = mineru._title_block(title + "\n" + other, raw)
        self.assertIn("AB-12", result)
        self.assertNotIn("数量", result)

    def test_markdown_fallback_requires_multiple_title_fields(self):
        one_field = "<table><tr><td>材料</td><td>Q235</td></tr></table>"
        title = "<table><tr><td>图号</td><td>AB-12</td><td>比例</td><td>1:2</td></tr></table>"
        self.assertNotIn("Q235", mineru._title_block(one_field, {}))
        self.assertIn("AB-12", mineru._title_block(one_field + title, {}))

    def test_overlapping_title_crops_choose_complete_version(self):
        partial = "<table><tr><td>图号</td><td>AB-12</td></tr></table>"
        complete = "<table><tr><td>图号</td><td>AB-12</td><td>材料</td><td>Q235</td><td>比例</td><td>1:2</td></tr></table>"
        separate = "<table><tr><td>图号</td><td>CD-34</td><td>材料</td><td>304</td></tr></table>"
        raw = {"content_list": [[
            table(partial, [0.02, 0.02, 0.35, 0.18]),
            table(complete, [0.01, 0.01, 0.37, 0.2]),
            table(separate, [0.62, 0.02, 0.96, 0.18]),
        ]]}
        result = mineru._title_block(partial + complete + separate, raw)
        self.assertEqual(result.count("AB-12"), 1)
        self.assertIn("Q235", result)
        self.assertIn("CD-34", result)

    def test_pages_groups_flat_blocks_by_page_index_and_accepts_page_objects(self):
        flat = {"content_list": [
            {"type": "text", "page_idx": 1, "content": "B"},
            {"type": "text", "page_idx": 0, "content": "A"},
            {"type": "text", "page_idx": 1, "content": "C"},
        ]}
        self.assertEqual([[b["content"] for b in page] for page in mineru._pages(flat)], [["B", "C"], ["A"]])
        page_objects = {"content_list": [{"page_idx": 0, "blocks": [{"type": "text", "content": "A"}]}]}
        self.assertEqual(mineru._pages(page_objects)[0][0]["content"], "A")

    def test_requirements_are_aggregated_per_page_without_cross_page_dedup(self):
        heading = {"type": "text", "bbox": [0.1, 0.2, 0.3, 0.22], "content": "技术要求："}
        item = {"type": "text", "bbox": [0.11, 0.23, 0.4, 0.25], "content": "1. 表面去毛刺"}
        indented = {"type": "text", "bbox": [0.16, 0.26, 0.43, 0.28], "content": "补充说明，边缘不得有毛刺"}
        view_label = {"type": "text", "bbox": [0.72, 0.3, 0.9, 0.32], "content": "主视图"}
        raw = {"content_list": [
            [heading, item, indented, view_label],
            [dict(heading), dict(item)],
        ]}
        result = mineru._tech_from_pages(raw)
        self.assertEqual(result.count("表面去毛刺"), 2)
        self.assertIn("补充说明", result)
        self.assertNotIn("主视图", result)

    def test_layout_uses_first_rendered_page_for_raster_and_pdf(self):
        class Page:
            width = 100
            height = 200

        with patch.object(mineru.images, "load_pages", return_value=[Page()]) as load:
            self.assertEqual(mineru._layout(Path("drawing.pdf")), "vertical")
            load.assert_called_once_with(Path("drawing.pdf"), dpi=72, limit=1)


if __name__ == "__main__":
    unittest.main()
