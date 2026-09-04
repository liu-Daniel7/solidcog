from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.repositories import drawings as repository
from app.services.drawings import template_rows

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))


@router.get("/")
def status():
    return {"系统名称": "工程数字图纸智能管理系统", "运行状态": "系统运行正常", "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@router.get("/home", response_class=HTMLResponse)
def home(request: Request):
    rows = template_rows(repository.list_all("DESC"))
    return templates.TemplateResponse(request, "index.html", {"图纸列表": rows, "drawings": rows})


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
        "filename": str(drawing["filename"] or ""),
        "title_text": str(drawing["title_text"] or "")[:200000],
        "tech_text": str(drawing["tech_text"] or "")[:200000],
        "all_text": str(drawing["all_text"] or "")[:200000],
        "layout": str(drawing["layout"] or "unknown"),
    })


@router.get("/export-ocr/{drawing_id}")
def export_ocr(drawing_id: int):
    drawing = repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    text = f"标题栏:\n{drawing['title_text'] or ''}\n\n技术要求:\n{drawing['tech_text'] or ''}\n\n全局OCR:\n{drawing['all_text'] or ''}"
    return PlainTextResponse(text, headers={"Content-Disposition": f"attachment; filename={drawing['filename']}.txt"})
