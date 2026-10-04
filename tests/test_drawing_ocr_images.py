import unittest

from PIL import Image

from app.services.drawing_ocr_images import build_ocr_views, iter_overlapping_tiles, merge_lines


class DrawingOcrImageTests(unittest.TestCase):
    def test_tiles_cover_page_edges_and_keep_detail_resolution(self):
        image = Image.new("RGB", (5000, 3100), "white")
        views = build_ocr_views(image, tile_size=2000, overlap=0.2)
        tiles = [view for view in views if view.name.startswith("tile_")]
        self.assertGreater(len(tiles), 1)
        self.assertTrue(any(view.image.width == 2000 for view in tiles))
        self.assertTrue(any(view.name.endswith("_1100") for view in tiles))
        self.assertTrue(all(max(view.image.size) <= 2000 for view in tiles))

    def test_field_candidate_views_cover_common_sheet_zones(self):
        image = Image.new("RGB", (1000, 800), "white")
        views = build_ocr_views(image)
        title = next(view for view in views if view.name == "title_candidate")
        tech = [view for view in views if view.name.startswith("tech_candidate_")]
        self.assertEqual(title.image.size, (480, 360))
        self.assertEqual(len(tech), 3)
        self.assertTrue(all(view.image.size[0] > 0 and view.image.size[1] > 0 for view in tech))

    def test_shared_tile_iterator_includes_bottom_and_right_edges(self):
        tiles = list(iter_overlapping_tiles(Image.new("RGB", (5100, 3300)), tile_size=2000))
        boxes = [box for box, _ in tiles]
        self.assertIn((3100, 1300, 5100, 3300), boxes)
        self.assertGreater(len(boxes), 4)
        self.assertEqual(len(boxes), len(set(boxes)))

    def test_merge_lines_only_removes_exact_duplicate_lines(self):
        merged = merge_lines(["A 12.5\n孔径 Ø8", "a 12.5\n材料 Q235"])
        self.assertEqual(merged, "A 12.5\n孔径 Ø8\na 12.5\n材料 Q235")


if __name__ == "__main__":
    unittest.main()
