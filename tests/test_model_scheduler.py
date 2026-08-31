import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from model_scheduler.scheduler import ModelScheduler, ServiceSpec, TimingHistory


class FakeScheduler(ModelScheduler):
    def _start(self, mode):
        self._processes[mode] = object()

    def _wait_healthy(self, mode):
        return None

    def _stop(self, mode):
        self._processes.pop(mode, None)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        spec = lambda mode: ServiceSpec(mode, ("true",), root, "http://local", root / f"{mode}.log", 1)
        self.scheduler = FakeScheduler(
            {"mineru": spec("mineru"), "mechvl": spec("mechvl")},
            TimingHistory(root / "timings.json"),
        )

    def tearDown(self):
        self.scheduler.shutdown()
        self.temp.cleanup()

    def wait_for(self, state):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.scheduler.status()["state"] == state:
                return
            time.sleep(0.01)
        self.fail(f"state did not become {state}")

    @patch("model_scheduler.scheduler.time.sleep", return_value=None)
    def test_models_switch_mutually_exclusive(self, _sleep):
        self.scheduler.request_switch("mineru")
        self.wait_for("mineru_ready")
        self.assertEqual(set(self.scheduler._processes), {"mineru"})
        self.scheduler.request_switch("mechvl")
        self.wait_for("mechvl_ready")
        self.assertEqual(set(self.scheduler._processes), {"mechvl"})

    def test_busy_model_rejects_switch(self):
        self.scheduler.begin_operation("test")
        with self.assertRaisesRegex(RuntimeError, "正在执行"):
            self.scheduler.request_switch("mineru")
        self.scheduler.end_operation()

    def test_timing_history_rolls_and_estimates(self):
        history = self.scheduler.history
        for value in (10, 20, 30, 40, 50, 60):
            history.record("mineru", value)
        self.assertEqual(history.estimate("mineru"), 40)
        restored = TimingHistory(history.path)
        self.assertEqual(restored.estimate("mineru"), 40)


if __name__ == "__main__":
    unittest.main()
