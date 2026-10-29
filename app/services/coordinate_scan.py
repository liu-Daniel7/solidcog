"""Coordinate-aware page scan and BOM-region reconstruction.

The scanner is deliberately split from the final BOM model call:

1. One local pass (Apple Vision on macOS) records every recognized text block
   with a page-normalized bounding box.
2. Header labels, title-block labels and rows are reconstructed from geometry.
3. Only an uncertain or incomplete table is sent to the focused BOM model.

This keeps the expensive VLM pass focused on the table instead of asking it to
re-read the whole sheet several times.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app import config
from app.repositories import scans as scan_repository
from app.services import macos_vision, rapidocr_scan
from app.services.images import load_pages, page_count
from app.services.kit_rules import clean_text, is_valid_code, normalize_code

HEADER_KEYS = ("序号", "代号", "名称", "数量", "备注")
OPTIONAL_COLUMNS = ("材料", "单重", "总重")
HEADER_LABELS = set(HEADER_KEYS) | set(OPTIONAL_COLUMNS) | {"比例", "重量", "图号"}
CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,}")
NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
SCAN_VERSION = "vision-3"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(path: Path, dpi: int, pages_limit: int, scanner_name: str) -> str:
    digest = hashlib.sha256()
    digest.update(_hash_file(path).encode("ascii"))
    for value in (SCAN_VERSION, str(dpi), str(pages_limit), scanner_name):
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
    return digest.hexdigest()


def _center(block: dict) -> tuple[float, float]:
    x, y, w, h = block["bbox"]
    return x + w / 2, y + h / 2


def _union(boxes: list[list[float]]) -> list[float]:
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[0] + box[2] for box in boxes)
    y1 = max(box[1] + box[3] for box in boxes)
    return [round(x0, 6), round(y0, 6), round(x1 - x0, 6), round(y1 - y0, 6)]


def _group_by_y(blocks: list[dict], tolerance: float = 0.014) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for block in sorted(blocks, key=lambda item: (_center(item)[1], _center(item)[0])):
        _, y = _center(block)
        if groups:
            last = groups[-1]
            last_y = sum(_center(item)[1] for item in last) / len(last)
            if abs(y - last_y) <= tolerance:
                last.append(block)
                continue
        groups.append([block])
    return groups


def _normalize_ocr_code(code: str) -> str:
    # OCR frequently reads the zero in a code as capital O.  Correct only the
    # common letter-O-between-digits pattern; other ambiguities stay reviewable.
    return re.sub(r"(?<=[A-Z])O(?=\d{2,})|(?<=\d)O(?=\d)", "0", code)


def _code_from_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    for match in CODE_RE.finditer(compact):
        candidate = normalize_code(match.group(0))
        # Title blocks frequently render "CODE-NAME" as one OCR block.  A
        # short alphabetic suffix is a name fragment, not part of the drawing
        # code (e.g. SK.CNC.0001-SK).
        candidate = _normalize_ocr_code(re.sub(r"-[A-Za-z]{1,4}$", "", candidate))
        if is_valid_code(candidate):
            return candidate
    return ""


def _header_group(blocks: list[dict]) -> tuple[list[dict], dict[str, float]]:
    best: list[dict] = []
    best_score = 0
    for group in _group_by_y(blocks, tolerance=0.018):
        keys = set()
        for block in group:
            text = block["text"]
            for key in HEADER_KEYS:
                if key in text:
                    keys.add(key)
        if len(keys) > best_score:
            best = group
            best_score = len(keys)
    if best_score < 3:
        return [], {}
    columns: dict[str, float] = {}
    for key in HEADER_KEYS + OPTIONAL_COLUMNS:
        candidates = [block for block in best if key in block["text"]]
        if candidates:
            columns[key] = _center(candidates[0])[0]
    return best, columns


def _is_header_block(block: dict) -> bool:
    text = clean_text(block.get("text"))
    return bool(text and (text in HEADER_LABELS or any(key == text for key in HEADER_KEYS)))


def _nearest_column(x: float, columns: dict[str, float]) -> tuple[str, float] | None:
    if not columns:
        return None
    key = min(columns, key=lambda item: abs(columns[item] - x))
    return key, abs(columns[key] - x)


def _table_region(blocks: list[dict]) -> dict | None:
    header, columns = _header_group(blocks)
    if not header:
        return None
    header_y = sum(_center(block)[1] for block in header) / len(header)
    x_values = list(columns.values())
    x_min, x_max = min(x_values) - 0.07, max(x_values) + 0.07
    candidates = [
        block for block in blocks
        if block not in header
        and not _is_header_block(block)
        and header_y - 0.35 <= _center(block)[1] <= header_y + 0.08
        and x_min <= _center(block)[0] <= x_max
    ]
    rows: list[dict] = []
    row_groups = _group_by_y(candidates, tolerance=0.013)
    for group in row_groups:
        cells: dict[str, list[str]] = {key: [] for key in HEADER_KEYS + OPTIONAL_COLUMNS}
        for block in group:
            x, _ = _center(block)
            nearest = _nearest_column(x, columns)
            if not nearest:
                continue
            key, distance = nearest
            if distance > 0.075:
                continue
            cells[key].append(clean_text(block["text"]))
        code = re.sub(r"\s+", "", " ".join(cells["代号"]))
        code = _normalize_ocr_code(normalize_code(code))
        name = " ".join(cells["名称"]).strip()
        note = " ".join(cells["备注"]).strip()
        # A single page-border letter or a weight value can land in the note
        # column when the table is rotated.  Notes that carry meaning in these
        # drawings contain Chinese; discard ASCII-only noise.
        if note and not re.search(r"[\u4e00-\u9fff]", note):
            note = ""
        quantity = " ".join(cells["数量"]).strip()
        sequence = " ".join(cells["序号"]).strip()
        # A row must have a code or a named item with a numeric quantity.
        # Title-block labels live below the table and otherwise look like rows.
        if not code and not (name and NUMERIC_RE.fullmatch(quantity)):
            continue
        issues: list[str] = []
        if not code:
            issues.append("代号缺失")
        elif not is_valid_code(code):
            issues.append("代号格式无法确认")
        if not name:
            issues.append("名称缺失")
        if quantity and not NUMERIC_RE.fullmatch(quantity):
            quantity_match = re.search(r"-?\d+(?:\.\d+)?", quantity)
            quantity = quantity_match.group(0) if quantity_match else ""
        if not quantity:
            issues.append("数量缺失")
        rows.append({
            "page_number": 0,
            "row_index": 0,
            "sequence_no": sequence,
            "code": code,
            "name": name,
            "note": note,
            "quantity": quantity,
            "raw_text": " ".join(block["text"] for block in group).strip(),
            "position": {
                "page": 0,
                "bbox": _union([block["bbox"] for block in group]),
                "source": "vision_scan",
            },
            "issues": issues,
        })
    table_boxes = [block["bbox"] for block in header]
    for group in row_groups:
        table_boxes.extend(block["bbox"] for block in group)
    return {
        "header": [block["text"] for block in header],
        "columns": columns,
        "bbox": _union(table_boxes),
        "rows": rows,
        "issues": [] if all(key in columns for key in HEADER_KEYS) else ["明细表表头不完整，列关系需复核"],
    }


def _title_region(blocks: list[dict]) -> dict:
    code_block = next(
        (
            block for block in sorted(blocks, key=lambda item: (-_center(item)[1], -_center(item)[0]))
            if _code_from_text(block["text"]) and _center(block)[1] > 0.7 and _center(block)[0] > 0.5
        ),
        None,
    )
    if not code_block:
        return {}
    code = _code_from_text(code_block["text"])
    excluded = {
        "装配件", "阶段标记", "更改文件号 签名", "年月日", "处数", "分区", "标记",
        "设计", "校核", "工艺", "审核", "批准", "标准化", "替代", "共张第张版本",
        "主管设计", "比例", "重量", "签字", "日期", "材料", "单重", "总重",
        "签名", "标记处数", "标记", "处数",
    }
    name_candidates = [
        block for block in blocks
        if block is not code_block
        and abs(_center(block)[0] - _center(code_block)[0]) < 0.2
        and abs(_center(block)[1] - _center(code_block)[1]) < 0.12
        and re.search(r"[\u4e00-\u9fff]", block["text"])
        and clean_text(block["text"]) not in excluded
        and not _is_header_block(block)
    ]
    name = ""
    if name_candidates:
        best = min(
            name_candidates,
            key=lambda item: (
                abs(_center(item)[0] - _center(code_block)[0])
                + abs(_center(item)[1] - _center(code_block)[1]),
                -float(item.get("confidence") or 0),
            ),
        )
        if float(best.get("confidence") or 0) >= 0.8:
            name = clean_text(best["text"])
    return {"code": code, "name": name, "bbox": code_block["bbox"]}


def _tech_region(blocks: list[dict]) -> dict:
    selected = []
    for block in blocks:
        if "技术要求" in block["text"] or "工艺要求" in block["text"]:
            selected.append(block)
    if not selected:
        return {}
    return {
        "text": "\n".join(block["text"] for block in sorted(selected, key=lambda item: (_center(item)[1], _center(item)[0]))),
        "bbox": _union([block["bbox"] for block in selected]),
    }


def _page_regions(blocks: list[dict]) -> dict:
    bom = _table_region(blocks)
    if bom:
        for row in bom["rows"]:
            row["page_number"] = 1
            row["position"]["page"] = 1
    return {
        "bom": bom or {},
        "title": _title_region(blocks),
        "tech": _tech_region(blocks),
    }


def _page_text(blocks: list[dict], page_number: int) -> str:
    lines = [
        block["text"] for block in sorted(blocks, key=lambda item: (_center(item)[1], _center(item)[0]))
        if block["text"].strip()
    ]
    return f"第 {page_number} 页\n" + "\n".join(lines)


def scan_file(path: Path, pages_limit: int | None = None, dpi: int | None = None) -> dict:
    """Scan a file once and return pages, blocks and geometric regions."""
    scanner = rapidocr_scan if rapidocr_scan.available() else macos_vision
    if not scanner.available():
        return {"available": False, "pages": [], "blocks": [], "text": "", "signature": ""}
    scanner_name = "rapidocr" if scanner is rapidocr_scan else "macos_vision"
    pages_limit = pages_limit or config.QWEN_OCR_MAX_PAGES
    dpi = dpi or config.KIT_SCAN_DPI
    try:
        images = load_pages(path, dpi=dpi, limit=pages_limit)
    except Exception as exc:
        return {"available": False, "error": str(exc), "pages": [], "blocks": [], "text": "", "signature": ""}
    pages = []
    all_blocks: list[dict] = []
    try:
        for page_number, image in enumerate(images, 1):
            blocks = scanner.scan_image(image)
            for block in blocks:
                block["page"] = page_number
            regions = _page_regions(blocks)
            pages.append({
                "page": page_number,
                "width": image.width,
                "height": image.height,
                "blocks": blocks,
                "regions": regions,
            })
            all_blocks.extend(blocks)
    finally:
        for image in images:
            image.close()
    return {
        "available": True,
        "pages": pages,
        "blocks": all_blocks,
        "text": "\n\n".join(_page_text(page["blocks"], page["page"]) for page in pages if page["blocks"]),
        "signature": _signature(path, dpi, pages_limit, scanner_name),
        "scanner": f"{scanner_name}/{SCAN_VERSION}",
        "pages_total": page_count(path),
        "pages_processed": len(pages),
    }


def scan_drawing(drawing: dict, force: bool = False) -> dict:
    path = config.UPLOAD_DIR / str(drawing.get("filename") or "")
    if not path.exists() or not (rapidocr_scan.available() or macos_vision.available()):
        return {"available": False, "pages": [], "blocks": [], "text": ""}
    scanner_name = "rapidocr" if rapidocr_scan.available() else "macos_vision"
    signature = _signature(path, config.KIT_SCAN_DPI, config.QWEN_OCR_MAX_PAGES, scanner_name)
    if not force:
        cached = scan_repository.get(int(drawing["id"]), signature)
        if cached:
            cached["cached"] = True
            return cached
    result = scan_file(path, pages_limit=config.QWEN_OCR_MAX_PAGES, dpi=config.KIT_SCAN_DPI)
    if result.get("available") and result.get("blocks"):
        scan_repository.put(int(drawing["id"]), signature, result)
    result["cached"] = False
    return result



def ocr_result(scan: dict) -> dict:
    """Translate a coordinate scan into the existing OCR result shape."""
    titles = []
    techs = []
    for page in scan.get("pages") or []:
        title = (page.get("regions") or {}).get("title") or {}
        tech = (page.get("regions") or {}).get("tech") or {}
        if title.get("code") or title.get("name"):
            titles.append(" ".join(part for part in (title.get("code"), title.get("name")) if part))
        if tech.get("text"):
            techs.append(tech["text"])
    first = (scan.get("pages") or [{}])[0]
    return {
        "title_block": "\n\n".join(titles),
        "tech_block": "\n\n".join(techs),
        "all_text": scan.get("text") or "",
        "layout": "horizontal" if int(first.get("width") or 0) >= int(first.get("height") or 0) else "vertical",
        "backend": "qwen_scan",
        "model": scan.get("scanner") or "local_scan",
        "pages_processed": int(scan.get("pages_processed") or 0),
        "pages_total": int(scan.get("pages_total") or 0),
        "page_errors": [],
        "_scan": scan,
    }

def rows_from_scan(scan: dict) -> tuple[list[dict], int, list[str], str, str, bool]:
    """Return rows and table metadata for the BOM extraction path."""
    rows: list[dict] = []
    issues: list[str] = []
    title_code = ""
    title_name = ""
    has_table = False
    pages = scan.get("pages") or []
    for page in pages:
        page_number = int(page.get("page") or 1)
        bom = (page.get("regions") or {}).get("bom") or {}
        title = (page.get("regions") or {}).get("title") or {}
        if title.get("code") and not title_code:
            title_code = title["code"]
        if title.get("name") and not title_name:
            title_name = title["name"]
        if bom:
            has_table = True
            issues.extend(str(item) for item in bom.get("issues") or [] if str(item).strip())
            for row in bom.get("rows") or []:
                item = dict(row)
                item["page_number"] = page_number
                item["position"] = {**(item.get("position") or {}), "page": page_number}
                rows.append(item)
    for index, row in enumerate(rows, 1):
        row["row_index"] = index
    return rows, len(pages), list(dict.fromkeys(issues)), title_code, title_name, has_table
