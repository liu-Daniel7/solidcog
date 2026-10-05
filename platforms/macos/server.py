"""SolidCog macOS model scheduler. Windows/WSL implementation stays isolated."""
from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import config as _app_config  # noqa: E402,F401 - loads root .env
sys.path.insert(0, str(ROOT / "model_scheduler"))
from scheduler import ModelScheduler, ServiceSpec, TimingHistory  # noqa: E402

STATE_DIR = Path.home() / "Library/Application Support/SolidCog/scheduler"
LOG_DIR = Path.home() / "Library/Logs/SolidCog"
PYTHON = Path(os.getenv("SOLIDCOG_MAC_PYTHON", ROOT / ".venv/bin/python"))


def _project_path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default))).expanduser()
    return value if value.is_absolute() else ROOT / value


def _llama_server() -> Path:
    configured = os.getenv("LLAMA_SERVER_PATH")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(shutil.which("llama-server")) if shutil.which("llama-server") else None,
        ROOT / "platforms/macos/vendor/llama-server",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError("找不到 llama-server，请设置 LLAMA_SERVER_PATH")


LLAMA_SERVER = _llama_server()


def build_scheduler() -> ModelScheduler:
    return ModelScheduler({
        "mineru": ServiceSpec(
            "mineru", (str(PYTHON), "-m", "uvicorn", "mineru_service:app", "--host", "127.0.0.1", "--port", "8200"),
            Path(__file__).parent, "http://127.0.0.1:8200/health", LOG_DIR / "mineru.log",
            int(os.getenv("MINERU_STARTUP_TIMEOUT", "180")),
        ),
        "mechvl": ServiceSpec(
            "mechvl", (
                str(LLAMA_SERVER), "--host", "127.0.0.1", "--port", "8100",
                "--model", str(_project_path("MECHVL_MODEL_PATH", ROOT / "models/mechvl/mechvl-model-q5.gguf")),
                "--mmproj", str(_project_path("MECHVL_MMPROJ_PATH", ROOT / "models/mechvl/mechvl-vision-f16.gguf")),
                "--ctx-size", os.getenv("MECHVL_CONTEXT", "8192"), "--n-gpu-layers", "99",
            ), ROOT, "http://127.0.0.1:8100/health", LOG_DIR / "mechvl.log",
            int(os.getenv("MECHVL_STARTUP_TIMEOUT", "180")),
        ),
    }, TimingHistory(STATE_DIR / "timings.json"))


scheduler = build_scheduler()
session = requests.Session()
session.trust_env = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    scheduler.shutdown()


app = FastAPI(title="SolidCog macOS model scheduler", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    question: str
    ocr_context: str = ""
    image_base64: str


@app.get("/health")
def health(): return {"status": "ready", "platform": "macos"}


@app.get("/status")
def status(): return {**scheduler.status(), "platform": "macos", "accelerator": "Metal"}


@app.post("/switch/{target}")
def switch(target: str):
    try: return scheduler.request_switch(target)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(409, str(exc)) from exc


@app.post("/mineru/parse")
def mineru_parse(file: UploadFile = File(...)):
    try:
        scheduler.ensure_mode("mineru", timeout=240)
        scheduler.begin_operation("MinerU OCR")
        response = session.post("http://127.0.0.1:8200/file_parse", files={"files": (file.filename or "drawing", file.file, file.content_type)}, timeout=600)
        payload = response.json()
        return JSONResponse(status_code=response.status_code, content=payload)
    except TimeoutError as exc: raise HTTPException(504, str(exc)) from exc
    except (RuntimeError, requests.RequestException, ValueError) as exc: raise HTTPException(503, str(exc)) from exc
    finally:
        if scheduler.busy_operation == "MinerU OCR": scheduler.end_operation()


@app.post("/mechvl/analyze")
def mechvl_analyze(request: AnalyzeRequest):
    try:
        scheduler.ensure_mode("mechvl", timeout=240)
        scheduler.begin_operation("MechVL 审核")
        context = f"\n\nOCR/图纸文字参考：\n{request.ocr_context}" if request.ocr_context else ""
        payload = {
            "model": "mechvl", "temperature": 0.1, "max_tokens": 2048,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": request.question + context},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + request.image_base64}},
            ]}],
        }
        response = session.post("http://127.0.0.1:8100/v1/chat/completions", json=payload, timeout=600)
        data = response.json()
        if not response.ok: return JSONResponse(status_code=response.status_code, content=data)
        answer = data["choices"][0]["message"]["content"]
        return {"answer": answer}
    except TimeoutError as exc: raise HTTPException(504, str(exc)) from exc
    except (RuntimeError, requests.RequestException, ValueError, KeyError, IndexError) as exc: raise HTTPException(503, str(exc)) from exc
    finally:
        if scheduler.busy_operation == "MechVL 审核": scheduler.end_operation()


@app.post("/mechvl/analyze/stream")
def mechvl_analyze_stream(request: AnalyzeRequest):
    operation_started = False
    try:
        scheduler.ensure_mode("mechvl", timeout=240)
        scheduler.begin_operation("MechVL 审核")
        operation_started = True
        context = f"\n\nOCR/图纸文字参考：\n{request.ocr_context}" if request.ocr_context else ""
        payload = {
            "model": "mechvl", "temperature": 0.1, "max_tokens": 2048, "stream": True,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": request.question + context},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + request.image_base64}},
            ]}],
        }
        response = session.post(
            "http://127.0.0.1:8100/v1/chat/completions",
            json=payload,
            stream=True,
            timeout=(10, 600),
        )
        response.raise_for_status()
    except TimeoutError as exc:
        if operation_started: scheduler.end_operation()
        raise HTTPException(504, str(exc)) from exc
    except (RuntimeError, requests.RequestException) as exc:
        if operation_started: scheduler.end_operation()
        raise HTTPException(503, str(exc)) from exc

    def events():
        try:
            for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    if event.get("error"):
                        yield f"data: {json.dumps({'error': event['error']}, ensure_ascii=False)}\n\n"
                        break
                    token = event["choices"][0].get("delta", {}).get("content")
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                if token:
                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except requests.RequestException as exc:
            message = str(exc)
            yield f"data: {json.dumps({'error': message}, ensure_ascii=False)}\n\n"
        finally:
            response.close()
            scheduler.end_operation()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
