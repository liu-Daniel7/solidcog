"""Regression coverage for the macOS runtime missing from main."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class MacRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "solidcog_macos_test_server", ROOT / "platforms/macos/server.py"
        )
        cls.runtime = importlib.util.module_from_spec(spec)
        # Importing the scheduler must not launch models; no llama installation
        # is needed to exercise the HTTP contract on Windows/Linux CI.
        with patch.dict("os.environ", {"LLAMA_SERVER_PATH": sys.executable}), patch.object(sys, "path", sys.path.copy()):
            spec.loader.exec_module(cls.runtime)

    def test_health_identifies_macos(self):
        with TestClient(self.runtime.app) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ready", "platform": "macos"})

    def test_idle_status_requires_no_model_process(self):
        with TestClient(self.runtime.app) as client:
            status = client.get("/status").json()
            self.assertEqual(status["current_mode"], "idle")
            self.assertEqual(status["platform"], "macos")
            self.assertFalse(status["busy"])

    def test_launcher_and_documented_runtime_are_present(self):
        launcher = (ROOT / "start_macos.command").read_text()
        self.assertIn('platforms/macos/start.sh', launcher)
        for name in ("start.sh", "setup.sh", "server.py", "mineru_service.py", "requirements.txt", "README.md"):
            self.assertTrue((ROOT / "platforms/macos" / name).is_file(), name)
        script = (ROOT / "platforms/macos/start.sh").read_text()
        self.assertIn('--app-dir "$ROOT/platforms/macos"', script)
        self.assertIn('scheduler_ready', script)


if __name__ == "__main__":
    unittest.main()
