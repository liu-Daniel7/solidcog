import unittest
from unittest.mock import patch

from PIL import Image

from app.services import rapidocr_scan


class RapidOcrScanTests(unittest.TestCase):
    def test_box_is_normalized_to_page_bbox(self):
        image = Image.new("RGB", (100, 50), "white")
        engine = lambda _array: (
            [[([10, 5], [40, 5], [40, 20], [10, 20]), "SK.CNC.0003", 0.98]],
            None,
        )
        with patch.object(rapidocr_scan, "available", return_value=True), \
             patch.object(rapidocr_scan, "_get_engine", return_value=engine):
            blocks = rapidocr_scan.scan_image(image)
        self.assertEqual(blocks[0]["text"], "SK.CNC.0003")
        self.assertEqual([round(value, 4) for value in blocks[0]["bbox"]], [0.1, 0.1, 0.3, 0.3])
        self.assertAlmostEqual(blocks[0]["confidence"], 0.98)


if __name__ == "__main__":
    unittest.main()
