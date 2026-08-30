from pydantic import BaseModel


class ChatWithDrawingRequest(BaseModel):
    prompt: str
    drawing_id: int


class QwenToolRequest(BaseModel):
    tool_call: str
    parameters: dict
