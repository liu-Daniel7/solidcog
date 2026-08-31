import os
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from scheduler import ModelScheduler, ServiceSpec, TimingHistory


ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
STATE_DIR = HOME / ".local" / "share" / "solidcog" / "scheduler"
MINERU_ROOT = HOME / ".local" / "share" / "solidcog" / "mineru"


def build_scheduler() -> ModelScheduler:
    specs = {
        "mineru": ServiceSpec(
            mode="mineru",
            command=(
                str(MINERU_ROOT / ".venv" / "bin" / "mineru-api"),
                "--host", "127.0.0.1", "--port", "8200",
                "--enable-vlm-preload", "true",
            ),
            cwd=MINERU_ROOT,
            health_url="http://127.0.0.1:8200/docs",
            log_path=STATE_DIR / "mineru.log",
            startup_timeout=int(os.getenv("MINERU_STARTUP_TIMEOUT", "180")),
            env={
                "MINERU_API_OUTPUT_ROOT": str(STATE_DIR / "mineru-output"),
                "MINERU_API_MAX_CONCURRENT_REQUESTS": "1",
            },
        ),
        "mechvl": ServiceSpec(
            mode="mechvl",
            command=(
                str(ROOT / "mechvl_server" / ".venv" / "bin" / "python"),
                "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8100",
            ),
            cwd=ROOT / "mechvl_server",
            health_url="http://127.0.0.1:8100/health",
            log_path=STATE_DIR / "mechvl.log",
            startup_timeout=int(os.getenv("MECHVL_STARTUP_TIMEOUT", "900")),
        ),
    }
    return ModelScheduler(specs, TimingHistory(STATE_DIR / "timings.json"))


scheduler = build_scheduler()
session = requests.Session()
session.trust_env = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    scheduler.shutdown()


app = FastAPI(title="SolidCog local model scheduler", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    question: str
    ocr_context: str = ""
    image_base64: str


@app.get("/health")
def health():
    return {"status": "ready"}


@app.get("/status")
def status():
    return scheduler.status()


@app.post("/switch/{target}")
def switch(target: str):
    try:
        return scheduler.request_switch(target)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/mineru/parse")
def mineru_parse(file: UploadFile = File(...)):
    try:
        scheduler.ensure_mode("mineru", timeout=240)
        scheduler.begin_operation("MinerU OCR")
        response = session.post(
            "http://127.0.0.1:8200/file_parse",
            files={"files": (file.filename or "drawing", file.file, file.content_type)},
            data={
                "backend": "vlm-engine",
                "return_md": "true",
                "return_content_list": "true",
                "return_middle_json": "true",
                "return_images": "false",
            },
            timeout=600,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:1000]}
        if not response.ok:
            return JSONResponse(status_code=response.status_code, content=payload)
        return payload
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(503, f"MinerU 服务请求失败: {exc}") from exc
    finally:
        if scheduler.busy_operation == "MinerU OCR":
            scheduler.end_operation()


@app.post("/mechvl/analyze")
def mechvl_analyze(request: AnalyzeRequest):
    try:
        scheduler.ensure_mode("mechvl", timeout=360)
        scheduler.begin_operation("MechVL 审核")
        response = session.post(
            "http://127.0.0.1:8100/analyze",
            json=request.model_dump(),
            timeout=600,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:1000]}
        return JSONResponse(status_code=response.status_code, content=payload)
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(503, f"MechVL 服务请求失败: {exc}") from exc
    finally:
        if scheduler.busy_operation == "MechVL 审核":
            scheduler.end_operation()
