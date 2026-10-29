import unittest

from app.services import coordinate_scan


def block(text, x, y, w=0.02, h=0.018, confidence=0.9):
    return {"text": text, "bbox": [x, y, w, h], "confidence": confidence}


class CoordinateScanTests(unittest.TestCase):
    def test_header_and_rows_are_reconstructed_from_geometry(self):
        blocks = [
            block("序号", 0.40, 0.50),
            block("代号", 0.48, 0.50),
            block("名称", 0.62, 0.50),
            block("数量", 0.76, 0.50),
            block("备注", 0.90, 0.50),
            block("5", 0.405, 0.44),
            block("SK.CNC.0071", 0.48, 0.44),
            block("控制器", 0.62, 0.44),
            block("1", 0.76, 0.44),
            block("4", 0.405, 0.40),
            block("SK0001", 0.48, 0.40),
            block("螺钉", 0.62, 0.40),
            block("4", 0.76, 0.40),
            block("标准件", 0.90, 0.40),
        ]
        regions = coordinate_scan._page_regions(blocks)
        rows = regions["bom"]["rows"]
        self.assertEqual([row["code"] for row in rows], ["SK0001", "SK.CNC.0071"])
        self.assertEqual(rows[0]["name"], "螺钉")
        self.assertEqual(rows[0]["quantity"], "4")
        self.assertEqual(rows[0]["note"], "标准件")
        self.assertEqual(rows[1]["name"], "控制器")
        self.assertEqual(rows[1]["quantity"], "1")

    def test_title_block_is_located_by_coordinate(self):
        blocks = [
            block("SK.CNC.0002", 0.90, 0.92, confidence=1.0),
            block("工作台框", 0.90, 0.88, confidence=0.95),
            block("图号", 0.80, 0.92, confidence=1.0),
        ]
        title = coordinate_scan._page_regions(blocks)["title"]
        self.assertEqual(title["code"], "SK.CNC.0002")
        self.assertEqual(title["name"], "工作台框")

    def test_table_without_header_is_not_accepted(self):
        blocks = [
            block("SK.CNC.0003", 0.48, 0.44),
            block("外框", 0.62, 0.44),
            block("2", 0.76, 0.44),
        ]
        self.assertEqual(coordinate_scan._page_regions(blocks)["bom"], {})

    def test_missing_quantity_marks_row_for_review(self):
        blocks = [
            block("序号", 0.40, 0.50),
            block("代号", 0.48, 0.50),
            block("名称", 0.62, 0.50),
            block("数量", 0.76, 0.50),
            block("备注", 0.90, 0.50),
            block("1", 0.405, 0.44),
            block("SK.CNC.0003", 0.48, 0.44),
            block("外框", 0.62, 0.44),
        ]
        rows = coordinate_scan._page_regions(blocks)["bom"]["rows"]
        self.assertEqual(rows[0]["issues"], ["数量缺失"])


if __name__ == "__main__":
    unittest.main()
