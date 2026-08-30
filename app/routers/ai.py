from fastapi import APIRouter

from app.schemas import ChatWithDrawingRequest, QwenToolRequest
from app.services import ai, mechvl

router = APIRouter()


@router.get("/mechvl/health")
def mechvl_health():
    return mechvl.health()


@router.post("/chat-with-drawing")
def chat_with_drawing(request: ChatWithDrawingRequest):
    return ai.chat_with_drawing(request.prompt, request.drawing_id)


@router.post("/qwen-tool")
def qwen_tool(request: QwenToolRequest):
    return ai.run_tool(request.tool_call, request.parameters)
