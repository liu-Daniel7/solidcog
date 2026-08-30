import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app import config
from app.services import ai, mechvl, qwen


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
            response = Mock(status_code=200)
            response.raise_for_status.return_value = None
            response.json.return_value = {"answer": "分析结果"}
            with patch.object(mechvl._session, "post", return_value=response) as post:
                answer = mechvl.analyze(path, "有什么问题？", "OCR内容")
        self.assertEqual(answer, "分析结果")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["question"], "有什么问题？")
        self.assertTrue(payload["image_base64"])

    def test_mechvl_unavailable(self):
        with patch.object(mechvl._session, "get", side_effect=mechvl.requests.ConnectionError):
            with self.assertRaisesRegex(Exception, "MechVL 本地服务未启动"):
                mechvl.health()

    def test_mechvl_ignores_environment_proxy(self):
        self.assertFalse(mechvl._session.trust_env)

    def test_mechvl_preview_is_bounded_for_laptop_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.png"
            Image.new("RGB", (2000, 1600), "white").save(path)
            preview = mechvl._preview(path)
        self.assertEqual(preview.size, (1536, 1229))

    def test_expected_model_defaults(self):
        self.assertEqual(config.QWEN_VL_MODEL, "qwen3-vl-plus")
        self.assertEqual(config.MECHVL_BASE_URL, "http://127.0.0.1:8100")


if __name__ == "__main__":
    unittest.main()
