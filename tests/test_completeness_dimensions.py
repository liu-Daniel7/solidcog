import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.services import dimensions


def _circle_page(path: Path, pages: int = 1) -> None:
    images = []
    for index in range(pages):
        image = Image.new("RGB", (900, 600), "white")
        draw = ImageDraw.Draw(image)
        if index == pages - 1:
            draw.ellipse((180, 150, 280, 250), outline="black", width=3)
            draw.ellipse((380, 220, 500, 290), outline="black", width=3)
        images.append(image)
    first, rest = images[0], images[1:]
    first.save(path, save_all=True, append_images=rest)


class DimensionReviewTests(unittest.TestCase):
    def test_closed_holes_generate_reviewable_geometry_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holes.png"
            _circle_page(path)
            result = dimensions.inspect_drawing(path, [])
        self.assertFalse(result["failed"])
        self.assertGreaterEqual(len(result["candidates"]), 2)
        bboxes = [item["bbox"] for item in result["candidates"]]
        self.assertTrue(any(0.18 <= box[0] <= 0.32 and 0.20 <= box[1] <= 0.32 for box in bboxes))
        self.assertTrue(all(item["detail"].endswith("需人工复核") for item in result["candidates"]))
        self.assertTrue(all(item["coordinate_association"] is False for item in result["candidates"]))

    def test_nearby_dimension_text_is_context_not_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holes.png"
            _circle_page(path)
            pages = [{
                "page_number": 1,
                "blocks": [
                    {"type": "text", "text": "φ20", "bbox": [0.32, 0.27, 0.38, 0.31]},
                ],
            }]
            result = dimensions.inspect_drawing(path, pages)
        circular = [item for item in result["candidates"] if "圆形" in item["type"]]
        self.assertTrue(circular)
        self.assertTrue(any(item["near_dimension"] for item in circular))
        self.assertTrue(all("无法证明" in item["detail"] or "未识别到" in item["detail"] for item in result["candidates"]))

    def test_header_zone_numbers_and_technical_notes_are_not_dimension_context(self):
        pages = [{
            "page_number": 1,
            "blocks": [
                {"type": "header", "text": "8 | 7 | 6 | 5 | 4 | 3 | 2 | 1", "bbox": [0.1, 0, 0.9, 0.03]},
                {"type": "text", "text": "1. 激光下料、打磨、成型。", "bbox": [0.1, 0.8, 0.4, 0.85]},
                {"type": "text", "text": "634.9 15° R2460", "bbox": [0.2, 0.2, 0.4, 0.3]},
                {"type": "table", "text": "图号 A-01", "bbox": [0.6, 0.7, 0.9, 0.9]},
            ],
        }]
        blocks = dimensions._text_blocks(pages[0])
        self.assertEqual([block["text"] for block in blocks], ["634.9 15° R2460"])

    def test_multipage_inspection_reaches_later_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "two-pages.tiff"
            _circle_page(path, pages=2)
            result = dimensions.inspect_drawing(path, [])
        self.assertEqual(result["pages_scanned"], 2)
        self.assertTrue(any(item["page_number"] == 2 for item in result["candidates"]))

    def test_blank_low_information_image_is_not_treated_as_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank.png"
            Image.new("RGB", (800, 500), "white").save(path)
            result = dimensions.inspect_drawing(path, [])
        self.assertFalse(result["failed"])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["limitations"])
        self.assertIn("对比度", result["limitations"][0])


if __name__ == "__main__":
    unittest.main()
