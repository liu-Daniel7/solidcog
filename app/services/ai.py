import re

from fastapi import HTTPException

from app import config
from app.repositories import drawings as drawing_repository
from app.services import mechvl


def sanitize_answer(answer: str) -> str:
    text = re.sub(r"(?m)```+|^\s*[-*_]{3,}\s*$", "", str(answer or ""))
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*|^\s*>\s*|^\s*[-+*]\s+", "", raw_line.strip())
        lines.append(line.translate(str.maketrans("", "", "*#`|_")).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def chat_with_drawing(prompt: str, drawing_id: int) -> dict:
    drawing = drawing_repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到该图纸")
    path = config.UPLOAD_DIR / drawing["filename"]
    if not path.exists():
        raise HTTPException(404, "图纸文件不存在")
    ocr_context = (
        f"标题栏：\n{drawing['title_text'] or ''}\n\n"
        f"技术要求：\n{drawing['tech_text'] or ''}\n\n"
        f"全局 OCR：\n{drawing['all_text'] or ''}"
    )
    answer = mechvl.analyze(path, prompt, ocr_context)
    return {
        "success": True,
        "answer": sanitize_answer(answer),
        "drawing_id": drawing_id,
        "filename": drawing["filename"],
        "model": "MechVL-4B-RL",
    }


def run_tool(tool_call: str, parameters: dict) -> dict:
    if tool_call == "query_database":
        query_type = parameters.get("query_type", "list")
        if query_type == "list":
            rows = drawing_repository.list_page(parameters.get("limit", 10), parameters.get("offset", 0))
            return {"success": True, "data": {"drawings": rows, "total": len(rows)}}
        if query_type == "detail":
            row = drawing_repository.get(parameters.get("drawing_id"))
            return {"success": True, "data": row} if row else {"success": False, "error": "未找到图纸"}
        if query_type == "count":
            return {"success": True, "data": {"count": drawing_repository.count()}}
        return {"success": False, "error": f"未知查询类型: {query_type}"}

    if tool_call == "search_drawings":
        keyword = parameters.get("keyword", "")
        if not keyword:
            return {"success": False, "error": "缺少keyword参数"}
        rows = drawing_repository.search(keyword, parameters.get("limit", 10))
        return {"success": True, "data": {"results": rows, "total": len(rows)}}

    return {"success": False, "error": f"未知工具: {tool_call}"}
