from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.repositories import drawings as repository
from app.repositories import kit_checks as kit_repository
from app.services.drawings import template_rows
from app.services.bom_extraction import drawing_title_name
from app.services.kit_rules import DRAWING_SUFFIXES

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))


@router.get("/")
def status():
    return {"系统名称": "工程数字图纸智能管理系统", "运行状态": "系统运行正常", "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@router.get("/home", response_class=HTMLResponse)
def home(request: Request):
    rows = template_rows(repository.list_all("DESC"))
    return templates.TemplateResponse(request, "index.html", {"图纸列表": rows, "drawings": rows})


@router.get("/kit-check", response_class=HTMLResponse)
def kit_check_page(request: Request):
    rows = repository.list_all("DESC")
    drawings = []
    for row in rows:
        if str(row.get("file_type") or "").lower() not in DRAWING_SUFFIXES:
            continue
        drawings.append({
            "id": row["id"],
            "filename": row["filename"],
            "original_filename": repository.display_filename(row),
            "file_type": row["file_type"],
            "file_size": row["file_size"],
            "batch_id": row.get("batch_id") or "",
            "upload_time": row["upload_time"],
            "title": drawing_title_name(row),
            "page_count": int(row.get("page_count") or 0),
            "pages_processed": int(row.get("pages_processed") or 0),
            "has_ocr": bool(str(row.get("all_text") or "").strip()),
        })
    recent_checks = []
    for check in kit_repository.list_recent(8):
        check = dict(check)
        check["status_label"] = {
            "queued": "排队中",
            "running": "检查中",
            "completed": "检查完成",
            "partial": "部分完成",
            "failed": "失败",
        }.get(check.get("status"), check.get("status") or "")
        check["mode_label"] = "全部装配图" if check.get("mode") == "all" else "手动总成图"
        recent_checks.append(check)
    default_batch_id = next(
        (drawing["batch_id"] for drawing in sorted(drawings, key=lambda item: item["id"], reverse=True) if drawing.get("batch_id")),
        "",
    )
    return templates.TemplateResponse(request, "kit_check.html", {
        "drawings": drawings,
        "recent_checks": recent_checks,
        "default_batch_id": default_batch_id,
    })


@router.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {})


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, keyword: str = "", page: int = 1, sort: str = "desc"):
    page = max(page, 1)
    rows = template_rows(repository.search(keyword, 10, (page - 1) * 10, sort))
    return templates.TemplateResponse(request, "index.html", {"图纸列表": rows, "drawings": rows, "search_keyword": keyword, "page": page, "sort": sort})


@router.get("/view-ocr/{drawing_id}", response_class=HTMLResponse)
def view_ocr(request: Request, drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    return templates.TemplateResponse(request, "ocr_view.html", {
        "drawing_id": drawing_id,
        "filename": str(drawing["filename"] or ""),
        "title_text": str(drawing["title_text"] or ""),
        "tech_text": str(drawing["tech_text"] or ""),
        "all_text": str(drawing["all_text"] or ""),
        "layout": str(drawing["layout"] or "unknown"),
    })


@router.get("/translate/{drawing_id}", response_class=HTMLResponse)
def translate_view(request: Request, drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    return templates.TemplateResponse(request, "translation_view.html", {
        "drawing_id": drawing_id,
        "filename": str(drawing["filename"] or ""),
        "source_lang": "zh",
        "target_lang": "en",
    })


@router.get("/export-ocr/{drawing_id}")
def export_ocr(drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    text = f"标题栏:\n{drawing['title_text'] or ''}\n\n技术要求:\n{drawing['tech_text'] or ''}\n\n全局OCR:\n{drawing['all_text'] or ''}"
    filename = quote(f"{drawing['filename']}.txt", safe="")
    return PlainTextResponse(text, headers={
        "Content-Disposition": f"attachment; filename=\"drawing-{drawing_id}.txt\"; filename*=UTF-8''{filename}",
    })
