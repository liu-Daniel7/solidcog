"""Normalize OCR text into stable, translation-sized rows.

The first translation version deliberately keeps a line/field-level table. It
does not claim to reconstruct the original drawing's merged table cells.
"""

from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException

from app.repositories import drawings as drawings_repository
from app.repositories import translations as repository

SECTION_LABELS = {
    "title": "标题栏",
    "tech": "技术要求",
    "extra": "全文补充",
}

# Labels are matched as substrings because MinerU often emits the Chinese label
# together with its English counterpart, for example "图名DRAWING NAME".
FIELD_ALIASES = {
    "零件名称": "零件名称",
    "图纸编号": "图号",
    "图纸名称": "图名",
    "图名": "图名",
    "名称": "名称",
    "图号": "图号",
    "项目": "项目",
    "材料": "材料",
    "材质": "材料",
    "比例": "比例",
    "质量": "质量",
    "重量": "重量",
    "图幅": "图幅",
    "版次": "版次",
    "状态": "状态",
    "说明": "说明",
    "编制": "编制",
    "设计": "设计",
    "制图": "制图",
    "校对": "校对",
    "审核": "审核",
    "批准": "批准",
    "日期": "日期",
    "数量": "数量",
    "单位": "单位",
    "页码": "页码",
    "SHEET": "页码",
    "DRAWINGNAME": "图名",
    "DRAWINGNO": "图号",
    "PARTNAME": "零件名称",
    "MATERIAL": "材料",
    "SCALE": "比例",
    "MASS": "质量",
    "WEIGHT": "重量",
    "SIZE": "图幅",
    "REV": "版次",
    "REVISION": "版次",
    "STATUS": "状态",
    "DESCRIPTION": "说明",
    "PREPARED": "编制",
    "DESIGN": "设计",
    "DRAWN": "制图",
    "CHECKED": "校对",
    "APPROVED": "审核",
    "AUTHORIZED": "批准",
    "DATE": "日期",
    "QTY": "数量",
    "QUANTITY": "数量",
    "UNIT": "单位",
}

_ALIASES = sorted(FIELD_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
_HEADER_TOKENS = {
    "标记", "处数", "分区", "更改文件号", "签名", "年月日", "标准化",
    "阶段标记", "工艺", "共张第张", "共页第页",
}
_CJK = re.compile(r"[\u3400-\u9fff]")
_COPYRIGHT = re.compile(r"版权|著作权|COPYRIGHT|PROPERTY", re.I)
_PAGE_MARKER = re.compile(r"^第\s*\d+\s*页(?:\s*/?\s*共\s*\d+\s*页)?$", re.I)
_EXTRA_LABEL = re.compile(r"图|视图|剖视|主视|俯视|左视|右视|三维|轴测|展开|折弯|下料|背面|刻字")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _match_text(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value or "").lower()


def _field_label(cell: str) -> str | None:
    compact = _compact(cell)
    if not compact:
        return None
    for alias, label in _ALIASES:
        if alias in compact:
            return label
    if re.search(r"(?:公司|集团|厂|大学|学院|COMPANY|GROUP)$", compact):
        return "单位"
    return None


def _is_header_cell(cell: str) -> bool:
    compact = _compact(cell)
    return any(token in compact for token in _HEADER_TOKENS)


def _embedded_page_value(cell: str) -> str | None:
    match = re.search(r"第\s*\d+\s*页(?:\s*/?\s*共\s*\d+\s*页)?", cell)
    return match.group(0) if match else None


def _is_frame_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.fullmatch(r"[A-Z0-9|/\-.\s]{1,5}", stripped):
        return True
    if _PAGE_MARKER.match(stripped):
        return True
    return False


def _clean_text(text: str) -> str:
    text = re.sub(r"\\\((.*?)\\\)", r"\1", text)
    text = re.sub(r"\\\[(.*?)\\\]", r"\1", text)
    return text.strip(" \t|")


def _append_unique(rows: list[dict], label: str, text: str, seen: set[tuple[str, str]]) -> None:
    text = _clean_text(text)
    if not text or _COPYRIGHT.search(text):
        return
    key = (label, _match_text(text))
    if key in seen:
        return
    seen.add(key)
    rows.append({"label": label, "source_text": text})


def _split_title_line(line: str) -> list[str]:
    if "|" in line:
        return [cell.strip() for cell in line.split("|") if cell.strip()]
    colon = re.match(r"^([^：:]{1,30})[：:]\s*(.+)$", line)
    if colon:
        return [colon.group(1).strip(), colon.group(2).strip()]
    for alias, _ in _ALIASES:
        match = re.match(rf"^{re.escape(alias)}\s*(?:[：:]\s*)?(.+)$", line, re.I)
        if match:
            return [alias, match.group(1).strip()]
    return [line]


def _title_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block in re.split(r"\n\s*\n", text or ""):
        for line in block.splitlines():
            line = line.strip()
            if not line or _is_frame_noise(line):
                continue
            if _COPYRIGHT.search(line):
                continue
            cells = _split_title_line(line)
            if not cells:
                continue
            labels = [_field_label(cell) for cell in cells]
            header_flags = [_is_header_cell(cell) for cell in cells]
            if all(label or header for label, header in zip(labels, header_flags)):
                # A pure header row such as 版次 | 状态 | 说明 | ...
                continue
            if not any(labels):
                if re.search(r"初版|第\s*\w+\s*版|CFC|REV", line, re.I):
                    _append_unique(rows, "版次记录", " | ".join(cells), seen)
                elif len(cells) == 1 and 2 <= len(_CJK.findall(line)) <= 40:
                    _append_unique(rows, "图名", line, seen)
                else:
                    _append_unique(rows, "标题栏", " | ".join(cells), seen)
                continue

            used: set[int] = set()
            index = 0
            while index < len(cells):
                label = labels[index]
                if not label:
                    if _is_header_cell(cells[index]):
                        index += 1
                        continue
                    if re.search(r"(?:公司|集团|厂|大学|学院|COMPANY|GROUP)$", _compact(cells[index])):
                        _append_unique(rows, "单位", cells[index], seen)
                        used.add(index)
                    index += 1
                    continue
                if label == "单位" and re.search(r"(?:公司|集团|厂|大学|学院|COMPANY|GROUP)$", _compact(cells[index])):
                    _append_unique(rows, "单位", cells[index], seen)
                    index += 1
                    continue
                if label == "页码":
                    embedded = _embedded_page_value(cells[index])
                    if embedded:
                        _append_unique(rows, "页码", embedded, seen)
                        index += 1
                        continue
                next_index = index + 1
                while next_index < len(cells) and not cells[next_index]:
                    next_index += 1
                if next_index < len(cells) and not labels[next_index] and not header_flags[next_index] and next_index not in used:
                    value = cells[next_index]
                    if label == "制图" and re.fullmatch(r"[A-Z]{2,}\d{2,}(?:[.-]\d+)*", value):
                        label = "图号"
                    _append_unique(rows, label, value, seen)
                    used.add(next_index)
                    index = next_index + 1
                    continue
                # Label/value pairs are sometimes written right-to-left.
                previous = index - 1
                while previous >= 0 and (not cells[previous] or labels[previous] or previous in used):
                    previous -= 1
                if previous >= 0 and not header_flags[previous] and not (label == "单位" and re.fullmatch(r"\d{1,4}", cells[previous])):
                    _append_unique(rows, label, cells[previous], seen)
                    used.add(previous)
                index += 1
    material = re.match(r"^\s*((?=[A-Z0-9#.\-]*\d)[A-Z0-9#.\-]{2,20})\s*\|", text or "", re.I)
    if material and not any(row["label"] == "材料" for row in rows):
        rows.insert(0, {"label": "材料", "source_text": material.group(1)})
    return rows


def _tech_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block in re.split(r"\n\s*\n", text or ""):
        block = block.strip()
        if not block:
            continue
        block = re.sub(r"^\s*(?:技术要求|技术条件|工艺要求|技术说明)\s*[:：]?\s*", "", block, flags=re.I)
        numbered = list(re.finditer(r"(?m)^\s*(\d{1,3})\s*[.、)]\s*", block))
        if numbered:
            for position, match in enumerate(numbered):
                end = numbered[position + 1].start() if position + 1 < len(numbered) else len(block)
                body = re.sub(r"\s+", " ", block[match.end():end]).strip()
                if body:
                    _append_unique(rows, f"第 {match.group(1)} 条", body, seen)
        else:
            body = re.sub(r"\s+", " ", block).strip()
            if body:
                _append_unique(rows, "技术要求", body, seen)
    return rows


def _looks_like_title_line(line: str) -> bool:
    cells = _split_title_line(line.strip())
    if len(cells) < 2:
        return False
    return any(_field_label(cell) for cell in cells)


def _extra_rows(text: str, title_text: str, tech_text: str, tech_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    covered_sources = [title_text, tech_text]
    covered_sources.extend(row["source_text"] for row in tech_rows)
    covered = [
        normalized for normalized in (_match_text(value) for value in covered_sources)
        if len(normalized) >= 4
    ]
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("#*- ").strip()
        if not line or _is_frame_noise(line) or _COPYRIGHT.search(line):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if re.fullmatch(r"第\s*\d+\s*页", line):
            continue
        if "|" in line or r"\(" in line or r"\[" in line:
            continue
        if len(line) > 40 or not _EXTRA_LABEL.search(line):
            continue
        if re.match(r"^(?:第\s*\d+\s*章|\d+(?:\.\d+)*[.、)]?\s*)", line):
            continue
        if not _CJK.search(line):
            continue
        normalized = _match_text(line)
        if len(normalized) < 2:
            continue
        if re.fullmatch(r"(?:技术要求|技术条件|工艺要求|技术说明)[:：]?", line):
            continue
        if _looks_like_title_line(line):
            continue
        if any(normalized in source or source in normalized for source in covered):
            continue
        if len(_CJK.findall(line)) < 2 and not re.search(r"图|孔|槽|面|线|角|边|焊|轴|套|板|座", line):
            continue
        _append_unique(rows, "全文补充", line, seen)
        if len(rows) >= 80:
            break
    return rows


def _fallback_title(text: str) -> str:
    selected = []
    for line in (text or "").splitlines():
        cells = _split_title_line(line.strip())
        if any(_field_label(cell) or _is_header_cell(cell) for cell in cells):
            selected.append(line)
        elif re.search(r"(?:公司|集团|厂|大学|学院|COMPANY|GROUP)\s*$", _compact(line), re.I):
            selected.append(line)
    return "\n".join(selected)


def _fallback_tech(text: str) -> str:
    match = re.search(r"(?:技术要求|技术条件|工艺要求|技术说明)\s*[:：]?", text or "")
    if not match:
        return ""
    body = []
    for line in text[match.end():].splitlines():
        if _looks_like_title_line(line) or re.match(r"^\s*(?:标记|设计|制图|审核|工艺)\b", line):
            break
        body.append(line)
    return "\n".join(body).strip()


def _is_translatable(label: str, text: str) -> bool:
    if label in {"图号", "页码"}:
        return False
    return bool(_CJK.search(text))


def build_segments(title_text: str = "", tech_text: str = "", all_text: str = "") -> list[dict]:
    """Build deterministic rows from the three OCR text fields."""
    all_text = all_text or ""
    title_source = title_text or _fallback_title(all_text)
    tech_source = tech_text or _fallback_tech(all_text)
    tech_rows = _tech_rows(tech_source)
    sections = [
        ("title", _title_rows(title_source)),
        ("tech", tech_rows),
        ("extra", _extra_rows(all_text, title_source, tech_source, tech_rows)),
    ]
    segments: list[dict] = []
    ordinal = 0
    for section, rows in sections:
        for index, row in enumerate(rows, 1):
            text = row["source_text"].strip()
            if not text:
                continue
            ordinal += 1
            segments.append({
                "segment_key": f"{section}:{index:03d}",
                "section": section,
                "ordinal": ordinal,
                "label": row["label"] or SECTION_LABELS[section],
                "source_text": text,
                "source_hash": source_hash(text),
                "translatable": _is_translatable(row["label"] or "", text),
                "meta": {},
            })
    return segments


def ensure_segments(drawing_id: int) -> list[dict]:
    drawing = drawings_repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    built = build_segments(
        str(drawing.get("title_text") or ""),
        str(drawing.get("tech_text") or ""),
        str(drawing.get("all_text") or ""),
    )
    existing = repository.list_segments(drawing_id)
    if not existing:
        repository.create_segments(drawing_id, built)
        return repository.list_segments(drawing_id)

    existing_by_key = {row["segment_key"]: row for row in existing}
    built_keys = {segment["segment_key"] for segment in built}
    new_segments = []
    for segment in built:
        old = existing_by_key.get(segment["segment_key"])
        if old is None:
            new_segments.append(segment)
            continue
        source_changed = old["source_hash"] != segment["source_hash"]
        translatable_changed = bool(old["translatable"]) != bool(segment["translatable"])
        if source_changed or translatable_changed:
            repository.delete_translation_for_segment(old["id"])
        if (
            old["label"] != segment["label"]
            or old["source_text"] != segment["source_text"]
            or old["source_hash"] != segment["source_hash"]
            or old["translatable"] != (1 if segment["translatable"] else 0)
            or old["ordinal"] != segment["ordinal"]
        ):
            repository.update_segment(
                old["id"], segment["label"], segment["source_text"], segment["source_hash"],
                segment["translatable"], segment["ordinal"],
            )
    if new_segments:
        repository.create_segments(drawing_id, new_segments)
    repository.delete_missing_segments(drawing_id, built_keys)
    return repository.list_segments(drawing_id)
