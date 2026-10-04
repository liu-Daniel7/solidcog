import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from app.services.images import load_pages, page_count
from app.services import ocr

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mineru_service_test', ROOT / 'platforms/macos/mineru_service.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class DrawingPipelineTests(unittest.TestCase):
    def test_transparent_linework_uses_white_background(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.png'
            image = Image.new('RGBA', (20, 10), (0, 0, 0, 0))
            image.putpixel((5, 5), (0, 0, 0, 255))
            image.save(path)
            page = load_pages(path, 72)[0]
            self.assertEqual(page.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(page.getpixel((5, 5)), (0, 0, 0))

    def test_multiframe_tiff_pages_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.tiff'
            Image.new('RGB', (20, 10), 'white').save(path, save_all=True, append_images=[Image.new('RGB', (10, 20), 'black')])
            self.assertEqual(page_count(path), 2)
            self.assertEqual([page.size for page in load_pages(path, 72)], [(20, 10), (10, 20)])
            self.assertEqual(len(load_pages(path, 72, limit=1)), 1)

    def test_qwen_reports_skipped_pages_and_region_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.pdf'
            Image.new('RGB', (10, 10), 'white').save(path, 'PDF', save_all=True, append_images=[Image.new('RGB', (10, 10), 'white')])
            response = dict(title_block='T', tech_block='R', all_text='A', layout='horizontal', page=1, warnings=['tile failed'])
            with patch.object(ocr.config, 'QWEN_OCR_MAX_PAGES', 1), patch.object(ocr, 'ocr_page', return_value=response):
                result = ocr.run_ocr(path)
            self.assertEqual(result['pages_total'], 2)
            self.assertTrue(any('限制' in warning for warning in result['page_errors']))
            self.assertTrue(any('tile failed' in warning for warning in result['page_errors']))

    def test_mineru_recovers_text_inside_drawing_image(self):
        model = Mock()
        model.two_step_extract.return_value = [{'type': 'image', 'bbox': [0.1, 0.1, 0.9, 0.9], 'content': ''}]
        model.extract_with_layout.return_value = [{'type': 'text', 'bbox': [0.1, 0.1, 0.9, 0.9], 'content': '⌀20 ±0.02'}]
        with patch.object(runtime, 'client', model):
            blocks, warnings = runtime._extract_drawing(Image.new('RGB', (100, 80)), 2)
        self.assertFalse(warnings)
        self.assertIn('⌀20 ±0.02', runtime._to_markdown(blocks))
        self.assertEqual(model.extract_with_layout.call_args.args[1][0]['type'], 'text')
        self.assertTrue(all(block['page_idx'] == 2 for block in blocks))

    def test_mineru_maps_tiles_and_preserves_repeated_dimensions(self):
        model = Mock()
        model.two_step_extract.side_effect = [[], [{'type': 'text', 'bbox': [0, 0, 0.1, 0.1], 'content': '20'}], [{'type': 'text', 'bbox': [0, 0, 0.1, 0.1], 'content': '20'}]]
        tiles = [((0, 0, 100, 100), Image.new('RGB', (100, 100))), ((100, 0, 200, 100), Image.new('RGB', (100, 100)))]
        with patch.object(runtime, 'client', model), patch.object(runtime, 'iter_overlapping_tiles', return_value=iter(tiles)), patch.dict('os.environ', {'MINERU_OCR_TILE_SIZE': '512'}):
            blocks, warnings = runtime._extract_drawing(Image.new('RGB', (1000, 100)), 0)
        self.assertEqual(len(blocks), 2)
        self.assertAlmostEqual(blocks[1]['bbox'][0], 0.1)
        self.assertFalse(warnings)

    def test_mineru_region_failure_keeps_base_and_reports_warning(self):
        model = Mock()
        model.two_step_extract.side_effect = [[{'type': 'text', 'bbox': [0, 0, 0.1, 0.1], 'content': 'material'}], RuntimeError('bad tile')]
        with patch.object(runtime, 'client', model), patch.object(runtime, 'iter_overlapping_tiles', return_value=iter([((0, 0, 100, 100), Image.new('RGB', (100, 100)))])), patch.dict('os.environ', {'MINERU_OCR_TILE_SIZE': '512'}):
            blocks, warnings = runtime._extract_drawing(Image.new('RGB', (1000, 100)), 0)
        self.assertEqual(blocks[0]['content'], 'material')
        self.assertIn('bad tile', warnings[0])


if __name__ == '__main__':
    unittest.main()
