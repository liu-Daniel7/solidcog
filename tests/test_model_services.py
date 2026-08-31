import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app import config
from app.services import ai, mechvl, mineru, model_scheduler, qwen


class ModelServiceTests(unittest.TestCase):
    def test_qwen_json_parser(self):
        result = qwen._parse_json('```json\n{"title_block":"A","tech_block":"B","all_text":"C","layout":"horizontal"}\n```')
        self.assertEqual(result["title_block"], "A")
        self.assertEqual(result["layout"], "horizontal")

    def test_qwen_quota_error_is_readable(self):
        error = Exception("raw provider response")
        error.status_code = 403
        self.assertIn("无可用额度", str(qwen._api_error(error)))

    def test_answer_sanitizer_handles_markdown_separators(self):
        self.assertEqual(ai.sanitize_answer("## 结果\n---\n- 正常"), "结果\n\n正常")

    def test_mechvl_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drawing.png"
            Image.new("RGB", (32, 32), "white").save(path)
            with patch.object(
                model_scheduler, "analyze_with_mechvl", return_value={"answer": "分析结果"}
            ) as analyze:
                answer = mechvl.analyze(path, "有什么问题？", "OCR内容")
        self.assertEqual(answer, "分析结果")
        payload = analyze.call_args.args[0]
        self.assertEqual(payload["question"], "有什么问题？")
        self.assertTrue(payload["image_base64"])

    def test_mechvl_unavailable(self):
        with patch.object(model_scheduler, "status", side_effect=Exception("offline")):
            with self.assertRaisesRegex(Exception, "offline"):
                mechvl.health()

    def test_mechvl_ignores_environment_proxy(self):
        self.assertFalse(mechvl._session.trust_env)

    def test_mechvl_preview_is_bounded_for_laptop_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.png"
            Image.new("RGB", (2000, 1600), "white").save(path)
            preview = mechvl._preview(path)
        self.assertEqual(preview.size, (1024, 819))

    def test_expected_model_defaults(self):
        self.assertEqual(config.QWEN_VL_MODEL, "qwen3-vl-plus")
        self.assertEqual(config.MECHVL_BASE_URL, "http://127.0.0.1:8100")

    def test_mineru_markdown_adapter_preserves_engineering_fields(self):
        markdown = """# 技术要求

1. 未注尺寸公差按GB/T1804-m;
2. 表面处理：镀镍。

<table><tr><td>固定底板</td><td>GAC-OP525-0-2</td></tr><tr><td>材料</td><td>Q235</td></tr></table>
"""
        self.assertIn("GB/T1804-m", mineru._tech_block(markdown))
        self.assertIn("GAC-OP525-0-2", mineru._title_block(markdown))
        self.assertIn("Q235", mineru._plain_markdown(markdown))


if __name__ == "__main__":
    unittest.main()
