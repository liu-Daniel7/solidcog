import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services import qwen, qwen_credentials, qwen_mt


class QwenCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text(
            "# local settings\nLLAMA_SERVER_PATH=/tmp/llama-server\nQWEN_API_KEY=old-key-value\n",
            encoding="utf-8",
        )
        self.patches = [
            patch.object(config, "ENV_PATH", self.env_path),
            patch.object(config, "QWEN_API_KEY", ""),
            patch.dict(os.environ, {}, clear=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_save_key_persists_masks_and_takes_effect_immediately(self):
        status = qwen_credentials.save_api_key("sk-test-1234567890")

        self.assertTrue(status["configured"])
        self.assertEqual(status["masked_key"], "••••••••••••")
        self.assertNotIn("sk-test-1234567890", json.dumps(status))
        self.assertEqual(config.QWEN_API_KEY, "sk-test-1234567890")
        self.assertEqual(os.environ["QWEN_API_KEY"], "sk-test-1234567890")
        contents = self.env_path.read_text(encoding="utf-8")
        self.assertIn("QWEN_API_KEY=sk-test-1234567890", contents)
        self.assertIn("LLAMA_SERVER_PATH=/tmp/llama-server", contents)
        self.assertNotIn("QWEN_API_KEY=old-key-value", contents)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o600)

    def test_both_model_channels_read_the_saved_key_immediately(self):
        qwen_credentials.save_api_key("sk-test-1234567890")
        with patch("openai.OpenAI") as client_factory:
            qwen._client()
            qwen_mt._client()
        keys = [call.kwargs["api_key"] for call in client_factory.call_args_list]
        self.assertEqual(keys, ["sk-test-1234567890", "sk-test-1234567890"])

    def test_clear_key_removes_only_qwen_line(self):
        qwen_credentials.save_api_key("sk-test-1234567890")
        status = qwen_credentials.clear_api_key()

        self.assertFalse(status["configured"])
        self.assertEqual(config.QWEN_API_KEY, "")
        contents = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("QWEN_API_KEY=", contents)
        self.assertIn("LLAMA_SERVER_PATH=/tmp/llama-server", contents)

    def test_invalid_keys_are_rejected(self):
        for value in ("", "short", "sk-test key", "x" * 513):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    qwen_credentials.save_api_key(value)

    def test_clear_without_env_file_is_a_noop(self):
        self.env_path.unlink()
        status = qwen_credentials.clear_api_key()
        self.assertFalse(status["configured"])
        self.assertFalse(self.env_path.exists())


if __name__ == "__main__":
    unittest.main()
