import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import config
from app.services import qwen, qwen_mt


def completion(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class QwenChannelTests(unittest.TestCase):
    def test_qwen_mt_uses_shared_key_and_translation_options(self):
        client = Mock()
        client.chat.completions.create.return_value = completion("Test")
        with patch.object(qwen_mt, "_client", return_value=client), patch.object(config, "QWEN_MT_MODEL", "qwen-mt-plus"):
            result = qwen_mt.test_connection()

        self.assertTrue(result["ok"])
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen-mt-plus")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "测试"}])
        self.assertEqual(kwargs["extra_body"]["translation_options"], {
            "source_lang": "Chinese",
            "target_lang": "English",
        })

    def test_translate_text_sends_glossary_and_returns_usage(self):
        client = Mock()
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Deburr"))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
        )
        client.chat.completions.create.return_value = completion
        with patch.object(qwen_mt, "_client", return_value=client), patch.object(config, "QWEN_MT_MODEL", "qwen-mt-plus"):
            result = qwen_mt.translate_text(
                "去毛刺",
                target_lang="en",
                source_lang="zh",
                terms=[{"source": "去毛刺", "target": "Deburr"}],
                domains="Mechanical drawing.",
            )
        self.assertEqual(result, {"text": "Deburr", "input_tokens": 12, "output_tokens": 3})
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"]["translation_options"]["terms"], [{"source": "去毛刺", "target": "Deburr"}])
        self.assertEqual(kwargs["extra_body"]["translation_options"]["domains"], "Mechanical drawing.")

    def test_qwen_vl_connection_test_sends_an_image(self):
        client = Mock()
        client.chat.completions.create.return_value = completion("OK")
        with patch.object(qwen, "_client", return_value=client), patch.object(config, "QWEN_VL_MODEL", "qwen3-vl-plus"):
            result = qwen.test_connection()

        self.assertTrue(result["ok"])
        content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_qwen_mt_auth_error_is_actionable(self):
        error = Exception("provider detail")
        error.status_code = 401
        self.assertIn("无效", str(qwen_mt._api_error(error)))


if __name__ == "__main__":
    unittest.main()
