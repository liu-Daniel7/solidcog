from pydantic import BaseModel


class ChatWithDrawingRequest(BaseModel):
    prompt: str
    drawing_id: int


class QwenToolRequest(BaseModel):
    tool_call: str
    parameters: dict


class KitCheckCreateRequest(BaseModel):
    drawing_ids: list[int]
    mode: str = "all"
    root_drawing_ids: list[int] = []


class KitCheckItemUpdateRequest(BaseModel):
    code: str
    name: str = ""
    note: str = ""
    quantity: str = ""
