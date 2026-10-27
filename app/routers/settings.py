from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import verify_local_request
from app.services import qwen_credentials

router = APIRouter()


class QwenKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


@router.get("/settings/qwen")
def qwen_settings_status():
    return qwen_credentials.public_status()


@router.post("/settings/qwen", dependencies=[Depends(verify_local_request)])
def save_qwen_settings(request: QwenKeyRequest):
    try:
        return qwen_credentials.save_api_key(request.api_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"无法保存 Qwen API Key: {exc}") from exc


@router.delete("/settings/qwen", dependencies=[Depends(verify_local_request)])
def delete_qwen_settings():
    try:
        return qwen_credentials.clear_api_key()
    except OSError as exc:
        raise HTTPException(500, f"无法清除 Qwen API Key: {exc}") from exc


@router.post("/settings/qwen/test", dependencies=[Depends(verify_local_request)])
def test_qwen_settings():
    try:
        return qwen_credentials.test_models()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
