"""Assembly drawing BOM (明细表) extraction with a durable per-drawing cache.

The extraction layer only produces *candidates*.  Every row keeps its page,
raw text and issue information, and the deterministic kit-check service decides
whether a row is exempt or whether a file exists.  A failed/partial extraction
is never converted into an empty, complete table.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pypdfium2 as pdfium

from app import config
from app.repositories import drawings as drawings_repository
from app.repositories import kit_checks as cache_repository
from app.services import coordinate_scan, qwen, segments
from app.services.images import load_pages, page_count
from app.services.kit_rules import (
    DRAWING_SUFFIXES,
    MODEL_SUFFIXES,
    NATIVE_DRAWING_SUFFIXES,
    clean_text,
    is_valid_code,
    normalize_code,
    split_filename,
    strip_code_boundary,
)

LOGGER = logging.getLogger(__name__)

BOM_EXTRACTION_VERSION = "2026-10-28.2"
_HEADER_WORDS = {"序号", "代号", "名称", "备注", "数量", "图号", "零件号", "件号", "明细", "BOM"}
_CODE_LABEL = re.compile(r"(?:图号|代号|图纸编号|零件号|件号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,39})")
_CODE_TOKEN = re.compile(r"(?<![A-Za-z0-9._/-])([A-Za-z0-9][A-Za-z0-9._/-]{2,39})(?![A-Za-z0-9._/-])")
_PAGE_MARKER = re.compile(r"^第\s*(\d+)\s*页(?:\s*/?\s*共\s*\d+\s*页)?$", re.I)
_HEADER_LINE = re.compile(r"^\s*(?:序号|序号\s*代号|代号|名称|备注|数量|图号)(?:\s+|$)")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _display_filename(drawing: dict) -> str:
    original = str(drawing.get("original_filename") or "").strip()
    return original or drawings_repository.display_filename(drawing)


def _file_path(drawing: dict) -> Path:
    return config.UPLOAD_DIR / str(drawing.get("filename") or "")


def drawing_signature(drawing: dict) -> str:
    """Hash file content plus extraction configuration.

    If the drawing is re-uploaded/replaced, or the model/page-limit changes,
    the cache is invalidated.  A changed *selected file set* does not invalidate
    extraction; matching is recomputed from the persisted rows for every check.
    """
    digest = hashlib.sha256()
    path = _file_path(drawing)
    try:
        if path.exists():
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            digest.update(str(drawing.get("filename") or "").encode("utf-8"))
    except OSError:
        digest.update(str(drawing.get("filename") or "").encode("utf-8"))
    for part in (
        BOM_EXTRACTION_VERSION,
        config.QWEN_VL_MODEL,
        str(config.QWEN_OCR_MAX_PAGES),
        str(drawing.get("title_text") or ""),
        str(drawing.get("all_text") or ""),
        _display_filename(drawing),
    ):
        digest.update(b"\0")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _valid_code_token(token: str) -> bool:
    return is_valid_code(token)


def _find_code_token(text: str) -> re.Match | None:
    for match in _CODE_TOKEN.finditer(text or ""):
        if _valid_code_token(match.group(1)):
            return match
    return None


def _extract_code_from_text(text: str) -> str:
    for line in str(text or "").splitlines():
        match = _CODE_LABEL.search(line)
        if match and _valid_code_token(match.group(1)):
            return strip_code_boundary(match.group(1))
    match = _find_code_token(str(text or ""))
    return strip_code_boundary(match.group(1)) if match else ""


def _filename_stem(filename: str) -> str:
    stem, suffix = split_filename(filename)
    if suffix in DRAWING_SUFFIXES | MODEL_SUFFIXES | NATIVE_DRAWING_SUFFIXES:
        return stem
    return clean_text(filename)


def _derive_title_code(drawing: dict, model_code: str = "") -> str:
    # Prefer the drawing's own metadata/filename over a model field. A model
    # can occasionally put a child code into title_block_code; filtering that
    # child would silently lose a required row.
    labelled = _extract_code_from_text(str(drawing.get("title_text") or ""))
    if labelled:
        return labelled
    stem = _filename_stem(_display_filename(drawing))
    match = _find_code_token(stem)
    if match:
        # The token regex allows trailing separators; they are not part of the
        # drawing's own code.
        return strip_code_boundary(match.group(1))
    if _valid_code_token(model_code):
        return strip_code_boundary(model_code)
    return ""


def _strip_own_code(title: str, code: str) -> str:
    title = clean_text(title)
    code = clean_text(code)
    if not title or not code:
        return title
    if normalize_code(title).startswith(normalize_code(code)):
        remainder = title[len(code):].lstrip("-_ ")
        if remainder:
            return clean_text(remainder)
    return title


def _derive_title_name(drawing: dict, model_name: str = "") -> str:
    code = _derive_title_code(drawing)
    title = segments.extract_title(
        str(drawing.get("title_text") or ""),
        str(drawing.get("all_text") or ""),
    )
    if title:
        stripped = _strip_own_code(title, code)
        if stripped:
            return stripped
    if clean_text(model_name):
        return _strip_own_code(model_name, code)
    stem = _filename_stem(_display_filename(drawing))
    match = _find_code_token(stem)
    if match:
        remainder = stem[match.end():].lstrip("-_ ")
        if remainder:
            return clean_text(remainder)
    return ""


def _filter_header_and_title(rows: Iterable[dict], title_code: str) -> list[dict]:
    output = []
    title_norm = normalize_code(title_code).rstrip("._-/ ")
    for index, row in enumerate(rows, 1):
        code = clean_text(row.get("code"))
        name = clean_text(row.get("name"))
        note = clean_text(row.get("note"))
        raw = clean_text(row.get("raw_text"))
        # A row must carry at least a code, name or note. A stray sequence or
        # quantity cell alone is not a BOM line.
        if not any((code, name, note)):
            continue
        compact_code = re.sub(r"\s+", "", code).upper()
        compact_name = re.sub(r"\s+", "", name)
        if compact_code in _HEADER_WORDS or (compact_name in _HEADER_WORDS and not code):
            continue
        if compact_name in {"序号代号名称备注数量", "序号", "代号", "名称", "备注", "数量"} and not _valid_code_token(code):
            continue
        if title_norm and normalize_code(code).rstrip("._-/ ") == title_norm:
            # The title block's own drawing number is not a child item.
            continue
        row = dict(row)
        row.setdefault("page_number", 0)
        row.setdefault("row_index", index)
        row["code"] = code
        row["name"] = name
        row["note"] = note
        row["quantity"] = clean_text(row.get("qty") or row.get("quantity"))
        row["raw_text"] = raw or " ".join(part for part in (code, name, note, row["quantity"]) if part)
        row.pop("qty", None)
        issues = [str(item) for item in row.get("issues") or [] if str(item).strip()]
        if not code:
            issues.append("代号缺失")
        elif not _valid_code_token(code):
            issues.append("代号格式无法确认")
        if not name:
            issues.append("名称缺失")
        if issues:
            row["extraction_status"] = "review"
            row["extraction_issue"] = "；".join(dict.fromkeys(issues))
        else:
            row["extraction_status"] = "ok"
            row["extraction_issue"] = ""
        output.append(row)
    return output


def _merge_and_validate(rows: list[dict]) -> list[dict]:
    """Merge continuation-page duplicates and flag contradictory rows."""
    merged: list[dict] = []
    seen: dict[tuple, dict] = {}
    for row in rows:
        key = (
            clean_text(row.get("sequence_no")),
            normalize_code(row.get("code")),
            clean_text(row.get("name")),
            clean_text(row.get("note")),
            clean_text(row.get("quantity")),
        )
        if key in seen:
            previous = seen[key]
            pages = set(previous.get("pages") or [previous.get("page_number")])
            pages.add(row.get("page_number"))
            previous["pages"] = sorted(page for page in pages if page)
            previous["position"] = {
                **(previous.get("position") or {}),
                "pages": previous["pages"],
            }
            continue
        seen[key] = row
        merged.append(row)

    # Same sequence number with different content is a table conflict. Keep
    # both problem rows visible rather than silently selecting one.
    by_sequence: dict[str, list[dict]] = {}
    for row in merged:
        sequence = clean_text(row.get("sequence_no"))
        if sequence:
            by_sequence.setdefault(sequence, []).append(row)
    for sequence, group in by_sequence.items():
        signatures = {
            (normalize_code(row.get("code")), clean_text(row.get("name")))
            for row in group
        }
        if len(signatures) > 1:
            for row in group:
                issue = "同一序号提取到冲突内容，需人工复核"
                row["extraction_status"] = "review"
                row["extraction_issue"] = "；".join(
                    item for item in (row.get("extraction_issue"), issue) if item
                )

    for index, row in enumerate(merged, 1):
        row["row_index"] = index
        row.setdefault("page_number", 0)
        row.setdefault("pages", [row.get("page_number")] if row.get("page_number") else [])
        row.setdefault("position", {"page": row.get("page_number"), "row_index": index})
    return merged


def _parse_table_lines(lines: list[str], page_number: int, source: str) -> list[dict]:
    """Conservative parser for PDF text layers / OCR text.

    It only creates rows when a code-like token is present.  Notes and
    quantities are marked for review because plain text loses cell boundaries.
    """
    rows = []
    for line in lines:
        cleaned = clean_text(line)
        if not cleaned or _HEADER_LINE.match(cleaned):
            continue
        match = _find_code_token(cleaned)
        if not match:
            continue
        before = cleaned[:match.start()].strip()
        after = cleaned[match.end():].strip()
        tokens = [token.strip() for token in re.split(r"\t+|\s{2,}|\s*\|\s*", after) if token.strip()]
        if len(tokens) == 1:
            # The PDF text layer often preserves normal single spaces rather
            # than cell separators. Split a single cell into words so a trailing
            # quantity does not become part of the name.
            tokens = tokens[0].split()
        sequence = ""
        sequence_match = re.search(r"(\d{1,4})\s*$", before)
        if sequence_match:
            sequence = sequence_match.group(1)
        quantity = ""
        note = ""
        if tokens and re.fullmatch(r"\d+(?:\.\d+)?", tokens[-1]) and len(tokens) >= 2:
            quantity = tokens[-1]
            tokens = tokens[:-1]
        if tokens and any(word in tokens[-1] for word in ("标准件", "外购件", "无图", "备注")):
            note = tokens[-1]
            tokens = tokens[:-1]
        name = " ".join(tokens).strip() if tokens else ""
        raw = cleaned
        issues = ["由PDF文字层/已有OCR文本推断，备注与数量需复核"] if source != "vision" else []
        rows.append({
            "page_number": page_number,
            "sequence_no": sequence,
            "code": match.group(1),
            "name": name,
            "note": note,
            "quantity": quantity,
            "raw_text": raw,
            "position": {"page": page_number, "raw_line": raw, "source": source},
            "issues": issues,
        })
    return rows


def _text_layer_rows(path: Path, pages_limit: int) -> tuple[list[dict], int, list[str]]:
    if path.suffix.lower() != ".pdf" or not path.exists():
        return [], 0, []
    rows: list[dict] = []
    processed = 0
    issues: list[str] = []
    document = None
    try:
        document = pdfium.PdfDocument(str(path))
        total = len(document)
        for index in range(min(total, pages_limit)):
            try:
                page = document[index]
                try:
                    text = page.get_textpage().get_text_range()
                finally:
                    page.close()
            except Exception as exc:
                issues.append(f"第 {index + 1} 页文字层读取失败: {exc}")
                continue
            if text.strip():
                processed += 1
            lines = [line for line in text.splitlines() if line.strip()]
            if not any("序号" in line or "代号" in line or "名称" in line for line in lines):
                continue
            rows.extend(_parse_table_lines(lines, index + 1, "pdf_text"))
    except Exception as exc:
        issues.append(f"PDF 文字层读取失败: {exc}")
    finally:
        if document is not None:
            document.close()
    return rows, processed, issues


def _ocr_text_rows(drawing: dict, pages_limit: int) -> tuple[list[dict], int, list[str]]:
    text = str(drawing.get("all_text") or "")
    if not text.strip():
        return [], 0, []
    issues: list[str] = []
    sections: list[tuple[int, str]] = []
    current_page = 1
    buffer: list[str] = []
    for line in text.splitlines():
        marker = _PAGE_MARKER.match(clean_text(line))
        if marker:
            if buffer:
                sections.append((current_page, "\n".join(buffer)))
            current_page = int(marker.group(1))
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        sections.append((current_page, "\n".join(buffer)))
    rows: list[dict] = []
    for page_number, section in sections[:pages_limit]:
        lines = [line for line in section.splitlines() if line.strip()]
        if any("序号" in line or "代号" in line or "名称" in line for line in lines):
            rows.extend(_parse_table_lines(lines, page_number, "ocr_text"))
    metadata_processed = int(drawing.get("pages_processed") or 0)
    processed = min(pages_limit, metadata_processed) if metadata_processed else (1 if len(sections) == 1 else 0)
    if len(sections) > pages_limit:
        issues.append(f"OCR文本包含 {len(sections)} 页，当前仅检查前 {pages_limit} 页")
    return rows, processed, issues


def _model_rows(
    drawing: dict,
    path: Path,
) -> tuple[list[dict], int, list[str], str, str, bool]:
    """Run the focused vision pass. Returns rows, processed, page issues, codes."""
    pages_limit = config.QWEN_OCR_MAX_PAGES
    try:
        pages = load_pages(path, dpi=400, limit=pages_limit)
    except Exception as exc:
        return [], 0, [f"无法渲染图纸页面: {exc}"], "", "", False
    if not pages:
        return [], 0, ["图纸没有可渲染页面"], "", "", False

    rows: list[dict] = []
    issues: list[str] = []
    processed = 0
    model_code = ""
    model_name = ""
    has_table = False
    for page_number, image in enumerate(pages, 1):
        try:
            data = qwen.extract_bom_page(
                image,
                page_number,
                filename_hint=_display_filename(drawing),
                title_hint=str(drawing.get("title_text") or ""),
            )
        except Exception as exc:
            issues.append(f"第 {page_number} 页明细表识别失败: {exc}")
            continue
        finally:
            image.close()
        processed += 1
        if not isinstance(data, dict):
            issues.append(f"第 {page_number} 页返回格式无效")
            continue
        table_kind = clean_text(data.get("table_kind")).lower()
        if table_kind in {"part_table", "title_block"}:
            # A part parameter table or the title block itself is not an
            # assembly BOM. It is not an extraction failure; this page simply
            # has no assembly BOM to check.
            continue
        if data.get("has_table") is True or str(data.get("has_table")).lower() == "true":
            has_table = True
        if not model_code and clean_text(data.get("title_block_code")):
            model_code = clean_text(data.get("title_block_code"))
        if not model_name and clean_text(data.get("title_block_name")):
            model_name = clean_text(data.get("title_block_name"))
        for issue in data.get("issues") or []:
            if clean_text(issue):
                issues.append(f"第 {page_number} 页: {clean_text(issue)}")
        page_rows = data.get("rows") or []
        if not isinstance(page_rows, list):
            issues.append(f"第 {page_number} 页 rows 不是数组")
            continue
        page_row_count = 0
        for row_index, raw_row in enumerate(page_rows, 1):
            if not isinstance(raw_row, dict):
                issues.append(f"第 {page_number} 页第 {row_index} 行格式无效，已保留问题提示")
                continue
            page_row_count += 1
            row = {
                "page_number": page_number,
                "sequence_no": clean_text(raw_row.get("seq") or raw_row.get("序号")),
                "code": clean_text(raw_row.get("code") or raw_row.get("代号")),
                "name": clean_text(raw_row.get("name") or raw_row.get("名称")),
                "note": clean_text(raw_row.get("note") or raw_row.get("备注")),
                "quantity": clean_text(raw_row.get("qty") or raw_row.get("数量")),
                "raw_text": clean_text(raw_row.get("raw_text") or raw_row.get("原始行文字")),
                "position": {"page": page_number, "row_index": row_index, "source": "vision"},
                "issues": [],
            }
            rows.append(row)
        if page_row_count and not has_table:
            # A model that returns rows while claiming has_table=false is
            # contradictory; keep the rows but force a reviewable partial
            # result instead of reporting a clean table.
            has_table = True
            issues.append(f"第 {page_number} 页模型返回了明细表行但 has_table=false，内容存在矛盾")
    return rows, processed, issues, model_code, model_name, has_table



def _merge_coordinate_notes(base_rows: list[dict], vision_rows: list[dict]) -> list[dict]:
    """Keep local notes/quantities when the focused VLM omits a cell."""
    by_code = {
        normalize_code(row.get("code")): row
        for row in base_rows
        if normalize_code(row.get("code"))
    }
    by_name = {
        clean_text(row.get("name")): row
        for row in base_rows
        if clean_text(row.get("name"))
    }
    merged = []
    for row in vision_rows:
        item = dict(row)
        base = by_code.get(normalize_code(item.get("code"))) or by_name.get(clean_text(item.get("name")))
        if base:
            for field in ("sequence_no", "name", "note", "quantity"):
                if not clean_text(item.get(field)):
                    item[field] = base.get(field, "")
        merged.append(item)
    return merged


def _coordinate_rows(drawing: dict) -> tuple[list[dict], int, list[str], str, str, bool] | None:
    """Use the local coordinate scan before asking the focused VLM to re-read.

    The scan is the cheap first pass.  If it reconstructs a BOM table with
    rows, those rows become the candidate table; otherwise the caller falls
    back to the existing focused vision pass.  A scan without a table never
    becomes a ``no_table`` verdict by itself.
    """
    try:
        scan = coordinate_scan.scan_drawing(drawing)
    except Exception as exc:
        LOGGER.warning("Coordinate scan failed for drawing %s: %s", drawing.get("id"), exc)
        return None
    if not scan.get("available") or not scan.get("blocks"):
        return None
    rows, processed, issues, title_code, title_name, has_table = coordinate_scan.rows_from_scan(scan)
    if not rows:
        return None
    return rows, processed, issues, title_code, title_name, has_table


def _status_for_table(
    *,
    rows: list[dict],
    has_table: bool,
    pages_total: int,
    pages_processed: int,
    issues: list[str],
) -> str:
    if rows:
        if pages_total > pages_processed or issues:
            return "partial"
        return "ok"
    if has_table:
        return "failed"
    if pages_total > pages_processed or issues:
        return "partial"
    return "no_table"


def _extract_uncached(drawing: dict) -> dict:
    path = _file_path(drawing)
    if not path.exists():
        return {
            "status": "failed",
            "rows": [],
            "pages_total": 0,
            "pages_processed": 0,
            "issues": ["原始图纸文件不存在，无法识别明细表"],
            "table_code": "",
            "table_name": "",
            "source": "none",
            "extracted_at": _now(),
        }
    try:
        total_pages = page_count(path)
    except Exception as exc:
        return {
            "status": "failed",
            "rows": [],
            "pages_total": 0,
            "pages_processed": 0,
            "issues": [f"无法读取图纸页数: {exc}"],
            "table_code": "",
            "table_name": "",
            "source": "none",
            "extracted_at": _now(),
        }

    pages_limit = config.QWEN_OCR_MAX_PAGES
    text_rows, text_processed, text_issues = _text_layer_rows(path, pages_limit)
    ocr_rows, ocr_processed, ocr_issues = _ocr_text_rows(drawing, pages_limit)
    model_rows: list[dict] = []
    model_processed = 0
    model_issues: list[str] = []
    model_code = ""
    model_name = ""
    has_table = False
    source = ""
    coordinate_used = False
    qwen_verified = False
    coordinate = _coordinate_rows(drawing)
    if coordinate:
        model_rows, model_processed, model_issues, model_code, model_name, has_table = coordinate
        coordinate_used = True
        source = "coordinate_scan"
        # The local scan is fast but may miss a small quantity/note cell.  Only
        # spend the focused VLM call when a coordinate row is visibly
        # incomplete; a clean scan never pays for a second full-page read.
        if qwen_configured() and any(row.get("issues") for row in model_rows):
            qwen_rows, qwen_processed, qwen_issues, qwen_code, qwen_name, qwen_has_table = _model_rows(
                drawing, path
            )
            if qwen_rows and len(qwen_rows) >= len(model_rows):
                model_rows = _merge_coordinate_notes(model_rows, qwen_rows)
                model_processed = qwen_processed
                model_issues = [*model_issues, *qwen_issues]
                model_code = qwen_code or model_code
                model_name = qwen_name or model_name
                has_table = qwen_has_table or has_table
                qwen_verified = True
    elif qwen_configured():
        model_rows, model_processed, model_issues, model_code, model_name, has_table = _model_rows(
            drawing, path
        )
        if model_rows:
            source = "vision"

    focus_configured = bool(qwen_configured() or coordinate_used)
    vision_verdict_available = bool(focus_configured and model_processed > 0)
    if vision_verdict_available:
        # If the focused vision pass actually ran, trust its explicit
        # no-table/part-table verdict. Text-layer heuristics must not turn a
        # part parameter table into a false assembly BOM.
        rows = model_rows
        issues = [*model_issues]
    else:
        rows = text_rows or ocr_rows
        issues = [*text_issues, *ocr_issues, *model_issues]
    from_model = bool(model_rows)
    pages_processed = model_processed if focus_configured else max(text_processed, ocr_processed)
    if not focus_configured:
        pages_processed = min(pages_limit, max(text_processed, ocr_processed))
    if model_processed and text_processed and model_processed != text_processed:
        pages_processed = max(model_processed, text_processed)
    if not rows and not focus_configured and (text_rows or ocr_rows):
        pages_processed = max(text_processed, ocr_processed)
    if coordinate_used:
        source = "coordinate_scan+qwen" if qwen_verified else "coordinate_scan"
    elif vision_verdict_available:
        source = "vision" if model_rows else "vision_no_table"
    else:
        source = "pdf_text" if text_rows else "ocr_text" if ocr_rows else ""
    if not focus_configured and not rows:
        issues.append("未配置 Qwen 视觉模型，且 PDF 文字层/已有 OCR 未发现可用明细表")
    if rows and not from_model:
        issues.append("当前使用 PDF 文字层或已有 OCR 文本推断明细表，建议人工复核列对齐")

    title_code = _derive_title_code(drawing, model_code)
    title_name = _derive_title_name(drawing, model_name)
    filtered = _filter_header_and_title(rows, title_code)
    merged = _merge_and_validate(filtered)
    valid_rows = [row for row in merged if _valid_code_token(row.get("code") or "")]
    if merged and not valid_rows:
        status = "failed"
        issues.append("明细表行存在，但没有可确认的代号，不能据此判定不存在")
    elif not valid_rows and focus_configured and model_processed == 0:
        # The focused vision pass failed on every page. This is an extraction
        # failure, not an empty table and not a partial no-table result.
        status = "failed"
        if not model_issues and not issues:
            issues.append("明细表视觉识别失败，未获得任何可用页面")
    elif not valid_rows and not focus_configured:
        # Without a focused vision pass, absence of parsed rows is not proof
        # that a table does not exist. Fail visibly instead of reporting an
        # empty/no-table result.
        status = "failed"
        issues.append("未配置 Qwen 视觉模型，且 PDF 文字层/已有 OCR 未获得可确认的明细表行")
    else:
        status = _status_for_table(
            rows=valid_rows,
            has_table=has_table,
            pages_total=total_pages,
            pages_processed=min(total_pages, pages_processed),
            issues=issues,
        )
        if merged and len(valid_rows) < len(merged):
            status = "partial" if status == "ok" else status
            issues.append("部分明细表行缺少可确认的代号，已保留为待复核项")
    if total_pages > pages_limit:
        issues.append(
            f"图纸共 {total_pages} 页，当前识别上限为 {pages_limit} 页；未处理页面未计入检查"
        )
        if status == "no_table":
            status = "partial"
    if total_pages > pages_processed and status == "ok":
        status = "partial"

    return {
        "status": status,
        "rows": merged if merged else [],
        "pages_total": total_pages,
        "pages_processed": min(total_pages, pages_processed),
        "issues": list(dict.fromkeys(issue for issue in issues if issue)),
        "table_code": title_code,
        "table_name": title_name,
        "source": source or "none",
        "extracted_at": _now(),
    }


def drawing_title_name(drawing: dict) -> str:
    """Display title for a drawing, independent of a model response."""
    return _derive_title_name(drawing)


def qwen_configured() -> bool:
    key = (config.QWEN_API_KEY or "").strip()
    return bool(key and key.lower() not in config.PLACEHOLDER_VALUES)


def extract_drawing(drawing: dict, force: bool = False) -> dict:
    """Return a cached or freshly extracted BOM payload for one drawing."""
    signature = drawing_signature(drawing)
    if not force:
        cached = cache_repository.cache_get(int(drawing["id"]), signature)
        if cached:
            cached["cached"] = True
            return cached
    try:
        result = _extract_uncached(drawing)
    except Exception as exc:
        LOGGER.exception("BOM extraction failed for drawing %s", drawing.get("id"))
        result = {
            "status": "failed",
            "rows": [],
            "pages_total": int(drawing.get("page_count") or 0),
            "pages_processed": 0,
            "issues": [f"明细表提取异常: {exc}"],
            "table_code": "",
            "table_name": "",
            "source": "none",
            "extracted_at": _now(),
        }
    result["signature"] = signature
    result["cached"] = False
    cache_repository.cache_put(
        int(drawing["id"]), signature, str(result.get("status") or "failed"), result
    )
    return result
