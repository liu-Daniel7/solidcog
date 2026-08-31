from fastapi import APIRouter

from app.schemas import ChatWithDrawingRequest, QwenToolRequest
from app.services import ai, mechvl, model_scheduler

router = APIRouter()


@router.get("/mechvl/health")
def mechvl_health():
    return mechvl.health()


@router.get("/local-model/status")
def local_model_status():
    return model_scheduler.status()


@router.post("/local-model/switch/{mode}")
def local_model_switch(mode: str):
    return model_scheduler.switch(mode)


@router.post("/chat-with-drawing")
def chat_with_drawing(request: ChatWithDrawingRequest):
    return ai.chat_with_drawing(request.prompt, request.drawing_id)


@router.post("/qwen-tool")
def qwen_tool(request: QwenToolRequest):
    return ai.run_tool(request.tool_call, request.parameters)
