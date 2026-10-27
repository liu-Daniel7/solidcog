"""Normalize saved OCR into page/block/table/cell evidence.

No OCR or vision model is called here. The completeness checker consumes the
OCR that already produced the drawing record, plus MinerU's saved structured
JSON when it is available. This keeps checks deterministic and avoids silently
changing the evidence being reviewed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from app import config
from app.services import mineru
from app.services.completeness_rules import (
    BOM_COLUMNS,
    TITLE_FIELDS,
    normalize_rules,
)


_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "－": "-", "﹣": "-"})
_DRAWING_NO = re.compile(
    r"(?<![A-Z0-9])(?:"
    r"[A-Z]{1,8}(?:[.\-][A-Z0-9]{1,16}){2,}"
    r"|[A-Z]{2,8}[0-9]{0,8}(?:[.\-][A-Z0-9]{1,16}){1,}"
    r"|[A-Z]{1,8}-(?=[A-Z0-9]*[0-9])[A-Z0-9]{1,16}"
    r"|[A-Z]{2,8}[0-9]{2,}"
    r")(?![A-Z0-9])",
    re.I,
)
_STANDARD_NO = re.compile(
    r"(?<![A-Z0-9])(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN|IEC|GJB|CB|HG|QB|YB|SY|TB|CJ|JG)"
    r"\s*(?:/\s*T)?\s*[0-9]",
    re.I,
)
_MATERIAL = re.compile(
    r"^(?:"
    r"[0-9]{2,3}[A-Z]?|"
    r"Q[0-9]{2,3}[A-Z]?|"
    r"HT[0-9]{3}|QT[0-9]{3}-[0-9]{2}|"
    r"[0-9]{2}(?:Cr|Mn|Ni|Mo)[A-Za-z0-9]*|"
    r"[0-9]{2}Cr[0-9]{2}Ni[0-9]{2}|"
    r"QA[0-9]{2}-[0-9]|"
    r"[0-9]{4}(?:-[A-Z0-9]+)?|"
    r"304L?|316L?|6061|7075"
    r")$",
    re.I,
)
_SCALE = re.compile(r"(?<![\d.])([0-9]{1,3})\s*:\s*([0-9]{1,3})(?![\d.])")
_DATE = re.compile(
    r"(?:19|20)[0-9]{2}\s*[-/.年]\s*[0-9]{1,2}\s*[-/.月]\s*[0-9]{1,2}\s*日?"
)
_FOOTER_PREFIX = re.compile(
    r"^(?:标记|设计|制图|校对|审核|工艺|批准|阶段标记|共\s*[0-9]+\s*张|第\s*[0-9]+\s*页)"
)


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).translate(_DASHES)


def _normalized_header(value: Any) -> str:
    text = _text(value).upper()
    return re.sub(r"[\s.:：/\\()（）\[\]【】,，;；-]+", "", text)


def normalize_drawing_number(value: Any) -> str:
    text = _text(value).upper().strip()
    text = re.sub(r"\s+", "", text)
    text = text.strip(".,，。;；:：()（）[]【】")
    return text


def is_standard_reference(value: str) -> bool:
    return bool(_STANDARD_NO.search(_text(value)))


def extract_drawing_numbers(value: str, *, allow_numeric: bool = False) -> list[str]:
    """Find plausible drawing numbers while excluding standards and dimensions."""
    text = _text(value)
    if not text or is_standard_reference(text):
        return []
    numbers: list[str] = []
    for match in _DRAWING_NO.finditer(text):
        raw = match.group(0)
        normalized = normalize_drawing_number(raw)
        if is_standard_reference(raw) or _MATERIAL.fullmatch(normalized):
            continue
        # A pure number with a decimal point is a dimension, not a drawing ID.
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", normalized):
            continue
        if normalized not in numbers:
            numbers.append(normalized)
    if allow_numeric:
        for match in re.finditer(r"(?<![0-9.])[0-9]{3,}(?:-[A-Z0-9]{1,16})*(?![0-9.])", text):
            normalized = normalize_drawing_number(match.group(0))
            if re.fullmatch(r"(?:19|20)[0-9]{2}[-/.][0-9]{1,2}[-/.][0-9]{1,2}", normalized):
                continue
            if normalized and normalized not in numbers:
                numbers.append(normalized)
    return numbers


def _split_cells(line: str) -> list[str]:
    text = _text(line).strip()
    if not text:
        return []
    if "\t" in text:
        return [part.strip() for part in text.split("\t")]
    if "|" in text:
        return [part.strip() for part in text.split("|")]
    parts = re.split(r"\s{2,}", text)
    if len(parts) > 1:
        return [part.strip() for part in parts]
    return [text]


def _alias_key(value: str, catalog: Iterable[dict]) -> str | None:
    header = _normalized_header(value)
    if not header:
        return None
    for item in catalog:
        aliases = [_normalized_header(alias) for alias in item["aliases"]]
        if any(header == alias or alias in header for alias in aliases if alias):
            return item["key"]
    return None


def _classify_header(cells: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for column, cell in enumerate(cells):
        key = _alias_key(cell, BOM_COLUMNS.values())
        # A merged header may combine "图号/代号"; prefer the more specific
        # drawing-number alias over a generic "名称" match.
        if key and key not in mapping:
            mapping[key] = column
    return mapping


def _is_bom_header(mapping: dict[str, int]) -> bool:
    if "drawing_no" in mapping and (
        {"sequence", "name", "quantity"} & set(mapping)
    ):
        return True
    return {"sequence", "name", "quantity"}.issubset(mapping)


class _HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.raw_rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {
                "row_span": _safe_span(attrs.get("rowspan")),
                "col_span": _safe_span(attrs.get("colspan")),
            }
            self._parts = []
        elif tag == "br" and self._cell is not None:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._cell is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = re.sub(r"[ \t]+", " ", "".join(self._parts)).strip()
            self._row.append(self._cell)
            self._cell = None
            self._parts = []
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.raw_rows.append(self._row)
            self._row = None


def _safe_span(value) -> int:
    try:
        return max(1, min(50, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _materialize_table(raw_rows: list[list[dict]]) -> tuple[list[dict], int]:
    """Expand spans into a rectangular grid so empty-cell checks are reliable."""
    pending: dict[int, tuple[int, str]] = {}
    cells: list[dict] = []
    row_count = 0
    column_count = 0
    for row_index, raw_row in enumerate(raw_rows):
        row_count = row_index + 1
        occupied: dict[int, str] = {}
        for column, (remaining, text) in list(pending.items()):
            occupied[column] = text
            if remaining <= 1:
                pending.pop(column, None)
            else:
                pending[column] = (remaining - 1, text)
        column = 0
        for raw_cell in raw_row:
            while column in occupied:
                column += 1
            row_span = int(raw_cell.get("row_span") or 1)
            col_span = int(raw_cell.get("col_span") or 1)
            text = str(raw_cell.get("text") or "")
            for offset in range(col_span):
                target_column = column + offset
                occupied[target_column] = text
                if row_span > 1:
                    pending[target_column] = (row_span - 1, text)
            column += col_span
        if occupied:
            last_column = max(occupied)
            column_count = max(column_count, last_column + 1)
            for column_index in range(last_column + 1):
                cells.append(
                    {
                        "row": row_index,
                        "col": column_index,
                        "text": occupied.get(column_index, ""),
                        "row_span": 1,
                        "col_span": 1,
                        "bbox": None,
                        "confidence": None,
                    }
                )
    return cells, column_count


def parse_html_table(html: str, bbox=None, confidence: float | None = 0.92) -> dict | None:
    parser = _HTMLTableParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        return None
    if not parser.raw_rows:
        return None
    cells, column_count = _materialize_table(parser.raw_rows)
    rows = max((cell["row"] for cell in cells), default=-1) + 1
    if not rows:
        return None
    plain = "\n".join(
        " | ".join(
            cell["text"]
            for cell in cells
            if cell["row"] == row_index
        )
        for row_index in range(rows)
    )
    return {
        "bbox": bbox,
        "confidence": confidence,
        "source": "MinerU HTML 表格",
        "row_count": rows,
        "column_count": column_count,
        "cells": cells,
        "plain_text": plain,
    }


def _table_signature(table: dict) -> str:
    """Signature that treats duplicate OCR crops of the same BOM as one table.

    Qwen text often contains the same BOM in overview, tile and title crops,
    sometimes in reverse row order and with fewer optional columns. The data
    signature therefore ignores header synonyms, row order and optional columns
    while retaining the sequence/drawing-number/name/quantity values.
    """
    grid: dict[int, dict[int, str]] = {}
    for cell in table.get("cells") or []:
        grid.setdefault(int(cell["row"]), {})[int(cell["col"])] = str(
            cell.get("text") or ""
        )
    columns = int(table.get("column_count") or 0)
    rows = int(table.get("row_count") or 0)
    for header_row in range(min(3, rows)):
        cells = [
            grid.get(header_row, {}).get(column, "") for column in range(columns)
        ]
        mapping = _classify_header(cells)
        if not _is_bom_header(mapping):
            continue
        core_keys = [
            key
            for key in ("sequence", "drawing_no", "name", "quantity")
            if key in mapping
        ]
        if len(core_keys) < 2:
            break
        data = []
        for row_index in range(header_row + 1, rows):
            values = tuple(
                _normalized_header(grid.get(row_index, {}).get(mapping[key], ""))
                for key in core_keys
            )
            if any(values):
                data.append(values)
        if data:
            return "bom|" + ",".join(core_keys) + "|" + "|".join(
                sorted("~".join(value) for value in data)
            )
    rows_by_index: dict[int, list[str]] = {}
    for cell in table.get("cells") or []:
        rows_by_index.setdefault(int(cell["row"]), []).append(
            f"{cell['col']}:{_normalized_header(cell.get('text'))}"
        )
    return "\n".join(
        f"{row_index}|" + "|".join(sorted(rows_by_index[row_index]))
        for row_index in sorted(rows_by_index)
    )


def _dedupe_tables(tables: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for table in tables:
        signature = _table_signature(table)
        if not signature:
            continue
        old = selected.get(signature)
        if old is None or (
            float(table.get("confidence") or 0), int(table.get("row_count") or 0)
        ) > (
            float(old.get("confidence") or 0), int(old.get("row_count") or 0)
        ):
            selected[signature] = table
    result = list(selected.values())
    for index, table in enumerate(result):
        table["table_index"] = index
    return result


def _tables_from_text(
    text: str,
    *,
    source: str,
    confidence: float,
    start_index: int = 0,
) -> list[dict]:
    lines = str(text or "").splitlines()
    tables: list[dict] = []
    index = 0
    while index < len(lines):
        header_cells = _split_cells(lines[index])
        mapping = _classify_header(header_cells)
        if len(header_cells) < 2 or not _is_bom_header(mapping):
            index += 1
            continue
        rows = [header_cells]
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor].strip()
            if not line:
                break
            cells = _split_cells(line)
            if len(cells) < 2:
                break
            if _is_bom_header(_classify_header(cells)):
                break
            if _FOOTER_PREFIX.match(cells[0].strip()):
                break
            sequence_col = mapping.get("sequence")
            if sequence_col is not None:
                first = cells[sequence_col].strip() if sequence_col < len(cells) else ""
                if first and not re.fullmatch(r"[0-9]+[.、]?", first):
                    break
            rows.append(cells)
            cursor += 1
        if len(rows) >= 2:
            width = max(len(row) for row in rows)
            cells = []
            for row_index, row in enumerate(rows):
                for column in range(width):
                    cells.append(
                        {
                            "row": row_index,
                            "col": column,
                            "text": row[column] if column < len(row) else "",
                            "row_span": 1,
                            "col_span": 1,
                            "bbox": None,
                            "confidence": confidence,
                        }
                    )
            plain = "\n".join(" | ".join(row) for row in rows)
            tables.append(
                {
                    "bbox": None,
                    "confidence": confidence,
                    "source": source,
                    "row_count": len(rows),
                    "column_count": width,
                    "cells": cells,
                    "plain_text": plain,
                    "table_index": start_index + len(tables),
                }
            )
        index = max(cursor, index + 1)
    return tables


def _block_text_value(block: dict) -> str:
    return str(
        block.get("content")
        or block.get("text")
        or block.get("html")
        or block.get("md_content")
        or ""
    ).strip()


def _normalized_bbox(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        return None
    return bbox


def _artifact_for(drawing: dict) -> Path | None:
    filename = str(drawing.get("filename") or "")
    if not filename:
        return None
    candidate = config.MINERU_RESULT_DIR / f"{Path(filename).stem}.json"
    return candidate if candidate.is_file() else None


def _pages_from_artifact(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("results"), dict):
        # Accept both the saved app artifact and an unmodified MinerU payload.
        first = next(iter(raw["results"].values()), None)
        if isinstance(first, dict):
            raw = {**raw, **first}
    pages = mineru._pages(raw)
    warnings = [str(item) for item in (raw.get("page_errors") or [])]
    result = []
    for page_index, blocks in enumerate(pages, 1):
        page_blocks = []
        tables = []
        seen_blocks = set()
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").lower()
            bbox = _normalized_bbox(block.get("bbox"))
            content = _block_text_value(block)
            if block_type in {"table", "table_body"} and "<table" in content.lower():
                table = parse_html_table(content, bbox)
                if table:
                    tables.append(table)
            dedupe_key = (block_type, content, tuple(bbox) if bbox else None)
            if dedupe_key in seen_blocks:
                continue
            seen_blocks.add(dedupe_key)
            text = content
            if block_type in {"table", "table_body"} and "<table" in text.lower():
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            if not text and block_type not in {"image", "image_block"}:
                continue
            page_blocks.append(
                {
                    "index": len(page_blocks),
                    "type": block_type,
                    "text": text,
                    "bbox": bbox,
                    "confidence": block.get("confidence") or block.get("score"),
                    "source": "MinerU 原始结果",
                }
            )
        page_text = "\n".join(block["text"] for block in page_blocks if block["text"])
        result.append(
            {
                "page_number": page_index,
                "width": None,
                "height": None,
                "backend": str(raw.get("backend") or "mineru"),
                "model": str(raw.get("model") or ""),
                "source": f"MinerU 原始结果 {path.name}",
                "text": page_text,
                "warnings": warnings if page_index == 1 else [],
                "blocks": page_blocks,
                "tables": _dedupe_tables(tables),
            }
        )
    return result


def _split_qwen_pages(text: str) -> list[tuple[int, str]]:
    source = str(text or "")
    pattern = re.compile(r"(?m)^第\s*([0-9]+)\s*页\s*$")
    matches = list(pattern.finditer(source))
    if not matches:
        return [(1, source)] if source.strip() else []
    pages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        pages.append((int(match.group(1)), source[match.end():end].strip()))
    return pages


def _qwen_blocks(text: str) -> list[dict]:
    blocks = []
    pattern = re.compile(r"(?m)^\[([^\]]+)\]\s*$")
    matches = list(pattern.finditer(str(text or "")))
    if not matches:
        return [
            {
                "index": 0,
                "type": "text",
                "text": str(text or "").strip(),
                "bbox": None,
                "confidence": None,
                "source": "已保存 OCR 文本",
            }
        ] if str(text or "").strip() else []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = str(text)[match.end():end].strip()
        if body:
            blocks.append(
                {
                    "index": index,
                    "type": "ocr_region",
                    "text": body,
                    "bbox": None,
                    "confidence": None,
                    "source": f"已保存 OCR 文本 [{match.group(1)}]",
                }
            )
    return blocks


def _pages_from_saved_text(drawing: dict, warning: str = "") -> list[dict]:
    all_text = str(drawing.get("all_text") or "")
    title_text = str(drawing.get("title_text") or "")
    pages = []
    for page_number, body in _split_qwen_pages(all_text):
        blocks = _qwen_blocks(body)
        tables = _tables_from_text(
            body,
            source="已保存 OCR 文本（表格由文本重建）",
            confidence=0.74,
        )
        pages.append(
            {
                "page_number": page_number,
                "width": None,
                "height": None,
                "backend": "saved_ocr_text",
                "model": "",
                "source": "drawing.all_text",
                "text": body,
                "warnings": [warning] if warning else [],
                "blocks": blocks,
                "tables": tables,
            }
        )
    if not pages:
        pages.append(
            {
                "page_number": 1,
                "width": None,
                "height": None,
                "backend": "saved_ocr_text",
                "model": "",
                "source": "drawing.title_text",
                "text": "",
                "warnings": [warning] if warning else [],
                "blocks": [],
                "tables": [],
            }
        )
    title_tables = _tables_from_text(
        title_text,
        source="标题栏 OCR 文本（表格由文本重建）",
        confidence=0.78,
    )
    existing_signatures = {
        _table_signature(table)
        for page in pages
        for table in page.get("tables") or []
        if _table_signature(table)
    }
    first_page = pages[0]
    for table in title_tables:
        signature = _table_signature(table)
        if signature and signature in existing_signatures:
            continue
        existing_signatures.add(signature)
        if len(pages) == 1:
            first_page["tables"].append(table)
        else:
            # Qwen stores title text as one merged string. If the table could
            # not be located on any page from all_text, do not invent a page:
            # keep it on page 1 with a warning that the physical page is unknown.
            warning = "合并标题栏文本无法确定明细表所在页码，页码按第 1 页暂记，需人工复核"
            first_page["warnings"] = list(
                dict.fromkeys([*(first_page.get("warnings") or []), warning])
            )
            first_page["tables"].append(table)
    for page in pages:
        page["tables"] = _dedupe_tables(page["tables"])
    page = first_page
    if title_text and title_text not in page["text"]:
        page["blocks"].append(
            {
                "index": len(page["blocks"]),
                "type": "title_text",
                "text": title_text,
                "bbox": None,
                "confidence": None,
                "source": "drawing.title_text",
            }
        )
        page["text"] = "\n".join(filter(None, [page["text"], title_text]))
    return pages


def extract_saved_evidence(drawing: dict) -> tuple[list[dict], list[str]]:
    """Return normalized pages plus non-fatal extraction warnings."""
    warnings: list[str] = []
    pages: list[dict] = []
    artifact = _artifact_for(drawing)
    if artifact:
        try:
            pages = _pages_from_artifact(artifact)
        except Exception as exc:
            warnings.append(f"MinerU 原始结构化结果读取失败，已回退到已保存 OCR 文本：{exc}")
    if not pages:
        warning = (
            warnings[0]
            if warnings
            else "未找到 MinerU 原始结构化结果，已使用已保存 OCR 文本；无坐标的表格需人工复核。"
        )
        pages = _pages_from_saved_text(drawing, warning)
    elif str(drawing.get("title_text") or "").strip():
        # Some MinerU runs only returned part of the title block through the
        # table block. Merge text-rebuilt BOM tables from title_text as a
        # secondary source; duplicate signatures are removed.
        page = pages[0]
        extra = _tables_from_text(
            str(drawing.get("title_text") or ""),
            source="标题栏 OCR 文本补充（表格由文本重建）",
            confidence=0.72,
            start_index=len(page["tables"]),
        )
        page["tables"] = _dedupe_tables([*page["tables"], *extra])
    for page in pages:
        page["warnings"] = list(dict.fromkeys(page.get("warnings") or []))
    return pages, list(dict.fromkeys(warnings))


def _title_portion(text: str) -> str:
    lines = str(text or "").splitlines()
    kept = []
    for line in lines:
        cells = _split_cells(line)
        if len(cells) >= 2 and _is_bom_header(_classify_header(cells)):
            break
        kept.append(line)
    return "\n".join(kept)


_EXTRA_HEADER_LABELS = (
    "状态", "STATUS", "说明", "DESCRIPTION", "编制", "PREPARED", "校对",
    "CHECKED", "审核", "APPROVED", "批准", "AUTHORIZED", "图幅", "SIZE",
    "共", "第", "SHEET", "OF", "标记", "分区",
)


def _is_label_cell(value: str) -> bool:
    if not value:
        return False
    header = _normalized_header(value)
    for field in TITLE_FIELDS.values():
        for alias in field["aliases"]:
            if _normalized_header(alias) in header:
                return True
    for label in _EXTRA_HEADER_LABELS:
        if _normalized_header(label) in header:
            return True
    for field in BOM_COLUMNS.values():
        for alias in field["aliases"]:
            if len(_normalized_header(alias)) > 1 and _normalized_header(alias) in header:
                return True
    return False


def _clean_value(value: str) -> str:
    text = _text(value).strip(" \t|:：")
    if not text or _is_label_cell(text):
        return ""
    text = re.sub(r"\s+", " ", text)
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
        return ""
    return text[:160]


def _value_after_alias(cell: str, alias: str) -> str:
    text = _text(cell)
    normalized_alias = _text(alias)
    match = re.search(
        re.escape(normalized_alias) + r"(?:\s*[.:：])?\s*(.+)$", text, re.I
    )
    if not match:
        return ""
    return _clean_value(match.group(1))


def _labeled_fields(lines: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for line_index, line in enumerate(lines):
        cells = _split_cells(line)
        for field in TITLE_FIELDS.values():
            if field["key"] in found:
                continue
            for column, cell in enumerate(cells):
                if not _is_alias(cell, field):
                    continue
                value = ""
                # The common case is a label cell followed by a value cell.
                # Skip duplicate cells produced by merged labels, but stop at a
                # different label so a later field's value cannot leak in.
                for candidate in cells[column + 1:column + 3]:
                    if not str(candidate).strip():
                        continue
                    if _normalized_header(candidate) == _normalized_header(cell):
                        continue
                    if _is_label_cell(candidate):
                        break
                    value = _clean_value(candidate)
                    if value:
                        break
                if not value:
                    value = _value_after_alias(cell, _matched_alias(cell, field))
                if not value and line_index + 1 < len(lines):
                    next_line = lines[line_index + 1].strip()
                    if next_line and not _is_label_cell(next_line):
                        next_cells = _split_cells(next_line)
                        if len(next_cells) == 1:
                            value = _clean_value(next_cells[0])
                if value:
                    found[field["key"]] = {
                        "value": value,
                        "explicit": True,
                        "line": line,
                        "line_index": line_index,
                        "source": "label",
                    }
                    break
    return found


def _is_alias(cell: str, field: dict) -> bool:
    header = _normalized_header(cell)
    return any(
        _normalized_header(alias) in header for alias in field["aliases"] if alias
    )


def _matched_alias(cell: str, field: dict) -> str:
    header = _normalized_header(cell)
    best = ""
    for alias in field["aliases"]:
        normalized = _normalized_header(alias)
        if normalized and normalized in header and len(normalized) > len(best):
            best = alias
    return best


def _heuristic_fields(lines: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not lines:
        return result
    for index, line in enumerate(lines):
        numbers = extract_drawing_numbers(line)
        if numbers and "drawing_no" not in result:
            result["drawing_no"] = {
                "value": line.strip()[:160],
                "explicit": False,
                "line": line,
                "line_index": index,
                "source": "drawing-number pattern",
            }
            for previous in reversed(lines[:index]):
                candidate = previous.strip()
                if (
                    candidate
                    and 1 < len(candidate) <= 40
                    and not _is_label_cell(candidate)
                    and not extract_drawing_numbers(candidate)
                    and not re.search(r"大学|公司|集团|学院|共\s*[0-9]+\s*张", candidate)
                ):
                    result.setdefault(
                        "drawing_name",
                        {
                            "value": candidate,
                            "explicit": False,
                            "line": previous,
                            "line_index": index - 1,
                            "source": "line before drawing number",
                        },
                    )
                    break
            break
    for index, line in enumerate(lines):
        candidate = line.strip()
        if "material" not in result and _MATERIAL.fullmatch(candidate):
            result["material"] = {
                "value": candidate,
                "explicit": False,
                "line": line,
                "line_index": index,
                "source": "material pattern",
            }
        if "scale" not in result:
            match = _SCALE.search(candidate)
            if match:
                result["scale"] = {
                    "value": match.group(0),
                    "explicit": False,
                    "line": line,
                    "line_index": index,
                    "source": "scale pattern",
                }
        if "date" not in result:
            match = _DATE.search(candidate)
            if match:
                result["date"] = {
                    "value": match.group(0),
                    "explicit": False,
                    "line": line,
                    "line_index": index,
                    "source": "date pattern",
                }
    return result


def parse_title_fields(title_text: str) -> dict:
    """Extract title fields with an explicit/uncertain distinction.

    Labels are much stronger evidence than heuristic tokens. Callers must keep
    fields without either evidence source as pending rather than declaring them
    missing.
    """
    text = _text(title_text).strip()
    lines = [line for line in text.splitlines() if line.strip()]
    title_lines = [line for line in _title_portion(text).splitlines() if line.strip()]
    explicit = _labeled_fields(title_lines)
    heuristic = _heuristic_fields(title_lines)
    fields = {**heuristic, **explicit}
    recognized_labels = 0
    for field in TITLE_FIELDS.values():
        if any(_is_alias(cell, field) for line in title_lines for cell in _split_cells(line)):
            recognized_labels += 1
    return {
        "fields": fields,
        "recognized_labels": recognized_labels,
        "has_text": bool(text),
        "quality": (
            "high"
            if recognized_labels >= 2
            else "medium"
            if recognized_labels == 1
            else "low"
            if text
            else "none"
        ),
        "text": text,
    }


def _is_bom_like_table(table: dict) -> bool:
    grid: dict[int, dict[int, str]] = {}
    for cell in table.get("cells") or []:
        grid.setdefault(int(cell["row"]), {})[int(cell["col"])] = str(
            cell.get("text") or ""
        )
    for row_index in range(min(3, int(table.get("row_count") or 0))):
        row = [
            grid.get(row_index, {}).get(column, "")
            for column in range(int(table.get("column_count") or 0))
        ]
        if _is_bom_header(_classify_header(row)):
            return True
    return False


def title_region(pages: list[dict]) -> dict:
    """Best-effort page/bbox evidence for title-block findings."""
    best = None
    for page in pages or []:
        for table in page.get("tables") or []:
            if _is_bom_like_table(table):
                continue
            text = " ".join(
                str(cell.get("text") or "") for cell in table.get("cells") or []
            )
            label_count = sum(
                1
                for field in TITLE_FIELDS.values()
                if any(
                    _normalized_header(alias) in _normalized_header(text)
                    for alias in field["aliases"]
                )
            )
            score = label_count + (2 if "DRAWING NAME" in text.upper() else 0)
            if label_count < 3:
                continue
            candidate = {
                "page_number": page.get("page_number") or 1,
                "bbox": table.get("bbox"),
                "snippet": text[:500],
                "score": score,
                "confidence": table.get("confidence"),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        for block in page.get("blocks") or []:
            if str(block.get("type") or "").lower() in {"table", "table_body"}:
                continue
            text = str(block.get("text") or "")
            label_count = sum(
                1
                for field in TITLE_FIELDS.values()
                if any(
                    _normalized_header(alias) in _normalized_header(text)
                    for alias in field["aliases"]
                )
            )
            if label_count >= 2:
                candidate = {
                    "page_number": page.get("page_number") or 1,
                    "bbox": block.get("bbox"),
                    "snippet": text[:500],
                    "score": label_count,
                    "confidence": block.get("confidence"),
                }
                if best is None or candidate["score"] > best["score"]:
                    best = candidate
    if best:
        return best
    first = (pages or [{}])[0]
    return {
        "page_number": first.get("page_number") or 1,
        "bbox": None,
        "snippet": str(first.get("text") or "")[:500],
        "score": 0,
        "confidence": None,
    }


def bom_tables(pages: list[dict]) -> list[dict]:
    """Return detected detail tables with header row and column mapping."""
    result = []
    for page in pages or []:
        for table in page.get("tables") or []:
            cells = table.get("cells") or []
            row_count = int(table.get("row_count") or 0)
            if row_count < 2:
                continue
            grid: dict[int, dict[int, str]] = {}
            for cell in cells:
                grid.setdefault(int(cell["row"]), {})[int(cell["col"])] = str(
                    cell.get("text") or ""
                )
            header_row = None
            mapping: dict[str, int] = {}
            for row_index in range(min(3, row_count)):
                row = [grid.get(row_index, {}).get(col, "") for col in range(
                    int(table.get("column_count") or 0)
                )]
                candidate = _classify_header(row)
                if _is_bom_header(candidate):
                    header_row = row_index
                    mapping = candidate
                    break
            if header_row is None:
                continue
            data_rows = []
            for row_index in range(header_row + 1, row_count):
                row = [
                    grid.get(row_index, {}).get(col, "")
                    for col in range(int(table.get("column_count") or 0))
                ]
                if not any(str(value).strip() for value in row):
                    continue
                if _FOOTER_PREFIX.match(str(row[0]).strip()):
                    break
                data_rows.append({"row": row_index, "cells": row})
            if not data_rows:
                continue
            result.append(
                {
                    "page_number": page.get("page_number") or 1,
                    "table_index": int(table.get("table_index") or 0),
                    "bbox": table.get("bbox"),
                    "confidence": table.get("confidence"),
                    "source": table.get("source") or "",
                    "header_row": header_row,
                    "columns": mapping,
                    "data_rows": data_rows,
                    "row_count": row_count,
                    "column_count": int(table.get("column_count") or 0),
                    "plain_text": table.get("plain_text") or "",
                }
            )
    return result


def table_header_labels(table: dict) -> dict[str, str]:
    return {
        key: BOM_COLUMNS[key]["label"]
        for key in table.get("columns", {})
        if key in BOM_COLUMNS
    }


def own_drawing_number(title_text: str) -> tuple[str, dict]:
    parsed = parse_title_fields(title_text)
    field = parsed["fields"].get("drawing_no")
    if field and field.get("value"):
        numbers = extract_drawing_numbers(field["value"], allow_numeric=True)
        if numbers:
            return numbers[0], field
    numbers = extract_drawing_numbers(_title_portion(title_text))
    if numbers:
        return numbers[0], {
            "value": numbers[0],
            "explicit": False,
            "source": "drawing-number pattern",
        }
    return "", {}


def extract_reference_candidates(pages: list[dict]) -> list[dict]:
    """Extract per-row detail-table references from normalized evidence."""
    references = []
    for table in bom_tables(pages):
        drawing_no_col = table["columns"].get("drawing_no")
        if drawing_no_col is None:
            continue
        for row in table["data_rows"]:
            cells = row["cells"]
            raw = cells[drawing_no_col].strip() if drawing_no_col < len(cells) else ""
            if not raw:
                continue
            numbers = extract_drawing_numbers(raw, allow_numeric=True)
            if not numbers:
                continue
            for number in numbers:
                references.append(
                    {
                        "page_number": table["page_number"],
                        "table_index": table["table_index"],
                        "row_index": row["row"],
                        "cell_index": drawing_no_col,
                        "raw_value": raw,
                        "normalized_value": number,
                        "bbox": table.get("bbox"),
                        "confidence": table.get("confidence"),
                        "source_text": table.get("plain_text") or "",
                        "table": table,
                    }
                )
    return references


def table_requires_review(table: dict) -> tuple[bool, str]:
    return table_review_confidence(table)


def field_label_present(title_text: str, field_key: str) -> bool:
    """Return whether the OCR visibly contains a title-field label."""
    field = TITLE_FIELDS.get(field_key)
    if not field:
        return False
    title = _title_portion(title_text)
    for line in title.splitlines():
        for cell in _split_cells(line):
            if _is_alias(cell, field):
                return True
    return False


def table_review_confidence(table: dict) -> tuple[bool, str]:
    """Only low-confidence structures are blocked from automatic judgments."""
    confidence = table.get("confidence")
    if confidence is not None and float(confidence) < 0.7:
        return True, "表格结构识别置信度低于 70%，不自动判定空白单元格"
    return False, ""


def best_title_table(pages: list[dict]) -> tuple[dict, dict] | tuple[None, None]:
    """Select the most complete title-block table without trusting probe labels."""
    best = None
    best_page = None
    best_score = -1
    for page in pages or []:
        for table in page.get("tables") or []:
            if _is_bom_like_table(table):
                continue
            cells = table.get("cells") or []
            labels = set()
            for cell in cells:
                text = str(cell.get("text") or "")
                for field in TITLE_FIELDS.values():
                    if _is_alias(text, field):
                        labels.add(field["key"])
            if len(labels) < 2:
                continue
            columns = int(table.get("column_count") or 0)
            rows = int(table.get("row_count") or 0)
            header_bonus = 2 if any(
                "DRAWING" in str(cell.get("text") or "").upper() for cell in cells
            ) else 0
            score = len(labels) * 100 + columns * 2 + rows + header_bonus
            if score > best_score:
                best = table
                best_page = page
                best_score = score
    return best_page, best


def parse_title_fields_from_pages(pages: list[dict]) -> dict:
    """Read title fields from real table-cell relationships when available."""
    page, table = best_title_table(pages)
    if not table or not page:
        return {"fields": {}, "recognized_labels": 0, "table": None}
    columns = int(table.get("column_count") or 0)
    rows = int(table.get("row_count") or 0)
    grid: dict[int, dict[int, str]] = {}
    for cell in table.get("cells") or []:
        grid.setdefault(int(cell["row"]), {})[int(cell["col"])] = str(
            cell.get("text") or ""
        )
    found: dict[str, dict] = {}
    for row_index in range(rows):
        for column in range(columns):
            cell = grid.get(row_index, {}).get(column, "")
            if not cell:
                continue
            for field in TITLE_FIELDS.values():
                key = field["key"]
                if key in found or not _is_alias(cell, field):
                    continue
                value = ""
                value_column = None
                for candidate_column in range(column + 1, columns):
                    candidate = grid.get(row_index, {}).get(candidate_column, "")
                    if not candidate:
                        continue
                    if _is_label_cell(candidate):
                        break
                    cleaned = _clean_value(candidate)
                    if cleaned:
                        value = cleaned
                        value_column = candidate_column
                        break
                # Revision tables commonly place the revision letter in the
                # row immediately above the "版次REV." header.
                if not value and key == "revision" and row_index > 0:
                    above = grid.get(row_index - 1, {}).get(column, "")
                    if above and not _is_label_cell(above):
                        value = _clean_value(above)
                        value_column = column
                if value:
                    found[key] = {
                        "value": value,
                        "explicit": True,
                        "line": f"{cell} | {value}",
                        "line_index": row_index,
                        "source": "title_table_cell",
                        "table_row": row_index,
                        "table_column": column,
                        "value_column": value_column,
                    }
    recognized = len(found)
    return {
        "fields": found,
        "recognized_labels": recognized,
        "table": table,
        "page_number": page.get("page_number") or 1,
        "bbox": table.get("bbox"),
        "confidence": table.get("confidence"),
        "quality": "high" if recognized >= 2 else "medium" if recognized else "low",
    }
