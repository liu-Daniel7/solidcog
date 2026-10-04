import json
import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi import HTTPException
from app import config
from app.services import images, model_scheduler


class _TableTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"tr", "br"}:
            self.parts.append("\n")
        elif tag in {"td", "th"} and self.parts and self.parts[-1] not in {"\n", " | "}:
            self.parts.append(" | ")

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self):
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]*\|[ \t]*", " | ", joined)
        return re.sub(r"\n{2,}", "\n", joined).strip(" \n|")


def _html_text(value: str) -> str:
    parser = _TableTextParser()
    parser.feed(value)
    return parser.text()


def _plain_markdown(markdown: str) -> str:
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", markdown)
    text = re.sub(
        r"<table\b.*?</table>",
        lambda match: "\n" + _html_text(match.group(0)) + "\n",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r" {2,}\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _block_text(value) -> str:
    """Collect readable text from MinerU's varying content-list shapes."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_block_text(item) for item in value)))
    if not isinstance(value, dict):
        return ""

    direct = []
    for key in ("text", "content", "md_content", "markdown", "html"):
        item = value.get(key)
        if isinstance(item, (str, list)):
            text = _block_text(item)
            if text and text not in direct:
                direct.append(text)
    if direct:
        return "\n".join(direct)
    for key in ("blocks", "content_list", "children", "layout", "result"):
        if key in value:
            text = _block_text(value[key])
            if text:
                return text
    return ""


def _all_markdown(raw: dict, markdown: str) -> str:
    content = markdown.strip()
    additions = []
    for page in _pages(raw):
        for block in page:
            text = _block_text(block)
            if text and text not in content and text not in additions:
                additions.append(text)
    return "\n\n".join(filter(None, [content, *additions]))


def _tech_block(markdown: str) -> str:
    lines = markdown.splitlines()
    heading = re.compile(r"^\s{0,3}#{0,6}\s*(?:技术要求|技术条件|工艺要求|技术说明)\s*[:：]?\s*(.*)$", re.I)
    other_heading = re.compile(r"^\s{0,3}#{1,6}\s+.+$")
    sections = []
    for index, line in enumerate(lines):
        match = heading.match(line)
        if not match:
            continue
        body = [match.group(1)] if match.group(1).strip() else []
        saw_content = bool(body)
        blank_after_content = False
        for following in lines[index + 1:]:
            if other_heading.match(following) or re.match(r"\s*<table\b", following, re.I):
                break
            if not following.strip():
                blank_after_content = saw_content
                body.append(following)
                continue
            if blank_after_content and not re.match(r"\s*(?:\d+[.、)]|[-*])", following):
                break
            body.append(following)
            saw_content = True
            blank_after_content = False
        result = _plain_markdown("\n".join(body))
        if result:
            sections.append(result)
    if sections:
        return "\n\n".join(sections)

    # MinerU may label the region directly in content-list blocks instead of Markdown headings.
    match = re.search(r"(?is)(?:技术要求|技术条件|工艺要求|技术说明)\s*[:：]?\s*(.{1,3000}?)(?=\n\s*(?:标题栏|图纸名称|备注|说明)\s*[:：]|<table\b|\Z)", markdown)
    return _plain_markdown(match.group(1)) if match else ""


def _bbox(block: dict) -> tuple[float, float, float, float] | None:
    box = block.get("bbox")
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        return None
    try:
        values = tuple(float(value) for value in box)
    except (TypeError, ValueError):
        return None
    return values


def _pages(raw: dict) -> list[list[dict]]:
    content = raw.get("content_list") or raw.get("blocks") or []
    if isinstance(content, dict):
        content = content.get("pages") or content.get("content_list") or content.get("blocks") or []
    if not isinstance(content, list):
        return []
    if content and all(isinstance(item, dict) and any(key in item for key in ("blocks", "content_list", "layout")) for item in content):
        return [
            item.get("blocks") or item.get("content_list") or item.get("layout") or []
            for item in content
        ]
    if content and all(isinstance(item, dict) for item in content):
        grouped = {}
        order = []
        for block in content:
            page_id = block.get("page_idx", block.get("page", 0))
            if page_id not in grouped:
                grouped[page_id] = []
                order.append(page_id)
            grouped[page_id].append(block)
        return [grouped[page_id] for page_id in order]
    pages = []
    for page in content:
        if isinstance(page, list):
            pages.append(page)
        elif isinstance(page, dict):
            blocks = page.get("blocks") or page.get("content_list") or page.get("layout") or []
            if isinstance(blocks, list):
                pages.append(blocks)
    return pages


def _title_block(markdown: str, raw: dict | None = None) -> str:
    pages = _pages(raw or {})
    located_tables = []
    has_located_tables = False
    title_field_patterns = (
        r"图号|图纸编号|DRAWING\s*NO",
        r"图名|零件名称|DRAWING\s*NAME",
        r"材料|材质|MATERIAL",
        r"比例|SCALE",
        r"版次|REV\.",
    )
    engineering_id = re.compile(r"\b[A-Z]{1,8}(?:-[A-Z0-9]{1,12}){2,}\b", re.I)
    material_field = re.compile(r"材料|材质|MATERIAL", re.I)

    def is_title(text: str, box) -> bool:
        matches = sum(bool(re.search(pattern, text, re.I)) for pattern in title_field_patterns)
        if engineering_id.search(text) and material_field.search(text):
            matches = max(matches, 2)
        # Fields are the primary signal; bbox only helps resolve ambiguous tables.
        return matches >= 2 or (matches >= 1 and box is not None and box[1] >= 0.55)

    for page in pages:
        for block in page:
            if str(block.get("type") or "").lower() not in {"table", "table_body"}:
                continue
            has_located_tables = True
            box = _bbox(block)
            text = _block_text(block)
            if not text:
                continue
            if is_title(text, box):
                plain = _html_text(text) if "<table" in text.lower() else _plain_markdown(text)
                score = sum(bool(re.search(pattern, text, re.I)) for pattern in title_field_patterns)
                score += 2 if engineering_id.search(text) and material_field.search(text) else 0
                area = (box[2] - box[0]) * (box[3] - box[1]) if box else 0
                located_tables.append((page, box, plain, score, len(plain), area))
    if has_located_tables:
        selected = []
        for candidate in located_tables:
            page, box, _, _, _, _ = candidate
            if box is None:
                selected.append(candidate)
                continue
            overlaps = []
            for other in located_tables:
                if other is candidate or other[0] is not page or other[1] is None:
                    continue
                other_box = other[1]
                intersection = max(0, min(box[2], other_box[2]) - max(box[0], other_box[0])) * max(
                    0, min(box[3], other_box[3]) - max(box[1], other_box[1])
                )
                smaller_area = min(candidate[5], other[5])
                if smaller_area and intersection / smaller_area >= 0.45:
                    overlaps.append(other)
            cluster = [candidate, *overlaps]
            if candidate is max(cluster, key=lambda item: (item[3], item[4])):
                selected.append(candidate)
        return "\n\n".join(item[2] for item in selected)

    tables = re.findall(r"<table\b.*?</table>", markdown, flags=re.I | re.S)
    title_tables = []
    for table in tables:
        plain = _html_text(table)
        matches = sum(bool(re.search(pattern, plain, re.I)) for pattern in title_field_patterns)
        if engineering_id.search(plain) and material_field.search(plain):
            matches = max(matches, 2)
        if matches >= 2:
            title_tables.append(plain)
    table_text = "\n\n".join(title_tables)
    if tables:
        return table_text

    lines = [line.strip() for line in _plain_markdown(markdown).splitlines() if line.strip()]
    key = re.compile(r"(?:图号|图纸编号|零件名称|名称|材料|材质|比例|重量|设计|审核|批准|日期)\s*[:：|]")
    selected = [line for line in lines if key.search(line)]
    return "\n".join(selected)


def _tech_from_pages(raw: dict) -> str:
    collected = []
    for page in _pages(raw):
        blocks = [(block, _bbox(block), _block_text(block)) for block in page]
        for index, (block, box, text) in enumerate(blocks):
            if not re.search(r"(?:技术要求|技术条件|工艺要求|技术说明)", text):
                continue
            heading_x = box[0] if box else None
            remainder = re.sub(r"(?is)^.*?(?:技术要求|技术条件|工艺要求|技术说明)\s*[:：]?", "", text).strip()
            pieces = [remainder] if remainder else []
            last_y = box[3] if box else None
            last_bottom = box[2] if box else None
            for candidate, candidate_box, candidate_text in blocks[index + 1:]:
                kind = str(candidate.get("type") or "").lower()
                if not candidate_text or kind not in {"text", "list", "list_item", "image_footnote"}:
                    continue
                if candidate_box and heading_x is not None:
                    if candidate_box[1] > 0.95:
                        break
                    if abs(candidate_box[0] - heading_x) > 0.22 or candidate_box[1] < (last_y or 0) - 0.01:
                        if pieces:
                            break
                        continue
                    # A drawing/view label on another column ends the requirements region.
                    if last_bottom is not None and candidate_box[0] > last_bottom + 0.08:
                        break
                elif pieces and not re.match(r"\s*(?:\d+[.、)]|[-*])", candidate_text):
                    break
                pieces.append(candidate_text)
                if candidate_box:
                    last_y = candidate_box[3]
            value = _plain_markdown("\n".join(pieces))
            if value:
                collected.append(value)
    return "\n\n".join(collected)


def _layout(path: Path) -> str:
    try:
        page = images.load_pages(path, dpi=72, limit=1)[0]
        try:
            return "horizontal" if page.width >= page.height else "vertical"
        finally:
            if hasattr(page, "close"):
                page.close()
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def _first_result(payload: dict) -> tuple[str, dict]:
    results = payload.get("results")
    if not isinstance(results, dict) or not results:
        raise HTTPException(502, "MinerU 没有返回解析结果")
    name, result = next(iter(results.items()))
    if not isinstance(result, dict):
        raise HTTPException(502, "MinerU 返回的结果格式无效")
    return str(name), result


def run(path: Path) -> dict:
    payload = model_scheduler.parse_with_mineru(path)
    name, raw = _first_result(payload)
    markdown = _all_markdown(raw, str(raw.get("md_content") or ""))
    if not markdown.strip():
        raise HTTPException(502, "MinerU 未识别出可用文字")

    config.MINERU_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = config.MINERU_RESULT_DIR / f"{path.stem}.json"
    artifact.write_text(
        json.dumps({"source": name, "backend": payload.get("backend"), **raw}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pages_processed = raw.get("pages_processed") or payload.get("pages_processed") or len(_pages(raw)) or 1
    page_errors = raw.get("page_errors") or payload.get("page_errors") or []
    return {
        "title_block": _title_block(markdown, raw),
        "tech_block": _tech_block(markdown) or _tech_from_pages(raw),
        "all_text": _plain_markdown(markdown),
        "layout": _layout(path),
        "backend": "mineru_vlm",
        "model": "MinerU2.5-Pro-2605-1.2B",
        "pages_processed": pages_processed,
        "page_errors": page_errors,
        "artifact": str(artifact),
    }
