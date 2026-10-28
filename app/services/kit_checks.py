"""Assembly-drawing kit completeness check orchestration.

The service does not trust a BOM extraction blindly.  It keeps extraction
problems visible, applies note exemptions per row, and only then performs the
deterministic filename match.  "All assemblies" and "manual total assembly"
share the same extraction/matching path; only the traversal differs.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

from fastapi import HTTPException

from app import config
from app.repositories import drawings as drawings_repository
from app.repositories import kit_checks as repository
from app.services import bom_extraction, segments
from app.services.kit_rules import (
    CHECK_SCOPE_NOTE,
    DRAWING_SUFFIXES,
    DrawingFile,
    classify_note,
    is_valid_code,
    match_files,
    status_label,
)

LOGGER = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solidcog-kit-check")

MODE_LABELS = {"all": "检查全部装配图", "manual": "手动指定总成图"}
CHECK_STATUS_LABELS = {
    "queued": "排队中",
    "running": "检查中",
    "completed": "检查完成",
    "partial": "部分完成",
    "failed": "检查失败",
}
DRAWING_STATUS_LABELS = {
    "pending": "等待处理",
    "processed": "已检查",
    "partial": "部分页未检查",
    "no_table": "未发现明细表",
    "failed": "识别失败",
}
ITEM_EXTRACTION_LABELS = {"ok": "正常", "review": "待复核", "failed": "提取失败"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _join_issue(*parts: object) -> str:
    return "；".join(str(part).strip() for part in parts if str(part or "").strip())


def _csv_safe(value: object) -> str:
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _csv_row(values: list[object]) -> list[str]:
    return [_csv_safe(value) for value in values]


def _drawing_file(row: dict) -> DrawingFile:
    return DrawingFile(
        drawing_id=int(row["id"]),
        original_filename=drawings_repository.display_filename(row),
        stored_filename=str(row.get("filename") or ""),
        file_type=str(row.get("file_type") or ""),
        title=segments.extract_title(str(row.get("title_text") or ""), str(row.get("all_text") or "")),
    )


def _drawing_title(row: dict) -> str:
    return bom_extraction.drawing_title_name(row)


def _public_match(match: dict) -> dict:
    stored = str(match.get("stored_filename") or "")
    return {
        "drawing_id": match.get("drawing_id"),
        "original_filename": match.get("original_filename") or match.get("filename") or "",
        "stored_filename": stored,
        "file_type": match.get("file_type") or "",
        "title": match.get("title") or "",
        "matched_filename": match.get("matched_filename") or match.get("original_filename") or "",
        "match_form": match.get("match_form") or "",
        "reason": match.get("reason") or "",
        "preview_url": f"/drawings/{match.get('drawing_id')}/preview" if match.get("drawing_id") else "",
        "file_url": f"/uploads/{quote(stored)}" if stored else "",
    }


def _build_item(row: dict, files: list[DrawingFile], *, original: dict | None = None) -> dict:
    code = str(row.get("code") or "").strip()
    name = str(row.get("name") or "").strip()
    note = str(row.get("note") or "").strip()
    extraction_status = str(row.get("extraction_status") or "ok")
    issue = str(row.get("extraction_issue") or "").strip()
    code_valid = is_valid_code(code)
    note_result = classify_note(note)
    if note_result.get("review"):
        extraction_status = "review"
        issue = _join_issue(issue, note_result.get("review_reason"))

    effective_exempt = bool(
        code_valid and note_result.get("exempt") and extraction_status == "ok"
    )
    if not code_valid:
        extraction_status = "review"
        issue = _join_issue(issue, "代号不可确认，未执行存在/不存在判定")
        match = {"status": "unchecked", "matches": [], "candidates": [], "ignored": [], "reason": "代号缺失或格式无法确认"}
    elif effective_exempt:
        match = {"status": "exempt", "matches": [], "candidates": [], "ignored": [], "reason": note_result.get("reason")}
    else:
        match = match_files(code, name, files)
        if match["status"] == "candidate":
            extraction_status = "review"
            issue = _join_issue(issue, "只找到同代号但名称不一致的文件，需人工复核")

    matched = [_public_match(item) for item in match.get("matches") or []]
    candidates = [_public_match(item) for item in match.get("candidates") or []]
    ignored = [_public_match(item) for item in match.get("ignored") or []]
    if ignored and not matched and not candidates and not note_result.get("exempt"):
        reasons = "；".join(sorted({str(item.get("reason") or "") for item in ignored if item.get("reason")}))
        if reasons:
            issue = _join_issue(issue, reasons)

    return {
        "page_number": int(row.get("page_number") or 0),
        "row_index": int(row.get("row_index") or 0),
        "sequence_no": str(row.get("sequence_no") or ""),
        "code": code,
        "name": name,
        "note": note,
        "quantity": str(row.get("quantity") or ""),
        "raw_text": str(row.get("raw_text") or ""),
        "position": row.get("position") or {},
        "extraction_status": extraction_status,
        "extraction_issue": issue,
        "exempt_reason": note_result.get("reason") if effective_exempt else None,
        "match_status": match.get("status") or "unchecked",
        "matches": matched,
        "candidates": candidates,
        "ignored": ignored,
        "edited": int(bool(row.get("edited"))),
        "original": original if original is not None else (row.get("original") or {}),
        "match_reason": match.get("reason") or "",
    }


def _record_drawing(
    check_id: int,
    drawing_id: int,
    *,
    discovery: str,
    depth: int,
    extraction: dict,
    parent_item_id: int | None = None,
) -> None:
    existing = repository.get_drawing(check_id, drawing_id)
    if not existing:
        repository.upsert_drawing(
            check_id,
            drawing_id,
            discovery=discovery,
            depth=depth,
            status="pending",
            started_at=_now(),
        )
    extraction_status = str(extraction.get("status") or "failed")
    status = "processed" if extraction_status == "ok" else extraction_status
    issues = list(extraction.get("issues") or [])
    if extraction_status == "ok" and not extraction.get("rows"):
        # A processed drawing without any row is not a valid completed group.
        # Treat it as an extraction failure instead of silently omitting it.
        status = "failed"
        issues.append("提取标记成功但没有明细表行，未形成检查结果")
    repository.update_drawing_fields(
        check_id,
        drawing_id,
        status=status,
        page_count=int(extraction.get("pages_total") or 0),
        pages_processed=int(extraction.get("pages_processed") or 0),
        table_code=str(extraction.get("table_code") or ""),
        table_name=str(extraction.get("table_name") or ""),
        issues_json=json.dumps(issues, ensure_ascii=False),
        finished_at=_now(),
    )
    if parent_item_id:
        repository.add_parent_reference(check_id, drawing_id, parent_item_id)


def _create_items(check_id: int, drawing_id: int, rows: list[dict], files: list[DrawingFile]) -> list[tuple[int, dict]]:
    repository.delete_items_for_drawing(check_id, drawing_id)
    result: list[tuple[int, dict]] = []
    for row in rows:
        item = _build_item(row, files)
        item_id = repository.add_item(check_id, drawing_id, item)
        result.append((item_id, item))
    return result


def _validate_check_input(drawing_ids: list[int], mode: str, root_ids: list[int]) -> None:
    if mode not in MODE_LABELS:
        raise HTTPException(400, "检查模式必须是 all 或 manual")
    if not drawing_ids:
        raise HTTPException(400, "请选择本次要检查的图纸集合")
    if len(drawing_ids) > config.KIT_CHECK_MAX_DRAWINGS:
        raise HTTPException(413, f"单次最多检查 {config.KIT_CHECK_MAX_DRAWINGS} 张图纸")
    if mode == "manual" and not root_ids:
        raise HTTPException(400, "手动指定总成图模式至少需要选择一张总成图")
    if any(root_id not in drawing_ids for root_id in root_ids):
        raise HTTPException(400, "总成图必须来自本次检查集合")


def create_check(drawing_ids: list[int], mode: str = "all", root_ids: list[int] | None = None, start: bool = True) -> dict:
    unique_ids: list[int] = []
    for value in drawing_ids or []:
        try:
            drawing_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, "图纸集合包含无效编号")
        if drawing_id not in unique_ids:
            unique_ids.append(drawing_id)
    roots: list[int] = []
    for value in root_ids or []:
        try:
            drawing_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, "总成图包含无效编号")
        if drawing_id not in roots:
            roots.append(drawing_id)
    _validate_check_input(unique_ids, mode, roots)
    selected_rows = {drawing_id: drawings_repository.get(drawing_id) for drawing_id in unique_ids}
    missing = [drawing_id for drawing_id, row in selected_rows.items() if not row]
    if missing:
        raise HTTPException(400, f"本次检查集合中有图纸已被删除：{', '.join(map(str, missing))}")
    unsupported = [
        drawing_id for drawing_id, row in selected_rows.items()
        if row and str(row.get("file_type") or "").lower() not in DRAWING_SUFFIXES
    ]
    if unsupported:
        raise HTTPException(400, "以下文件不是可检查的工程图（PDF/PNG/JPG/TIFF/BMP/WebP）：" + ", ".join(map(str, unsupported)))
    check_id = repository.create(mode, unique_ids, roots)
    if start:
        submit(check_id)
    return get_check_summary(check_id)


def submit(check_id: int, force_drawing_ids: list[int] | None = None) -> None:
    _executor.submit(_run_safe, check_id, force_drawing_ids or [])


def _run_safe(check_id: int, force_drawing_ids: list[int]) -> None:
    try:
        run_check(check_id, force_drawing_ids=force_drawing_ids)
    except Exception as exc:
        LOGGER.exception("Kit check %s failed", check_id)
        repository.update(
            check_id,
            status="failed",
            current_step="检查失败",
            error=str(exc),
            finished_at=_now(),
        )


def run_check(check_id: int, force_drawing_ids: list[int] | None = None) -> None:
    check = repository.get(check_id)
    if not check:
        return
    drawing_ids = [int(item) for item in check.get("drawing_ids") or []]
    roots = [int(item) for item in check.get("root_drawing_ids") or []]
    force_ids = {int(item) for item in (force_drawing_ids or [])}
    selected_rows = {drawing_id: drawings_repository.get(drawing_id) for drawing_id in drawing_ids}
    files = [_drawing_file(row) for row in selected_rows.values() if row]
    repository.clear_results(check_id)
    repository.update(
        check_id,
        status="running",
        total_drawings=len(drawing_ids),
        processed_drawings=0,
        current_step="正在检查图纸",
        error=None,
        finished_at=None,
    )

    extractions: dict[int, dict] = {}
    visited: set[int] = set()
    processing_stack: list[int] = []
    fatal: list[str] = []
    processed = 0

    def extraction_for(drawing_id: int) -> dict:
        if drawing_id not in extractions:
            row = selected_rows.get(drawing_id)
            if not row:
                extractions[drawing_id] = {
                    "status": "failed",
                    "rows": [],
                    "pages_total": 0,
                    "pages_processed": 0,
                    "issues": ["图纸不在本次导入集合中，已跳过"],
                    "table_code": "",
                    "table_name": "",
                }
            elif str(row.get("file_type") or "").lower() not in DRAWING_SUFFIXES:
                extractions[drawing_id] = {
                    "status": "no_table",
                    "rows": [],
                    "pages_total": 0,
                    "pages_processed": 0,
                    "issues": ["非工程图文件，不参与本次检查"],
                    "table_code": "",
                    "table_name": "",
                }
            else:
                extractions[drawing_id] = bom_extraction.extract_drawing(
                    row, force=drawing_id in force_ids
                )
        return extractions[drawing_id]

    def record(
        drawing_id: int,
        *,
        discovery: str,
        depth: int,
        parent_item_id: int | None = None,
    ) -> dict:
        nonlocal processed
        extraction = extraction_for(drawing_id)
        _record_drawing(
            check_id,
            drawing_id,
            discovery=discovery,
            depth=depth,
            extraction=extraction,
            parent_item_id=parent_item_id,
        )
        processed += 1
        repository.update(
            check_id,
            processed_drawings=min(processed, len(drawing_ids)),
            current_step=f"已检查 {processed} 张图纸",
        )
        return extraction

    def process_manual_children(drawing_id: int, items: list[tuple[int, dict]], depth: int) -> None:
        if depth >= config.KIT_CHECK_MAX_DEPTH:
            for item_id, _ in items:
                repository.update_item(
                    item_id,
                    extraction_status="review",
                    extraction_issue=_join_issue(
                        repository.get_item(item_id).get("extraction_issue") if repository.get_item(item_id) else "",
                        "达到子装配展开深度上限，未继续展开",
                    ),
                )
            return
        processing_stack.append(drawing_id)
        try:
            for item_id, item in items:
                if item.get("exempt_reason") or item.get("match_status") != "exists":
                    continue
                for match in item.get("matches") or []:
                    child_id = int(match.get("drawing_id") or 0)
                    if not child_id or child_id not in selected_rows:
                        continue
                    if child_id == drawing_id:
                        repository.update_item(
                            item_id,
                            extraction_status="review",
                            extraction_issue=_join_issue(
                                item.get("extraction_issue"), "循环引用：明细表指向图纸自身，未继续展开"
                            ),
                        )
                        continue
                    child_row = selected_rows.get(child_id)
                    if not child_row:
                        continue
                    # A matched drawing may be a subassembly even when its
                    # existing OCR text has no obvious BOM-header hint. Probe
                    # every matched child (the extraction cache makes repeated
                    # references cheap); otherwise a real subassembly could be
                    # skipped and the parent falsely reported as complete.
                    child_extraction = extraction_for(child_id)
                    if child_id in processing_stack:
                        repository.update_item(
                            item_id,
                            extraction_status="review",
                            extraction_issue=_join_issue(
                                item.get("extraction_issue"), "循环引用：该子装配已在上层展开，未重复提取"
                            ),
                        )
                        repository.add_parent_reference(check_id, child_id, item_id)
                        continue
                    if child_extraction.get("status") in {"ok", "partial"} and child_extraction.get("rows"):
                        if child_id in visited:
                            repository.add_parent_reference(check_id, child_id, item_id)
                        else:
                            child_items = process_all_mode_drawing(child_id, discovery="reference", depth=depth + 1)
                            repository.add_parent_reference(check_id, child_id, item_id)
                            if child_items:
                                process_manual_children(child_id, child_items, depth + 1)
                    elif child_extraction.get("status") in {"failed", "partial"}:
                        if child_id not in visited:
                            if child_extraction.get("rows"):
                                # Preserve the failed table's problem rows as a
                                # visible group, but never recurse into them.
                                process_all_mode_drawing(child_id, discovery="reference", depth=depth + 1)
                                repository.add_parent_reference(check_id, child_id, item_id)
                            else:
                                visited.add(child_id)
                                _record_drawing(
                                    check_id,
                                    child_id,
                                    discovery="reference",
                                    depth=depth + 1,
                                    extraction=child_extraction,
                                    parent_item_id=item_id,
                                )
                        repository.update_item(
                            item_id,
                            extraction_status="review",
                            extraction_issue=_join_issue(
                                item.get("extraction_issue"),
                                "匹配文件可能为子装配，但明细表识别失败，未继续展开",
                            ),
                        )
        finally:
            processing_stack.pop()

    def process_all_mode_drawing(drawing_id: int, *, discovery: str, depth: int) -> list[tuple[int, dict]]:
        if drawing_id in visited:
            return []
        visited.add(drawing_id)
        extraction = record(drawing_id, discovery=discovery, depth=depth)
        rows = extraction.get("rows") or []
        if not rows:
            return []
        # Problem rows are kept even when the table is marked failed: a table
        # with unreadable codes must remain visible for review instead of being
        # silently discarded.
        items = _create_items(check_id, drawing_id, rows, files)
        return items

    mode = str(check.get("mode") or "all")
    if mode == "manual":
        for root_id in roots:
            if root_id not in selected_rows:
                fatal.append(f"总成图 {root_id} 不在本次检查集合中")
                continue
            if root_id in visited:
                continue
            root_extraction = extraction_for(root_id)
            visited.add(root_id)
            _record_drawing(check_id, root_id, discovery="root", depth=0, extraction=root_extraction)
            processed += 1
            repository.update(check_id, processed_drawings=min(processed, len(drawing_ids)), current_step="正在展开总成图")
            root_rows = root_extraction.get("rows") or []
            if root_rows:
                items = _create_items(check_id, root_id, root_rows, files)
                if root_extraction.get("status") in {"ok", "partial"}:
                    process_manual_children(root_id, items, 0)
            if root_extraction.get("status") == "failed":
                fatal.append("总成图明细表识别失败，未形成完整的子项清单")
            elif root_extraction.get("status") == "no_table":
                fatal.append("总成图未识别到有效明细表")
    else:
        for drawing_id in drawing_ids:
            process_all_mode_drawing(drawing_id, discovery="scan", depth=0)
            if not selected_rows.get(drawing_id):
                fatal.append(f"图纸 {drawing_id} 已被删除，无法检查")

    # Determine whether the run itself completed. Item-level review is kept
    # separate so the UI can still show exists/missing results.
    check_drawings = repository.list_drawings(check_id)
    group_drawings = [
        row for row in check_drawings
        if row.get("status") in {"processed", "partial", "failed"} and repository.list_items(check_id, int(row["drawing_id"]))
    ]
    bad_drawings = [row for row in check_drawings if row.get("status") in {"failed", "partial"}]
    if mode == "manual":
        for root_id in roots:
            root_state = repository.get_drawing(check_id, root_id)
            if not root_state or root_state.get("status") != "processed":
                bad_drawings.append(root_state or {"drawing_id": root_id, "status": "missing"})
    if fatal and not group_drawings:
        status = "failed"
    elif bad_drawings or fatal:
        status = "partial"
    else:
        status = "completed"
    error = "；".join(dict.fromkeys(fatal))
    repository.update(
        check_id,
        status=status,
        processed_drawings=len(visited),
        current_step="检查完成" if status == "completed" else "检查结束，存在未完成项",
        error=error or None,
        finished_at=_now(),
    )


def _public_item(item: dict) -> dict:
    result = dict(item)
    result["status_label"] = status_label(str(item.get("match_status") or ""))
    result["extraction_status_label"] = ITEM_EXTRACTION_LABELS.get(
        str(item.get("extraction_status") or ""), str(item.get("extraction_status") or "")
    )
    result["review"] = bool(
        item.get("exempt_reason") is None
        and (item.get("extraction_status") != "ok" or item.get("match_status") in {"candidate", "unchecked"})
    )
    result["matches"] = [_public_match(match) for match in item.get("matches") or []]
    result["candidates"] = [_public_match(match) for match in item.get("candidates") or []]
    result["ignored"] = [_public_match(match) for match in item.get("ignored") or []]
    result.pop("matched_json", None)
    result.pop("candidates_json", None)
    result.pop("ignored_json", None)
    return result


def _summary(
    items: list[dict],
    groups: list[dict],
    check_drawings: list[dict],
    check: dict,
    missing_selected: int = 0,
    missing_files: int = 0,
    unprocessed_selected: int = 0,
) -> dict:
    non_exempt = [item for item in items if not item.get("exempt_reason")]
    exists = sum(1 for item in non_exempt if item.get("match_status") == "exists")
    missing = sum(1 for item in non_exempt if item.get("match_status") == "missing")
    candidates = sum(1 for item in non_exempt if item.get("match_status") == "candidate")
    unchecked = sum(1 for item in non_exempt if item.get("match_status") == "unchecked")
    exempt = sum(1 for item in items if item.get("exempt_reason"))
    review_items = sum(
        1 for item in non_exempt
        if item.get("extraction_status") != "ok" or item.get("match_status") in {"candidate", "unchecked"}
    )
    group_ids = {int(group.get("drawing_id") or 0) for group in groups}
    empty_processed = [
        row for row in check_drawings
        if row.get("status") == "processed" and int(row["drawing_id"]) not in group_ids
    ]
    issue_drawings = [
        row for row in check_drawings
        if row.get("status") in {"failed", "partial"}
    ] + empty_processed
    no_table = [row for row in check_drawings if row.get("status") == "no_table"]
    complete = (
        str(check.get("status")) == "completed"
        and missing_selected == 0
        and missing_files == 0
        and unprocessed_selected == 0
        and not empty_processed
    )
    all_present = bool(
        complete
        and groups
        and missing == 0
        and candidates == 0
        and unchecked == 0
        and review_items == 0
        and not issue_drawings
    )
    return {
        "selected_files": len(check.get("drawing_ids") or []),
        "assemblies": len(groups),
        "exists": exists,
        "missing": missing,
        "candidates": candidates,
        "unchecked": unchecked,
        "exempt": exempt,
        "review_items": review_items,
        "issue_drawings": len(issue_drawings),
        "no_table_drawings": len(no_table),
        "review_total": review_items + len(issue_drawings) + missing_selected + missing_files + unprocessed_selected,
        "missing_selected_files": missing_selected,
        "missing_files": missing_files,
        "unprocessed_selected_drawings": unprocessed_selected,
        "empty_processed_drawings": len(empty_processed),
        "complete": complete,
        "all_present": all_present,
    }


def get_check_summary(check_id: int) -> dict:
    check = repository.get(check_id)
    if not check:
        raise HTTPException(404, "检查任务不存在")
    return {
        "id": check["id"],
        "mode": check["mode"],
        "mode_label": MODE_LABELS.get(str(check["mode"]), str(check["mode"])),
        "status": check["status"],
        "status_label": CHECK_STATUS_LABELS.get(str(check["status"]), str(check["status"])),
        "total_drawings": check.get("total_drawings") or 0,
        "processed_drawings": check.get("processed_drawings") or 0,
        "current_step": check.get("current_step") or "",
        "error": check.get("error") or "",
        "created_at": check.get("created_at") or "",
        "updated_at": check.get("updated_at") or "",
        "finished_at": check.get("finished_at") or "",
    }


def build_payload(check_id: int) -> dict:
    check = repository.get(check_id)
    if not check:
        raise HTTPException(404, "检查任务不存在")
    check_drawings = repository.list_drawings(check_id)
    all_items = repository.list_items(check_id)
    drawing_cache: dict[int, dict | None] = {}
    missing_selected: list[int] = []
    missing_files: list[dict] = []
    relevant_ids = [int(item) for item in check.get("drawing_ids") or []]
    if str(check.get("mode")) == "manual":
        relevant_set = {int(item) for item in check.get("root_drawing_ids") or []}
        relevant_set.update(int(row["drawing_id"]) for row in check_drawings)
        relevant_ids = [drawing_id for drawing_id in relevant_ids if drawing_id in relevant_set]
    for drawing_id in relevant_ids:
        drawing = drawings_repository.get(drawing_id)
        if not drawing:
            missing_selected.append(drawing_id)
            drawing_cache[drawing_id] = None
        elif not (config.UPLOAD_DIR / str(drawing.get("filename") or "")).exists():
            missing_files.append(drawing)
            drawing_cache[drawing_id] = None
        else:
            drawing_cache[drawing_id] = drawing
    check_drawing_ids = {int(row["drawing_id"]) for row in check_drawings}
    unprocessed_selected = [
        drawing_id for drawing_id in (check.get("drawing_ids") or [])
        if int(drawing_id) not in check_drawing_ids
    ] if str(check.get("mode")) == "all" and str(check.get("status")) not in {"queued", "running"} else []
    visible_items = [
        item for item in all_items
        if drawing_cache.get(int(item["drawing_id"]))
    ]
    items_by_drawing: dict[int, list[dict]] = {}
    for item in visible_items:
        items_by_drawing.setdefault(int(item["drawing_id"]), []).append(item)
    groups = []
    for row in check_drawings:
        drawing_id = int(row["drawing_id"])
        drawing = drawing_cache.get(drawing_id)
        if not drawing:
            continue
        items = items_by_drawing.get(drawing_id, [])
        if row.get("status") not in {"processed", "partial", "failed"} or not items:
            continue
        public_items = [_public_item(item) for item in items]
        main_items = [item for item in public_items if not item.get("exempt_reason")]
        exempt_items = [item for item in public_items if item.get("exempt_reason")]
        references = []
        for parent_id in row.get("parent_item_ids") or []:
            parent_item = repository.get_item(int(parent_id))
            if not parent_item:
                continue
            source_drawing = drawings_repository.get(int(parent_item["drawing_id"]))
            references.append({
                "item_id": parent_item["id"],
                "source_drawing_id": parent_item["drawing_id"],
                "source_drawing": drawings_repository.display_filename(source_drawing) if source_drawing else "",
                "source_title": _drawing_title(source_drawing) if source_drawing else "",
                "code": parent_item.get("code") or "",
                "name": parent_item.get("name") or "",
                "note": parent_item.get("note") or "",
            })
        groups.append({
            "drawing_id": drawing_id,
            "original_filename": drawings_repository.display_filename(drawing),
            "stored_filename": drawing.get("filename") or "",
            "file_type": drawing.get("file_type") or "",
            "title": row.get("table_name") or _drawing_title(drawing) or drawings_repository.display_filename(drawing),
            "code": row.get("table_code") or "",
            "status": row.get("status") or "pending",
            "status_label": DRAWING_STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
            "pages": {
                "total": int(row.get("page_count") or 0),
                "processed": int(row.get("pages_processed") or 0),
                "unprocessed": max(0, int(row.get("page_count") or 0) - int(row.get("pages_processed") or 0)),
            },
            "issues": row.get("issues") or [],
            "discovery": row.get("discovery") or "scan",
            "depth": int(row.get("depth") or 0),
            "is_root": drawing_id in (check.get("root_drawing_ids") or []),
            "references": references,
            "preview_url": f"/drawings/{drawing_id}/preview",
            "file_url": f"/uploads/{quote(str(drawing.get('filename') or ''))}",
            "items": main_items,
            "exempt_items": exempt_items,
            "counts": {
                "exists": sum(1 for item in main_items if item.get("match_status") == "exists"),
                "missing": sum(1 for item in main_items if item.get("match_status") == "missing"),
                "candidate": sum(1 for item in main_items if item.get("match_status") == "candidate"),
                "unchecked": sum(1 for item in main_items if item.get("match_status") == "unchecked"),
                "exempt": len(exempt_items),
                "review": sum(1 for item in main_items if item.get("review")),
            },
        })

    summary = _summary(
        visible_items,
        groups,
        check_drawings,
        check,
        missing_selected=len(missing_selected),
        missing_files=len(missing_files),
        unprocessed_selected=len(unprocessed_selected),
    )
    drawing_issues = []
    issues_by_id: dict[int, dict] = {}

    def add_drawing_issue(issue: dict) -> None:
        drawing_id = int(issue["drawing_id"])
        existing = issues_by_id.get(drawing_id)
        if existing:
            existing["issues"] = list(dict.fromkeys([*(existing.get("issues") or []), *(issue.get("issues") or [])]))
            return
        issues_by_id[drawing_id] = issue
        drawing_issues.append(issue)

    for row in check_drawings:
        if row.get("status") in {"failed", "partial"} or (
            row.get("status") == "no_table" and int(row["drawing_id"]) in (check.get("root_drawing_ids") or [])
        ) or (
            row.get("status") == "processed" and not items_by_drawing.get(int(row["drawing_id"]))
        ):
            drawing = drawing_cache.get(int(row["drawing_id"]))
            if not drawing:
                continue
            add_drawing_issue({
                "drawing_id": row["drawing_id"],
                "original_filename": drawings_repository.display_filename(drawing),
                "title": row.get("table_name") or _drawing_title(drawing),
                "status": row.get("status"),
                "status_label": DRAWING_STATUS_LABELS.get(str(row.get("status")), str(row.get("status"))),
                "issues": row.get("issues") or [],
                "pages_total": int(row.get("page_count") or 0),
                "pages_processed": int(row.get("pages_processed") or 0),
                "is_root": int(row["drawing_id"]) in (check.get("root_drawing_ids") or []),
            })
    for drawing in missing_files:
        add_drawing_issue({
            "drawing_id": int(drawing["id"]),
            "original_filename": drawings_repository.display_filename(drawing),
            "title": _drawing_title(drawing),
            "status": "file_missing",
            "status_label": "原始图纸文件缺失",
            "issues": ["图纸库记录存在，但原始文件已不在上传目录，不能沿用旧结果宣称齐全"],
            "pages_total": int(drawing.get("page_count") or 0),
            "pages_processed": int(drawing.get("pages_processed") or 0),
            "is_root": int(drawing["id"]) in (check.get("root_drawing_ids") or []),
        })
    for drawing_id in unprocessed_selected:
        add_drawing_issue({
            "drawing_id": int(drawing_id),
            "original_filename": "",
            "title": f"图纸 #{drawing_id}",
            "status": "unprocessed",
            "status_label": "本次检查未处理",
            "issues": ["该图纸属于本次检查集合，但没有生成检查结果，不能宣称齐全"],
            "pages_total": 0,
            "pages_processed": 0,
            "is_root": int(drawing_id) in (check.get("root_drawing_ids") or []),
        })
    for drawing_id in missing_selected:
        add_drawing_issue({
            "drawing_id": drawing_id,
            "original_filename": "",
            "title": f"图纸 #{drawing_id}",
            "status": "missing",
            "status_label": "检查集合中的图纸已被删除",
            "issues": ["检查集合中的图纸已不存在，不能沿用原结果宣称齐全"],
            "pages_total": 0,
            "pages_processed": 0,
            "is_root": drawing_id in (check.get("root_drawing_ids") or []),
        })
    return {
        **get_check_summary(check_id),
        "root_drawing_ids": check.get("root_drawing_ids") or [],
        "drawing_ids": check.get("drawing_ids") or [],
        "summary": summary,
        "groups": groups,
        "drawing_issues": drawing_issues,
        "scope_note": CHECK_SCOPE_NOTE,
        "can_retry": bool(
            str(check.get("status")) in {"completed", "partial", "failed"}
            and any(row.get("status") in {"failed", "partial"} for row in check_drawings)
            and not any(bool(item.get("edited")) for item in all_items)
        ),
        "retry_blocked_reason": (
            ""
            if any(row.get("status") in {"failed", "partial"} for row in check_drawings)
            and not any(bool(item.get("edited")) for item in all_items)
            else ("存在人工修正，重试会覆盖修正" if any(bool(item.get("edited")) for item in all_items)
                  else "没有失败或部分页图纸需要重试")
        ),
    }


def retry_check(check_id: int) -> dict:
    check = repository.get(check_id)
    if not check:
        raise HTTPException(404, "检查任务不存在")
    if check.get("status") in {"queued", "running"}:
        raise HTTPException(409, "检查仍在进行中")
    failed_ids = [
        int(row["drawing_id"])
        for row in repository.list_drawings(check_id)
        if row.get("status") in {"failed", "partial"}
    ]
    if not failed_ids:
        raise HTTPException(409, "没有失败或部分页的图纸，无需重试")
    if any(bool(item.get("edited")) for item in repository.list_items(check_id)):
        raise HTTPException(409, "检查中存在人工修正，重试会覆盖修正；请先导出 CSV 或新建检查")
    repository.update(check_id, status="queued", current_step="等待重试", error=None, finished_at=None)
    submit(check_id, force_drawing_ids=failed_ids)
    return get_check_summary(check_id)


def update_item(item_id: int, fields: dict) -> dict:
    item = repository.get_item(item_id)
    if not item:
        raise HTTPException(404, "检查条目不存在")
    check = repository.get(int(item["check_id"]))
    if not check:
        raise HTTPException(404, "检查任务不存在")
    if check.get("status") in {"queued", "running"}:
        raise HTTPException(409, "检查仍在进行中，暂不能修改条目")
    code = str(fields.get("code", item.get("code") or "")).strip()
    name = str(fields.get("name", item.get("name") or "")).strip()
    note = str(fields.get("note", item.get("note") or "")).strip()
    quantity = str(fields.get("quantity", item.get("quantity") or "")).strip()
    if not is_valid_code(code):
        raise HTTPException(400, "代号不能为空，且应同时包含字母和数字")
    original = item.get("original") or {
        "code": item.get("code"),
        "name": item.get("name"),
        "note": item.get("note"),
        "quantity": item.get("quantity"),
        "extraction_status": item.get("extraction_status"),
        "extraction_issue": item.get("extraction_issue"),
    }
    row = {
        "code": code,
        "name": name,
        "note": note,
        "quantity": quantity,
        "page_number": item.get("page_number") or 0,
        "row_index": item.get("row_index") or 0,
        "sequence_no": item.get("sequence_no") or "",
        "raw_text": item.get("raw_text") or "",
        "position": item.get("position") or {},
        "extraction_status": "ok",
        "extraction_issue": "人工修正后重新匹配",
        "edited": 1,
        "original": original,
    }
    files = [
        _drawing_file(row)
        for row in (drawings_repository.get(int(drawing_id)) for drawing_id in check.get("drawing_ids") or [])
        if row
    ]
    rebuilt = _build_item(row, files, original=original)
    rebuilt["extraction_issue"] = "人工修正后重新匹配" if not rebuilt.get("extraction_issue") else rebuilt["extraction_issue"]
    repository.update_item(
        item_id,
        sequence_no=rebuilt["sequence_no"],
        code=rebuilt["code"],
        name=rebuilt["name"],
        note=rebuilt["note"],
        quantity=rebuilt["quantity"],
        extraction_status=rebuilt["extraction_status"],
        extraction_issue=rebuilt["extraction_issue"],
        exempt_reason=rebuilt["exempt_reason"],
        match_status=rebuilt["match_status"],
        matched_json=json.dumps(rebuilt["matches"], ensure_ascii=False),
        candidates_json=json.dumps(rebuilt["candidates"], ensure_ascii=False),
        ignored_json=json.dumps(rebuilt["ignored"], ensure_ascii=False),
        edited=1,
        original_json=json.dumps(original, ensure_ascii=False),
    )
    return build_payload(int(item["check_id"]))


def export_csv(check_id: int) -> str:
    payload = build_payload(check_id)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(_csv_row([
        "检查模式", "检查集合文件数", "所属装配图", "装配图文件", "装配图代号",
        "页码", "序号", "代号", "名称", "备注", "数量", "匹配文件",
        "检查结果", "提取状态", "问题说明", "引用来源",
    ]))
    summary = payload["summary"]
    writer.writerow(_csv_row([
        payload.get("mode_label"), summary.get("selected_files"), "【检查汇总】",
        "", "", "", "", "", "", "", "",
        f"存在 {summary.get('exists', 0)}；不存在 {summary.get('missing', 0)}；免查 {summary.get('exempt', 0)}；待复核 {summary.get('review_total', 0)}",
        "整套齐全" if summary.get("all_present") else "未完成/需处理",
        "", f"检查状态：{payload.get('status_label', '')}",
        "",
    ]))
    for group in payload["groups"]:
        reference_text = "；".join(
            f"{reference.get('source_drawing')} / {reference.get('code')} {reference.get('name')}"
            for reference in group.get("references") or []
        )
        for item in [*group.get("items", []), *group.get("exempt_items", [])]:
            matches = "；".join(
                str(match.get("original_filename") or "") for match in item.get("matches") or []
            )
            candidates = "；".join(
                str(candidate.get("original_filename") or "") for candidate in item.get("candidates") or []
            )
            issue_parts = [item.get("extraction_issue") or "", item.get("match_reason") or ""]
            if candidates:
                issue_parts.append(f"候选文件（名称不一致）：{candidates}")
            writer.writerow(_csv_row([
                payload.get("mode_label"),
                payload["summary"].get("selected_files"),
                group.get("title"),
                group.get("original_filename"),
                group.get("code"),
                item.get("page_number") or "",
                item.get("sequence_no") or "",
                item.get("code") or "",
                item.get("name") or "",
                item.get("note") or "",
                item.get("quantity") or "",
                matches,
                item.get("status_label") or "",
                item.get("extraction_status_label") or "",
                "；".join(part for part in issue_parts if part),
                reference_text,
            ]))
    for issue in payload.get("drawing_issues") or []:
        writer.writerow(_csv_row([
            payload.get("mode_label"),
            payload["summary"].get("selected_files"),
            issue.get("title") or "图纸问题",
            issue.get("original_filename") or "",
            "",
            "",
            "",
            "",
            "【图纸问题】",
            "",
            "",
            "",
            issue.get("status_label") or "",
            "",
            "；".join(str(part) for part in issue.get("issues") or [] if part),
            "",
        ]))
    return "\ufeff" + output.getvalue()
