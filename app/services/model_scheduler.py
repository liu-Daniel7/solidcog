from pathlib import Path

import requests
from fastapi import HTTPException

from app import config


_session = requests.Session()
_session.trust_env = False


def _error(exc: requests.RequestException) -> HTTPException:
    return HTTPException(503, f"本地模型调度器不可用: {exc}")


def status() -> dict:
    try:
        response = _session.get(f"{config.MODEL_SCHEDULER_BASE_URL}/status", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise _error(exc) from exc


def switch(mode: str) -> dict:
    if mode not in {"idle", "mineru", "mechvl"}:
        raise HTTPException(404, f"不支持的本地模型模式: {mode}")
    try:
        response = _session.post(
            f"{config.MODEL_SCHEDULER_BASE_URL}/switch/{mode}", timeout=10
        )
        payload = response.json()
        if response.status_code == 409:
            raise HTTPException(409, payload.get("detail", "本地模型正在忙碌"))
        response.raise_for_status()
        return payload
    except HTTPException:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise _error(exc) from exc


def parse_with_mineru(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            response = _session.post(
                f"{config.MODEL_SCHEDULER_BASE_URL}/mineru/parse",
                files={"file": (path.name, handle, "application/octet-stream")},
                timeout=config.MINERU_TIMEOUT_SECONDS,
            )
        payload = response.json()
        if not response.ok:
            detail = payload.get("detail") or payload.get("message") or payload.get("error")
            raise HTTPException(response.status_code, detail or "MinerU 解析失败")
        return payload
    except HTTPException:
        raise
    except (requests.RequestException, ValueError, OSError) as exc:
        raise HTTPException(503, f"MinerU 本地解析请求失败: {exc}") from exc


def analyze_with_mechvl(payload: dict) -> dict:
    try:
        response = _session.post(
            f"{config.MODEL_SCHEDULER_BASE_URL}/mechvl/analyze",
            json=payload,
            timeout=config.MECHVL_TIMEOUT_SECONDS + config.MODEL_SWITCH_TIMEOUT_SECONDS,
        )
        data = response.json()
        if response.status_code == 409:
            raise HTTPException(409, data.get("detail", "本地模型正在忙碌"))
        if response.status_code == 507:
            raise HTTPException(507, "MechVL 显存不足，请停止其他 GPU 程序后重试")
        response.raise_for_status()
        return data
    except HTTPException:
        raise
    except requests.Timeout as exc:
        raise HTTPException(504, "MechVL 切换或分析超时") from exc
    except (requests.RequestException, ValueError) as exc:
        raise _error(exc) from exc
