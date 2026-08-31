import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests


@dataclass(frozen=True)
class ServiceSpec:
    mode: str
    command: tuple[str, ...]
    cwd: Path
    health_url: str
    log_path: Path
    startup_timeout: int
    env: dict[str, str] | None = None


class TimingHistory:
    def __init__(self, path: Path, window: int = 5):
        self.path = path
        self.window = window
        self._lock = threading.Lock()
        self._values = self._load()

    def _load(self) -> dict[str, list[float]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {
                mode: [float(value) for value in values[-self.window :]]
                for mode, values in data.items()
                if mode in {"mineru", "mechvl"} and isinstance(values, list)
            }
        except (OSError, ValueError, TypeError):
            return {}

    def estimate(self, mode: str) -> float | None:
        with self._lock:
            values = self._values.get(mode, [])
            return sum(values) / len(values) if values else None

    def record(self, mode: str, seconds: float) -> None:
        with self._lock:
            values = deque(self._values.get(mode, []), maxlen=self.window)
            values.append(round(seconds, 3))
            self._values[mode] = list(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._values, indent=2), encoding="utf-8")
            temporary.replace(self.path)


class ModelScheduler:
    def __init__(
        self,
        specs: dict[str, ServiceSpec],
        history: TimingHistory,
        health_check: Callable[[str], bool] | None = None,
    ):
        self.specs = specs
        self.history = history
        self.health_check = health_check or self._default_health_check
        self._lock = threading.RLock()
        self._switch_lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._processes: dict[str, subprocess.Popen] = {}
        self.state = "idle"
        self.current_mode = "idle"
        self.target_mode = "idle"
        self.stage = "idle"
        self.started_at: float | None = None
        self.last_duration: float | None = None
        self.busy = False
        self.busy_operation: str | None = None
        self.error: str | None = None

    @staticmethod
    def _default_health_check(url: str) -> bool:
        try:
            response = requests.get(url, timeout=2)
            return response.status_code < 500
        except requests.RequestException:
            return False

    def status(self) -> dict:
        with self._lock:
            elapsed = time.monotonic() - self.started_at if self.started_at else 0
            estimate = self.history.estimate(self.target_mode) if self.state == "switching" else None
            return {
                "state": self.state,
                "current_mode": self.current_mode,
                "target_mode": self.target_mode,
                "stage": self.stage,
                "started_at": (
                    datetime.now(timezone.utc).timestamp() - elapsed if self.started_at else None
                ),
                "elapsed_seconds": round(elapsed, 1),
                "estimated_total_seconds": round(estimate, 1) if estimate else None,
                "last_duration_seconds": self.last_duration,
                "busy": self.busy,
                "busy_operation": self.busy_operation,
                "error": self.error,
            }

    def request_switch(self, target: str) -> dict:
        if target not in {"idle", *self.specs.keys()}:
            raise ValueError(f"unsupported mode: {target}")
        with self._lock:
            if self.busy:
                raise RuntimeError(f"模型正在执行{self.busy_operation or '任务'}")
            if self.state == "switching":
                if self.target_mode != target:
                    raise RuntimeError(f"正在切换到 {self.target_mode}")
                return self.status()
            if self.current_mode == target and self.state != "error":
                return self.status()
            self.state = "switching"
            self.target_mode = target
            self.stage = "queued"
            self.started_at = time.monotonic()
            self.last_duration = None
            self.error = None
        threading.Thread(target=self._switch, args=(target,), daemon=True).start()
        return self.status()

    def ensure_mode(self, target: str, timeout: int) -> dict:
        self.request_switch(target)
        deadline = time.monotonic() + timeout
        with self._ready:
            while True:
                if self.state == f"{target}_ready" and self.current_mode == target:
                    return self.status()
                if self.state == "error":
                    raise RuntimeError(self.error or f"{target} 启动失败")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待 {target} 就绪超时")
                self._ready.wait(min(remaining, 1))

    def _set_stage(self, stage: str) -> None:
        with self._ready:
            self.stage = stage
            self._ready.notify_all()

    def _switch(self, target: str) -> None:
        with self._switch_lock:
            try:
                previous = self.current_mode
                if previous != "idle":
                    self._set_stage(f"stopping_{previous}")
                    self._stop(previous)
                    self._set_stage("releasing_gpu")
                    time.sleep(2)
                if target != "idle":
                    self._set_stage(f"starting_{target}")
                    self._start(target)
                    self._set_stage(f"waiting_{target}")
                    self._wait_healthy(target)
                duration = time.monotonic() - (self.started_at or time.monotonic())
                if target != "idle":
                    self.history.record(target, duration)
                with self._ready:
                    self.current_mode = target
                    self.target_mode = target
                    self.state = "idle" if target == "idle" else f"{target}_ready"
                    self.stage = "ready" if target != "idle" else "idle"
                    self.last_duration = round(duration, 1)
                    self.started_at = None
                    self.error = None
                    self._ready.notify_all()
            except Exception as exc:
                self._stop(target)
                with self._ready:
                    self.current_mode = "idle"
                    self.state = "error"
                    self.stage = "failed"
                    self.error = str(exc)
                    self.started_at = None
                    self._ready.notify_all()

    def _start(self, mode: str) -> None:
        spec = self.specs[mode]
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = spec.log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env.update(spec.env or {})
        try:
            process = subprocess.Popen(
                list(spec.command),
                cwd=spec.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log.close()
            raise
        process._solidcog_log = log
        self._processes[mode] = process

    def _wait_healthy(self, mode: str) -> None:
        spec = self.specs[mode]
        deadline = time.monotonic() + spec.startup_timeout
        while time.monotonic() < deadline:
            process = self._processes.get(mode)
            if process is None or process.poll() is not None:
                raise RuntimeError(f"{mode} 进程启动后异常退出，请查看 {spec.log_path}")
            if self.health_check(spec.health_url):
                return
            time.sleep(1)
        raise TimeoutError(f"{mode} 启动超时，请查看 {spec.log_path}")

    def _stop(self, mode: str) -> None:
        process = self._processes.pop(mode, None)
        if not process:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            except ProcessLookupError:
                pass
        log = getattr(process, "_solidcog_log", None)
        if log:
            log.close()

    def begin_operation(self, name: str) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError(f"模型正在执行{self.busy_operation or '任务'}")
            self.busy = True
            self.busy_operation = name

    def end_operation(self) -> None:
        with self._ready:
            self.busy = False
            self.busy_operation = None
            self._ready.notify_all()

    def shutdown(self) -> None:
        for mode in tuple(self.specs):
            self._stop(mode)
