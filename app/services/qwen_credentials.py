"""Local Qwen credential storage and public status helpers.

The API key lives in the local, git-ignored .env file. The browser never
receives the key; it only receives a configured flag and a fixed mask.
"""

from __future__ import annotations

import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from app import config
from app.services import qwen, qwen_mt


def is_configured() -> bool:
    value = (config.QWEN_API_KEY or "").strip()
    return bool(value and value.lower() not in config.PLACEHOLDER_VALUES)


def _validate_key(raw_key: str) -> str:
    key = (raw_key or "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
        key = key[1:-1].strip()
    if not key:
        raise ValueError("请输入 Qwen API Key")
    if len(key) < 8:
        raise ValueError("Qwen API Key 长度过短")
    if len(key) > 512:
        raise ValueError("Qwen API Key 过长")
    if any(character.isspace() for character in key):
        raise ValueError("Qwen API Key 不能包含空白字符")
    return key


def _write_env_value(path: Path, name: str, value: str | None) -> None:
    """Replace one key in .env without disturbing comments or other settings."""
    if value is None and not path.exists():
        return
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    output: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if not replaced and value is not None:
                output.append(f"{name}={value}")
            replaced = True
        else:
            output.append(line)
    if value is not None and not replaced:
        output.append(f"{name}={value}")

    content = "\n".join(output).strip("\n")
    if content:
        content += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def public_status() -> dict:
    configured = is_configured()
    base_url = config.QWEN_BASE_URL.rstrip("/")
    parsed = urlsplit(base_url)
    return {
        "configured": configured,
        "masked_key": "••••••••••••" if configured else "",
        "base_url": base_url,
        "base_host": parsed.netloc or base_url,
        "vl_model": config.QWEN_VL_MODEL,
        "mt_model": config.QWEN_MT_MODEL,
        "shared_key": True,
    }


def save_api_key(raw_key: str) -> dict:
    key = _validate_key(raw_key)
    _write_env_value(config.ENV_PATH, "QWEN_API_KEY", key)
    config.QWEN_API_KEY = key
    os.environ["QWEN_API_KEY"] = key
    return public_status()


def clear_api_key() -> dict:
    _write_env_value(config.ENV_PATH, "QWEN_API_KEY", None)
    config.QWEN_API_KEY = ""
    os.environ.pop("QWEN_API_KEY", None)
    return public_status()


def test_models() -> dict:
    if not is_configured():
        raise ValueError("请先保存 Qwen API Key")
    # Both checks are independent and use the same configured key.
    with ThreadPoolExecutor(max_workers=2) as pool:
        vl_future = pool.submit(qwen.test_connection)
        mt_future = pool.submit(qwen_mt.test_connection)
        vl_result = vl_future.result()
        mt_result = mt_future.result()
    key = config.QWEN_API_KEY
    for result in (vl_result, mt_result):
        for field in ("message", "sample"):
            if isinstance(result.get(field), str) and key:
                result[field] = result[field].replace(key, "[已隐藏]")
    return {
        "ok": bool(vl_result.get("ok") and mt_result.get("ok")),
        "qwen_vl": vl_result,
        "qwen_mt": mt_result,
    }
