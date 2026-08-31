import json
import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app import config
from app.services import model_scheduler


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


def _tech_block(markdown: str) -> str:
    match = re.search(
        r"(?ims)^#{1,6}\s*技术要求\s*$\s*(.*?)(?=^#{1,6}\s|<table\b|\Z)", markdown
    )
    return _plain_markdown(match.group(1)) if match else ""


def _title_block(markdown: str) -> str:
    tables = re.findall(r"<table\b.*?</table>", markdown, flags=re.I | re.S)
    return "\n\n".join(filter(None, (_html_text(table) for table in tables)))


def _layout(path: Path) -> str:
    try:
        if path.suffix.lower() == ".png":
            with Image.open(path) as image:
                return "horizontal" if image.width >= image.height else "vertical"
    except OSError:
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
    markdown = str(raw.get("md_content") or "")
    if not markdown.strip():
        raise HTTPException(502, "MinerU 未识别出可用文字")

    config.MINERU_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = config.MINERU_RESULT_DIR / f"{path.stem}.json"
    artifact.write_text(
        json.dumps({"source": name, "backend": payload.get("backend"), **raw}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "title_block": _title_block(markdown),
        "tech_block": _tech_block(markdown),
        "all_text": _plain_markdown(markdown),
        "layout": _layout(path),
        "backend": "mineru_vlm",
        "model": "MinerU2.5-Pro-2605-1.2B",
        "pages_processed": 1,
        "page_errors": [],
        "artifact": str(artifact),
    }
