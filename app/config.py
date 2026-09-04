import os
from pathlib import Path

from fastapi import HTTPException

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


_load_env(BASE_DIR / ".env")

UPLOAD_DIR = Path(os.getenv("SOLIDCOG_UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
DATABASE_PATH = Path(os.getenv("SOLIDCOG_DATABASE_PATH", BASE_DIR / "database.db")).resolve()
TEMPLATE_DIR = BASE_DIR / "templates"
MAX_FILE_SIZE = 50 * 1024 * 1024

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen3-vl-plus")
QWEN_OCR_MAX_PAGES = max(1, int(os.getenv("QWEN_OCR_MAX_PAGES", "10")))
MECHVL_TIMEOUT_SECONDS = max(1, int(os.getenv("MECHVL_TIMEOUT_SECONDS", "180")))
MODEL_SCHEDULER_BASE_URL = os.getenv(
    "MODEL_SCHEDULER_BASE_URL", "http://127.0.0.1:8090"
).rstrip("/")
MODEL_SWITCH_TIMEOUT_SECONDS = max(1, int(os.getenv("MODEL_SWITCH_TIMEOUT_SECONDS", "360")))
MINERU_TIMEOUT_SECONDS = max(1, int(os.getenv("MINERU_TIMEOUT_SECONDS", "600")))
MINERU_RESULT_DIR = Path(
    os.getenv("MINERU_RESULT_DIR", BASE_DIR / "mineru_results")
).resolve()
PLACEHOLDER_VALUES = {
    "replace-with-your-qwen-api-key",
    "your-qwen-api-key",
}
def require_config(name: str, value: str) -> str:
    if not value or value.strip().lower() in PLACEHOLDER_VALUES:
        raise HTTPException(500, f"Missing required configuration: {name}")
    return value
