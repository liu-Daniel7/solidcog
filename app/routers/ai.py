import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.schemas import ChatWithDrawingRequest, QwenToolRequest
from app.security import verify_local_request
from app.services import ai, mechvl, model_scheduler

router = APIRouter()


@router.get("/mechvl/health")
def mechvl_health():
    return mechvl.health()


@router.get("/local-model/status")
def local_model_status():
    return model_scheduler.status()


@router.post("/local-model/switch/{mode}", dependencies=[Depends(verify_local_request)])
def local_model_switch(mode: str):
    return model_scheduler.switch(mode)


@router.post("/chat-with-drawing", dependencies=[Depends(verify_local_request)])
def chat_with_drawing(request: ChatWithDrawingRequest):
    return ai.chat_with_drawing(request.prompt, request.drawing_id)


@router.post("/chat-with-drawing/stream", dependencies=[Depends(verify_local_request)])
def chat_with_drawing_stream(request: ChatWithDrawingRequest):
    tokens = ai.chat_with_drawing_stream(request.prompt, request.drawing_id)

    def events():
        try:
            for token in tokens:
                yield f"event: token\ndata: {json.dumps({'text': token}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/qwen-tool", dependencies=[Depends(verify_local_request)])
def qwen_tool(request: QwenToolRequest):
    return ai.run_tool(request.tool_call, request.parameters)
