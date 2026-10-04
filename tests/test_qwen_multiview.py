import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from app.services import qwen


def _completion(payload):
    message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _payload(title="", tech="", all_text="", layout="unknown"):
    return {"title_block": title, "tech_block": tech, "all_text": all_text, "layout": layout}


class QwenMultiviewTests(unittest.TestCase):
    def test_partial_view_failure_returns_successful_text_and_warning(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            _completion(_payload(title="图号 A-1", all_text="尺寸 Ø8", layout="vertical")),
            RuntimeError("tile unavailable"),
            _completion(_payload(tech="去毛刺", all_text="尺寸 Ø8")),
            _completion(_payload()),
            _completion(_payload()),
        ]
        with patch.object(qwen, "_client", return_value=client), patch.object(qwen.config, "QWEN_VL_MODEL", "configured-model"):
            result = qwen.ocr_page(Image.new("RGB", (80, 60), "white"), 2)
        self.assertEqual(result["title_block"], "图号 A-1")
        self.assertEqual(result["tech_block"], "去毛刺")
        self.assertIn("[整页]\n尺寸 Ø8", result["all_text"])
        self.assertIn("[tech_candidate_1]\n尺寸 Ø8", result["all_text"])
        self.assertEqual(result["layout"], "vertical")
        self.assertTrue(result["warnings"][0].startswith("title_candidate:"))
        self.assertTrue(all(call.kwargs["model"] == "configured-model" for call in client.chat.completions.create.call_args_list))

    def test_empty_responses_are_failure(self):
        client = Mock()
        client.chat.completions.create.return_value = _completion(_payload())
        with patch.object(qwen, "_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "未识别出"):
                qwen.ocr_page(Image.new("RGB", (80, 60), "white"), 1)

    def test_overview_failure_uses_dimensions_for_layout(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            RuntimeError("overview unavailable"),
            _completion(_payload(all_text="局部发现文本")),
            _completion(_payload()),
            _completion(_payload()),
            _completion(_payload()),
        ]
        with patch.object(qwen, "_client", return_value=client):
            result = qwen.ocr_page(Image.new("RGB", (120, 60), "white"), 1)
        self.assertEqual(result["layout"], "horizontal")
        self.assertEqual(result["all_text"], "[title_candidate]\n局部发现文本")
        self.assertEqual(len(result["warnings"]), 1)

    def test_parser_handles_null_and_malformed_json(self):
        self.assertEqual(qwen._parse_json("null")["all_text"], "")
        self.assertEqual(qwen._parse_json('{"all_text":')["all_text"], '{"all_text":')

    def test_truncated_response_preserves_text_with_warning(self):
        client = Mock()
        completion = _completion(_payload(all_text="尺寸 Ø8"))
        completion.choices[0].finish_reason = "length"
        client.chat.completions.create.return_value = completion
        with patch.object(qwen, "_client", return_value=client):
            result = qwen.ocr_page(Image.new("RGB", (80, 60), "white"), 1)
        self.assertIn("尺寸 Ø8", result["all_text"])
        self.assertTrue(any("长度限制" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
