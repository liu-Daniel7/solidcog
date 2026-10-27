from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import config

from app.repositories import drawings as repository
from app.security import verify_local_request
from app.services import drawings as service
from app.services import jobs
from app.services import previews

router = APIRouter()


@router.post("/upload-drawing", dependencies=[Depends(verify_local_request)])
def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    ocr_backend: str = Form("qwen"),
):
    if not files:
        raise HTTPException(400, "请选择至少一个文件")
    if len(files) > config.MAX_BATCH_UPLOADS:
        raise HTTPException(413, f"单次最多上传 {config.MAX_BATCH_UPLOADS} 个文件")
    if ocr_backend not in {"qwen", "mineru"}:
        raise HTTPException(400, "OCR 后端必须是 qwen 或 mineru")
    uploaded = [service.save_upload(file, ocr_backend) for file in files]
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": True, "files": uploaded})
    return RedirectResponse("/home", 303)


@router.post("/upload-drawing-async", dependencies=[Depends(verify_local_request)])
def upload_async(files: list[UploadFile] = File(...), ocr_backend: str = Form("qwen")):
    return {"success": True, "job": jobs.create(files, ocr_backend)}


@router.get("/upload-jobs/{job_id}")
def upload_job_status(job_id: str):
    return jobs.get(job_id)


@router.get("/drawings")
def list_drawings():
    rows = service.template_rows(repository.list_all("ASC"))
    drawings = [
        {
            "编号": row[0],
            "id": row[5],
            "文件名": row[1],
            "图纸名称": row[6],
            "文件类型": row[2],
            "文件大小(字节)": row[3],
            "上传时间": row[4],
        }
        for row in rows
    ]
    return {"图纸数量": len(drawings), "图纸信息": drawings}


@router.delete("/drawings/{drawing_id}", dependencies=[Depends(verify_local_request)])
def delete_drawing(drawing_id: int):
    service.delete(drawing_id)
    return {"success": True, "deleted_id": drawing_id}


@router.post("/delete-drawing/{drawing_id}", dependencies=[Depends(verify_local_request)])
def delete_drawing_from_page(drawing_id: int):
    service.delete(drawing_id)
    return RedirectResponse("/home", 303)


@router.post("/delete-all-drawings", dependencies=[Depends(verify_local_request)])
def delete_all_drawings():
    service.delete_all()
    return RedirectResponse("/home", 303)


@router.get("/drawings/{drawing_id}/preview")
def drawing_preview(drawing_id: int):
    path = previews.get_preview(drawing_id)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/ocr/{drawing_id}")
def ocr_text(drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    return {"文件名": drawing["filename"], "标题栏": drawing["title_text"] or "", "技术要求": drawing["tech_text"] or "", "全局OCR": drawing["all_text"] or ""}
