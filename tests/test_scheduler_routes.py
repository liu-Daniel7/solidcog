import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class SchedulerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtimes = []
        for name, path in (
            ("wsl_scheduler_routes", ROOT / "model_scheduler/server.py"),
            ("mac_scheduler_routes", ROOT / "platforms/macos/server.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, path)
            runtime = importlib.util.module_from_spec(spec)
            with patch.dict("os.environ", {"LLAMA_SERVER_PATH": sys.executable}), patch.object(
                sys, "path", [str(ROOT / "model_scheduler"), *sys.path]
            ):
                spec.loader.exec_module(runtime)
            cls.runtimes.append(runtime)

    @staticmethod
    def request(client, operation):
        if operation == "MinerU OCR":
            return client.post("/mineru/parse", files={"file": ("sample.pdf", b"%PDF")})
        return client.post("/mechvl/analyze", json={"question": "test", "image_base64": "test"})

    def test_rejected_request_does_not_release_another_operation(self):
        for runtime in self.runtimes:
            for operation in ("MinerU OCR", "MechVL 审核"):
                for failure in ("ensure_mode", "begin_operation"):
                    with self.subTest(runtime=runtime.__name__, operation=operation, failure=failure):
                        scheduler = Mock(busy_operation=operation)
                        getattr(scheduler, failure).side_effect = RuntimeError("busy")
                        with patch.object(runtime, "scheduler", scheduler), patch.object(runtime, "session") as session:
                            response = self.request(TestClient(runtime.app), operation)
                        self.assertEqual(response.status_code, 503)
                        scheduler.end_operation.assert_not_called()
                        session.post.assert_not_called()

    def test_successful_request_releases_its_operation(self):
        for runtime in self.runtimes:
            for operation in ("MinerU OCR", "MechVL 审核"):
                with self.subTest(runtime=runtime.__name__, operation=operation):
                    scheduler = Mock()
                    result = Mock(status_code=200, ok=True)
                    result.json.return_value = {"choices": [{"message": {"content": "answer"}}]}
                    with patch.object(runtime, "scheduler", scheduler), patch.object(runtime.session, "post", return_value=result):
                        response = self.request(TestClient(runtime.app), operation)
                    self.assertEqual(response.status_code, 200)
                    scheduler.begin_operation.assert_called_once_with(operation)
                    scheduler.end_operation.assert_called_once_with()

    def test_upstream_failure_releases_its_operation(self):
        for runtime in self.runtimes:
            for operation in ("MinerU OCR", "MechVL 审核"):
                with self.subTest(runtime=runtime.__name__, operation=operation):
                    scheduler = Mock()
                    with patch.object(runtime, "scheduler", scheduler), patch.object(
                        runtime.session, "post", side_effect=runtime.requests.ConnectionError("offline")
                    ):
                        response = self.request(TestClient(runtime.app), operation)
                    self.assertEqual(response.status_code, 503)
                    scheduler.end_operation.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
