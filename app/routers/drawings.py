from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from app.repositories import drawings as repository
from app.services import drawings as service

router = APIRouter()


@router.post("/upload-drawing")
def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    ocr_backend: str = Form("qwen"),
):
    if not files:
        raise HTTPException(400, "请选择至少一个文件")
    if ocr_backend not in {"qwen", "mineru"}:
        raise HTTPException(400, "OCR 后端必须是 qwen 或 mineru")
    uploaded = [service.save_upload(file, ocr_backend) for file in files]
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": True, "files": uploaded})
    return RedirectResponse("/home", 303)


@router.get("/drawings")
def list_drawings():
    rows = repository.list_all("ASC")
    drawings = [{"编号": index, "id": row["id"], "文件名": row["filename"], "文件类型": row["file_type"], "文件大小(字节)": row["file_size"], "上传时间": row["upload_time"]} for index, row in enumerate(rows, 1)]
    return {"图纸数量": len(drawings), "图纸信息": drawings}


@router.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int):
    service.delete(drawing_id)
    return RedirectResponse("/home", 303)


@router.get("/delete-drawing/{drawing_id}")
def delete_drawing_from_page(drawing_id: int):
    service.delete(drawing_id)
    return RedirectResponse("/home", 303)


@router.get("/delete-all-drawings")
def delete_all_drawings():
    service.delete_all()
    return RedirectResponse("/home", 303)


@router.get("/ocr/{drawing_id}")
def ocr_text(drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    return {"文件名": drawing["filename"], "标题栏": drawing["title_text"] or "", "技术要求": drawing["tech_text"] or "", "全局OCR": drawing["all_text"] or ""}
