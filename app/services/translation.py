"""Qwen-MT orchestration with glossary, token protection, and SQLite cache."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from app import config
from app.repositories import drawings as drawings_repository
from app.repositories import translations as repository
from app.services import qwen_mt, segments as segment_service
from app.services.translation_guard import protect, protected_tokens, restore, validate

LOGGER = logging.getLogger(__name__)
PROVIDER = "qwen-mt"
TARGET_LANGUAGES = {"en": "English"}
SECTIONS = ("title", "tech", "extra")
MAX_TRANSLATION_CHARS = 3000
DOMAIN_PROMPT = (
    "Mechanical engineering drawing and manufacturing notes. "
    "Use concise, standard shop-floor English. Preserve every placeholder "
    "starting with SCGPH exactly. Do not convert units, substitute standards, "
    "add explanations, or change numbers. Keep the original meaning and order."
)
def _csv_safe(value) -> str:
    text = str(value if value is not None else "")
    # Preserve ordinary signed engineering values (-0.2, +0.021 mm) while
    # neutralizing spreadsheet formulas.
    if re.fullmatch(r"[+-]\d+(?:\.\d+)?(?:\s*(?:mm|cm|m|kg|g|MPa|N|°|%|μm|um))?", text):
        return text
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


STATUS_LABELS = {
    "translated": "已翻译",
    "cached": "缓存",
    "skipped": "无需翻译",
    "needs_review": "待复核",
    "failed": "失败",
    "missing": "未翻译",
}


def _glossary_hash(terms: list[dict]) -> str:
    payload = sorted((str(term["source"]), str(term["target"])) for term in terms)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _terms_for(text: str, terms: list[dict]) -> list[dict]:
    matches = [term for term in terms if term["source"] and term["source"] in text]
    matches.sort(key=lambda term: len(term["source"]), reverse=True)
    return matches[:30]


def _source_lang(text: str) -> str:
    return "zh" if any("\u3400" <= char <= "\u9fff" for char in text) else "auto"


def _error_message(exc: Exception) -> str:
    key = config.QWEN_API_KEY or ""
    message = str(qwen_mt._api_error(exc) if hasattr(qwen_mt, "_api_error") else exc)
    if key:
        message = message.replace(key, "[已隐藏]")
    return message[:500]


def _has_target_text(source: str, translated: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", translated or ""):
        return False
    residue = translated or ""
    for token in protected_tokens(source):
        residue = residue.replace(token, " ")
    return bool(re.search(r"[A-Za-z]{2,}", residue))


def _chunk_text(text: str, limit: int = MAX_TRANSLATION_CHARS) -> list[str]:
    """Split long OCR blocks without cutting a protected placeholder."""
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[。！？；;!?])\s*|(?<=\.)\s+|\n+", text)
    chunks: list[str] = []
    current = ""
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        while len(part) > limit:
            cut = limit
            # Never split a decimal number or a protected placeholder at the
            # hard character boundary.
            while cut > 0 and cut < len(part) and part[cut - 1].isdigit() and part[cut].isdigit():
                cut -= 1
            while cut > 0 and cut < len(part) and (part[cut - 1] == "." or part[cut] == "."):
                cut -= 1
            for marker in re.finditer(r"SCGPH\d{4}", part):
                if marker.start() < cut < marker.end():
                    cut = marker.start()
                    break
            if cut <= 0:
                cut = limit
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        if not part:
            continue
        if current and len(current) + len(part) + 1 > limit:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]


def _translate_one(segment: dict, target_lang: str, glossary: list[dict]) -> dict:
    source = str(segment["source_text"])
    if not segment.get("translatable"):
        return {
            "translated_text": source,
            "status": "skipped",
            "error": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    masked, mapping = protect(source)
    terms = _terms_for(source, glossary)
    try:
        translated_parts = []
        input_tokens = 0
        output_tokens = 0
        for chunk in _chunk_text(masked):
            result = None
            for attempt in range(2):
                try:
                    result = qwen_mt.translate_text(
                        chunk,
                        target_lang=target_lang,
                        source_lang=_source_lang(source),
                        terms=terms,
                        domains=DOMAIN_PROMPT,
                    )
                    break
                except Exception as exc:
                    status = getattr(exc, "status_code", None)
                    if attempt == 0 and status in {429, 500, 502, 503, 504}:
                        time.sleep(0.8)
                        continue
                    raise
            if result is None:
                raise RuntimeError("Qwen-MT 没有返回结果")
            translated_parts.append(result["text"])
            input_tokens += int(result.get("input_tokens") or 0)
            output_tokens += int(result.get("output_tokens") or 0)
        restored, missing = restore("\n".join(translated_parts), mapping)
        issues = validate(source, restored)
        if missing:
            issues.insert(0, "译文丢失工程数据占位符：" + "、".join(missing))
        if re.search(r"[\u3400-\u9fff]", source):
            if restored.strip() == source.strip():
                issues.append("译文与原文相同，可能未翻译")
            elif not _has_target_text(source, restored):
                issues.append("译文未包含目标语言文本")
        return {
            "translated_text": restored,
            "status": "needs_review" if issues else "translated",
            "error": "；".join(issues) if issues else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    except Exception as exc:
        LOGGER.warning("Qwen-MT translation failed for segment %s: %s", segment.get("id"), _error_message(exc))
        return {
            "translated_text": "",
            "status": "failed",
            "error": _error_message(exc),
            "input_tokens": 0,
            "output_tokens": 0,
        }


def _load_glossary(target_lang: str) -> list[dict]:
    if target_lang == "en":
        return repository.list_glossary("zh", "en")
    return []


def _validate_request(target_lang: str, sections: list[str] | None) -> list[str]:
    if target_lang not in TARGET_LANGUAGES:
        raise HTTPException(400, f"暂不支持目标语言：{target_lang}")
    selected = list(sections) if sections else list(SECTIONS)
    if any(section not in SECTIONS for section in selected):
        raise HTTPException(400, "翻译分区无效")
    return selected


def translate_drawing(
    drawing_id: int,
    target_lang: str = "en",
    force: bool = False,
    sections: list[str] | None = None,
) -> dict:
    selected_sections = _validate_request(target_lang, sections)
    drawing = drawings_repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    segment_service.ensure_segments(drawing_id)
    all_segments = repository.list_segments(drawing_id)
    glossary = _load_glossary(target_lang)
    glossary_hash = _glossary_hash(glossary)
    candidates = [segment for segment in all_segments if segment["section"] in selected_sections]

    def cached_for(segment: dict) -> dict | None:
        cached = repository.get_cached(segment["id"], target_lang, PROVIDER)
        if not cached or force:
            return None
        if cached["source_hash"] != segment["source_hash"]:
            return None
        if cached["glossary_hash"] != glossary_hash:
            return None
        if cached.get("model") != config.QWEN_MT_MODEL:
            return None
        if cached["status"] not in {"translated", "skipped", "needs_review"}:
            return None
        return cached

    pending: list[dict] = []
    for segment in candidates:
        cached = cached_for(segment)
        if cached:
            continue
        pending.append(segment)

    # A skipped segment is cheap and deterministic; store it alongside real
    # translations so the export and summary have one row per segment.
    skipped = [segment for segment in pending if not segment.get("translatable")]
    to_translate = [segment for segment in pending if segment.get("translatable")]

    if to_translate and (
        not config.QWEN_API_KEY
        or config.QWEN_API_KEY.strip().lower() in config.PLACEHOLDER_VALUES
    ):
        raise HTTPException(503, "请先在右上角“处理设置”中填写 Qwen API Key")

    new_ids: set[int] = set()
    for segment in skipped:
        repository.upsert_translation(
            segment["id"], target_lang, config.QWEN_MT_MODEL,
            str(segment["source_text"]), "skipped", segment["source_hash"],
            glossary_hash, provider=PROVIDER,
        )
        new_ids.add(segment["id"])

    if to_translate:
        with ThreadPoolExecutor(max_workers=config.TRANSLATION_MAX_WORKERS) as pool:
            futures = {
                segment["id"]: pool.submit(_translate_one, segment, target_lang, glossary)
                for segment in to_translate
            }
            for segment in to_translate:
                result = futures[segment["id"]].result()
                repository.upsert_translation(
                    segment["id"], target_lang, config.QWEN_MT_MODEL,
                    result["translated_text"], result["status"], segment["source_hash"],
                    glossary_hash, provider=PROVIDER, error=result["error"],
                    input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
                )
                new_ids.add(segment["id"])
    return get_translation_payload(drawing_id, target_lang, new_ids=new_ids)


def get_translation_payload(drawing_id: int, target_lang: str = "en", new_ids: set[int] | None = None) -> dict:
    _validate_request(target_lang, None)
    drawing = drawings_repository.get(drawing_id)
    if not drawing:
        raise HTTPException(404, "未找到图纸")
    segment_service.ensure_segments(drawing_id)
    glossary = _load_glossary(target_lang)
    glossary_hash = _glossary_hash(glossary)
    rows = repository.list_rows(drawing_id, target_lang, PROVIDER)
    output = []
    summary = {key: 0 for key in ("total", "translated", "cached", "skipped", "needs_review", "failed", "missing")}
    input_tokens = 0
    output_tokens = 0
    for row in rows:
        stored_status = row.get("translation_status")
        source_hash = row["source_hash"]
        has_translation = bool(stored_status)
        if has_translation and row.get("translation_source_hash") != source_hash:
            has_translation = False
        if has_translation and row.get("glossary_hash") != glossary_hash:
            has_translation = False
        if has_translation and row.get("model") != config.QWEN_MT_MODEL:
            has_translation = False
        if not has_translation:
            status = "missing"
            translated = ""
            error = None
            updated_at = None
        else:
            translated = row.get("translated_text") or ""
            error = row.get("error")
            updated_at = row.get("updated_at")
            status = stored_status
            if status == "translated" and (new_ids is None or row["id"] not in new_ids):
                status = "cached"
        summary["total"] += 1
        summary[status] = summary.get(status, 0) + 1
        input_tokens += int(row.get("input_tokens") or 0)
        output_tokens += int(row.get("output_tokens") or 0)
        output.append({
            "id": row["id"],
            "segment_key": row["segment_key"],
            "section": row["section"],
            "section_label": segment_service.SECTION_LABELS.get(row["section"], row["section"]),
            "label": row["label"],
            "source_text": row["source_text"],
            "translated_text": translated,
            "status": status,
            "error": error,
            "updated_at": updated_at,
        })
    return {
        "drawing_id": drawing_id,
        "filename": str(drawing.get("filename") or ""),
        "target_lang": target_lang,
        "provider": PROVIDER,
        "model": config.QWEN_MT_MODEL,
        "segments": output,
        "summary": summary,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def export_csv(drawing_id: int, target_lang: str = "en") -> tuple[str, str]:
    payload = get_translation_payload(drawing_id, target_lang)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["序号", "区域", "位置/字段", "原文", "译文", "状态"])
    for index, segment in enumerate(payload["segments"], 1):
        writer.writerow([
            index,
            segment["section_label"],
            segment["label"],
            _csv_safe(segment["source_text"]),
            _csv_safe(segment["translated_text"]),
            STATUS_LABELS.get(segment["status"], segment["status"]),
        ])
    filename = f"{payload['filename']}.zh-{target_lang}.csv"
    return "\ufeff" + buffer.getvalue(), filename
