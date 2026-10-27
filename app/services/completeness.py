"""Completeness-check orchestration.

The checker is deterministic and evidence-backed. It consumes the OCR already
stored with each drawing, normalizes available table structure, and never calls
an LLM to decide that a whole submission is complete.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from app import config
from app.repositories import completeness as repository
from app.repositories import drawings as drawings_repository
from app.repositories import translations as translations_repository
from app.services import completeness_ocr as ocr
from app.services import dimensions, images, segments
from app.services.completeness_rules import (
    BOM_COLUMN_CATALOG,
    TITLE_FIELD_CATALOG,
    normalize_rules,
)

LOGGER = logging.getLogger(__name__)

STATUS_LABELS = {
    "passed": "已通过",
    "missing": "明确缺失",
    "pending": "待确认",
    "failed": "检查失败",
}
STATUS_ORDER = ("passed", "missing", "pending", "failed")
KIND_LABELS = {
    "reference": "图纸引用",
    "reference_scan": "明细表/递归",
    "title_field": "标题栏字段",
    "title_scan": "标题栏结构",
    "bom_cell": "明细表单元格",
    "bom_table": "明细表结构",
    "dimension_candidate": "疑似漏尺寸",
    "dimension_scan": "尺寸标注复核",
    "ocr": "OCR 证据",
    "run": "检查运行",
}
CONFIRMATION_LABELS = {
    "unconfirmed": "未确认",
    "confirmed": "已确认有效",
    "dismissed": "已排除",
}
BATCH_STATUS_LABELS = {
    "draft": "待检查",
    "running": "检查中",
    "completed": "已完成",
    "stale": "结果已失效",
    "failed": "检查失败",
}
_SCOPE_TEMPLATE = "仅检查本批次选入的 {count} 张图纸；图纸库中未选入的文件不参与检查或匹配。"


class BatchRunningError(RuntimeError):
    pass


class CompletenessRunError(RuntimeError):
    pass


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_text(value, limit: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text[:limit]


def _normalize_name(value: str) -> str:
    name = re.sub(r"\s+", " ", _safe_text(value, 160)).strip()
    return name or f"齐套性检查 {_now_text()[:16]}"


def _drawing_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValueError("图纸编号格式无效")
        if parsed <= 0:
            raise ValueError("图纸编号格式无效")
        if parsed not in result:
            result.append(parsed)
    return result


def validate_creation(
    name: str,
    drawing_ids,
    start_drawing_ids,
    rules_payload: dict | None,
) -> dict:
    selected = _drawing_ids(drawing_ids)
    if not selected:
        raise ValueError("请至少选择一张图纸，本批次不会默认包含整个图纸库")
    if len(selected) > config.MAX_COMPLETENESS_DRAWINGS:
        raise ValueError(f"单个批次最多选入 {config.MAX_COMPLETENESS_DRAWINGS} 张图纸")
    missing = [drawing_id for drawing_id in selected if not drawings_repository.get(drawing_id)]
    if missing:
        raise ValueError("部分图纸不存在或已被删除：" + ", ".join(map(str, missing)))
    starts = _drawing_ids(start_drawing_ids)
    if any(drawing_id not in selected for drawing_id in starts):
        raise ValueError("起始图必须来自本批次已选图纸")
    rules = normalize_rules(rules_payload)
    return {
        "name": _normalize_name(name),
        "drawing_ids": selected,
        "start_drawing_ids": starts,
        "rules": rules,
        "scope_note": _SCOPE_TEMPLATE.format(count=len(selected)),
    }


def create_batch(
    name: str,
    drawing_ids,
    start_drawing_ids=None,
    rules_payload: dict | None = None,
) -> int:
    payload = validate_creation(name, drawing_ids, start_drawing_ids, rules_payload)
    return repository.create_batch(
        name=payload["name"],
        drawing_ids=payload["drawing_ids"],
        start_drawing_ids=payload["start_drawing_ids"],
        rules=payload["rules"],
        scope_note=payload["scope_note"],
    )


def _decode_batch(batch: dict) -> dict:
    result = dict(batch)
    try:
        rules = json.loads(result.get("rules_json") or "{}")
        result["rules"] = normalize_rules(rules)
    except (ValueError, TypeError, json.JSONDecodeError):
        result["rules"] = normalize_rules({})
    try:
        starts = json.loads(result.get("start_drawing_ids_json") or "[]")
        result["start_drawing_ids"] = [int(item) for item in starts if int(item) > 0]
    except (ValueError, TypeError, json.JSONDecodeError):
        result["start_drawing_ids"] = []
    result["status_label"] = BATCH_STATUS_LABELS.get(
        result.get("status"), str(result.get("status") or "")
    )
    return result


def list_batches_payload() -> dict:
    batches = []
    for row in repository.list_batches():
        item = _decode_batch(row)
        item["summary"] = row.get("summary") or {
            "passed": 0,
            "missing": 0,
            "pending": 0,
            "failed": 0,
            "total": 0,
        }
        batches.append(item)
    return {
        "batches": batches,
        "status_labels": STATUS_LABELS,
        "batch_status_labels": BATCH_STATUS_LABELS,
    }


def _display_title(drawing: dict) -> str:
    try:
        title = segments.extract_title(
            str(drawing.get("title_text") or ""),
            str(drawing.get("all_text") or ""),
        )
        return title or str(drawing.get("filename") or "")
    except Exception:
        return str(drawing.get("filename") or "")


def batch_payload(batch_id: int) -> dict:
    raw_batch = repository.get_batch(batch_id)
    if not raw_batch:
        raise KeyError(batch_id)
    batch = _decode_batch(raw_batch)
    members = repository.get_members(batch_id)
    for member in members:
        member["display_title"] = _display_title(member)
    results = repository.list_results(batch_id)
    for result in results:
        result["status_label"] = STATUS_LABELS.get(result["status"], result["status"])
        result["kind_label"] = KIND_LABELS.get(result["kind"], result["kind"])
        result["confirmation_label"] = CONFIRMATION_LABELS.get(
            result.get("confirmed_status"), ""
        )
        result["location"] = result_location(result)
    summary = repository.get_summary(batch_id)
    review = {"unconfirmed": 0, "confirmed": 0, "dismissed": 0}
    for result in results:
        status = result.get("confirmed_status") or "unconfirmed"
        if status in review:
            review[status] += 1
    return {
        "batch": batch,
        "members": members,
        "results": results,
        "summary": summary,
        "review": review,
        "drawing_summaries": repository.list_result_drawings(batch_id),
        "status_labels": STATUS_LABELS,
        "status_order": STATUS_ORDER,
        "kind_labels": KIND_LABELS,
        "confirmation_labels": CONFIRMATION_LABELS,
        "batch_status_labels": BATCH_STATUS_LABELS,
        "title_catalog": TITLE_FIELD_CATALOG,
        "bom_catalog": BOM_COLUMN_CATALOG,
    }


def result_location(result: dict) -> str:
    parts = []
    if result.get("page_number"):
        parts.append(f"第 {result['page_number']} 页")
    if result.get("table_index"):
        parts.append(f"明细表 {result['table_index']}")
    if result.get("row_index"):
        parts.append(f"第 {result['row_index']} 行")
    if result.get("column_index"):
        parts.append(f"第 {result['column_index']} 列")
    if result.get("field_name"):
        parts.append(str(result["field_name"]))
    return " / ".join(parts) or "批次级结果"


def update_rules(batch_id: int, payload: dict) -> int:
    batch = repository.get_batch(batch_id)
    if not batch:
        raise KeyError(batch_id)
    if batch["status"] == "running":
        raise BatchRunningError("检查正在进行，不能修改规则")
    try:
        rules = normalize_rules(payload)
    except ValueError:
        raise
    return repository.update_rules(batch_id, rules)


def _drawing_number_from_legacy_segments(drawing: dict) -> tuple[str, dict]:
    """Use the existing 图号 translation segment when title OCR is ambiguous."""
    try:
        for segment in translations_repository.list_segments(int(drawing["id"])):
            label = str(segment.get("label") or "")
            if not any(alias in label for alias in ("图号", "图纸编号", "代号", "DRAWING")):
                continue
            numbers = ocr.extract_drawing_numbers(
                str(segment.get("source_text") or ""), allow_numeric=True
            )
            if numbers:
                return numbers[0], {
                    "value": numbers[0],
                    "explicit": True,
                    "source": "drawing_segments",
                    "line": label,
                }
    except Exception:
        LOGGER.debug("Unable to read legacy drawing-number segment", exc_info=True)
    return "", {}


def _title_text_for(drawing: dict, pages: list[dict]) -> str:
    title = str(drawing.get("title_text") or "").strip()
    if title:
        return title
    candidates = []
    for page in pages:
        for block in page.get("blocks") or []:
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            parsed = ocr.parse_title_fields(text)
            if parsed["recognized_labels"] >= 2:
                candidates.append((parsed["recognized_labels"], len(text), text))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2]
    return ""


def _result(
    kind: str,
    status: str,
    title: str,
    *,
    drawing: dict | None = None,
    page_number: int | None = None,
    table_index: int | None = None,
    row_index: int | None = None,
    column_index: int | None = None,
    field_name: str = "",
    field_value: str = "",
    target: dict | None = None,
    bbox=None,
    confidence: float | None = None,
    detail: str = "",
    evidence: dict | None = None,
    code: str = "",
    reference_index: int | None = None,
) -> dict:
    return {
        "kind": kind,
        "code": code,
        "status": status,
        "title": _safe_text(title, 300),
        "detail": _safe_text(detail, 3000),
        "source_drawing_id": drawing.get("id") if drawing else None,
        "source_filename": str(drawing.get("filename") or "") if drawing else "",
        "page_number": page_number,
        "table_index": table_index,
        "row_index": row_index,
        "column_index": column_index,
        "field_name": _safe_text(field_name, 120),
        "field_value": _safe_text(field_value, 500),
        "target_drawing_id": target.get("id") if target else None,
        "target_filename": str(target.get("filename") or "") if target else "",
        "bbox": bbox,
        "confidence": confidence,
        "evidence": evidence or {},
        "reference_index": reference_index,
    }


def _title_results(
    drawing: dict,
    pages: list[dict],
    title_text: str,
    rules: dict,
) -> list[dict]:
    results = []
    parsed = ocr.parse_title_fields(title_text)
    table_parsed = ocr.parse_title_fields_from_pages(pages)
    if table_parsed["fields"]:
        parsed["fields"] = {**parsed["fields"], **table_parsed["fields"]}
        parsed["recognized_labels"] = max(
            parsed["recognized_labels"], table_parsed["recognized_labels"]
        )
        parsed["quality"] = "high"
    region = ocr.title_region(pages)
    if table_parsed.get("bbox") and table_parsed.get("page_number"):
        region = {
            **region,
            "page_number": table_parsed["page_number"],
            "bbox": table_parsed["bbox"],
        }
    required_keys = rules.get("required_title_fields") or []
    if not required_keys:
        return results
    if not title_text.strip():
        results.append(
            _result(
                "title_scan",
                "pending",
                "标题栏未识别，无法判断必填字段",
                drawing=drawing,
                page_number=region["page_number"],
                bbox=region.get("bbox"),
                confidence=0.3,
                detail="该图纸没有可用的标题栏 OCR 文本。不能把无法读取误判为字段缺失，需人工确认。",
                evidence={"scope": "title_block", "snippet": region.get("snippet", "")},
                code="title_unreadable",
            )
        )
        return results
    labels = {
        item["key"]: item["label"] for item in rules.get("title_fields") or []
    }
    quality = parsed["quality"]
    for key in required_keys:
        label = labels.get(key, key)
        field = parsed["fields"].get(key)
        evidence = {
            "scope": "title_block",
            "field": label,
            "title_quality": quality,
            "recognized_labels": parsed["recognized_labels"],
            "snippet": (field or {}).get("line") or region.get("snippet", ""),
            "page_snippet": region.get("snippet", ""),
            "source": "saved_title_ocr",
        }
        if field and str(field.get("value") or "").strip():
            explicit = bool(field.get("explicit"))
            results.append(
                _result(
                    "title_field",
                    "passed" if explicit else "pending",
                    (
                        f"标题栏“{label}”已填写"
                        if explicit
                        else f"标题栏“{label}”需人工确认"
                    ),
                    drawing=drawing,
                    page_number=region["page_number"],
                    field_name=label,
                    field_value=field["value"],
                    bbox=region.get("bbox"),
                    confidence=0.95 if explicit else 0.62,
                    detail=(
                        "在标题栏 OCR 中识别到该字段标签和相邻非空值。"
                        if explicit
                        else "从版式/模式推断出该值，但未定位到明确的字段标签与值关系，需结合原图人工确认。"
                    ),
                    evidence={**evidence, "extraction": field.get("source")},
                    code="title_present" if explicit else "title_inferred",
                )
            )
        elif ocr.field_label_present(title_text, key):
            results.append(
                _result(
                    "title_field",
                    "missing",
                    f"标题栏必填字段“{label}”为空",
                    drawing=drawing,
                    page_number=region["page_number"],
                    field_name=label,
                    bbox=region.get("bbox"),
                    confidence=0.82 if quality in {"high", "medium"} else 0.62,
                    detail="标题栏 OCR 中出现了字段标签，但未读取到相邻值。请对照原图确认。",
                    evidence=evidence,
                    code="title_empty",
                )
            )
        else:
            results.append(
                _result(
                    "title_field",
                    "pending",
                    f"标题栏“{label}”待确认",
                    drawing=drawing,
                    page_number=region["page_number"],
                    field_name=label,
                    bbox=region.get("bbox"),
                    confidence=0.42,
                    detail=(
                        "未在标题栏 OCR 中定位到该字段标签或值。可能是漏填，也可能是标题栏结构/文字未被识别，"
                        "不能据此判定通过或缺失。"
                    ),
                    evidence=evidence,
                    code="title_unknown",
                )
            )
    return results


def _bom_results(drawing: dict, pages: list[dict], rules: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Return BOM cell results, parsed reference candidates, detected tables."""
    results: list[dict] = []
    tables = ocr.bom_tables(pages)
    references = ocr.extract_reference_candidates(pages)
    required_keys = rules.get("bom_required_columns") or []
    label_by_key = {item["key"]: item["label"] for item in BOM_COLUMN_CATALOG}
    if not tables or not required_keys:
        return results, references, tables
    for table in tables:
        review, reason = ocr.table_requires_review(table)
        table_human = int(table["table_index"]) + 1
        base_evidence = {
            "scope": "detail_table",
            "table_index": table_human,
            "table_source": table.get("source"),
            "table_confidence": table.get("confidence"),
            "header": ocr.table_header_labels(table),
            "row_count": table.get("row_count"),
            "plain_text": str(table.get("plain_text") or "")[:2000],
        }
        if review:
            results.append(
                _result(
                    "bom_table",
                    "pending",
                    f"明细表 {table_human} 结构需人工复核",
                    drawing=drawing,
                    page_number=table.get("page_number"),
                    table_index=table_human,
                    bbox=table.get("bbox"),
                    confidence=table.get("confidence"),
                    detail=reason + "，因此未把单元格空白自动判定为缺失。",
                    evidence=base_evidence,
                    code="bom_low_confidence",
                )
            )
            continue
        missing_headers = [
            key for key in required_keys if key not in table["columns"]
        ]
        empty_count = 0
        for key in required_keys:
            label = label_by_key.get(key, key)
            column = table["columns"].get(key)
            if column is None:
                results.append(
                    _result(
                        "bom_table",
                        "pending",
                        f"明细表 {table_human} 未识别到必填列“{label}”",
                        drawing=drawing,
                        page_number=table.get("page_number"),
                        table_index=table_human,
                        field_name=label,
                        bbox=table.get("bbox"),
                        confidence=table.get("confidence"),
                        detail="规则要求该列必填，但表头中未识别到。可能是客户表头不同、合并单元格或 OCR 缺失，需人工确认。",
                        evidence={**base_evidence, "required_column": label},
                        code="bom_header_unknown",
                    )
                )
                continue
            for row in table["data_rows"]:
                cells = row["cells"]
                value = str(cells[column] if column < len(cells) else "").strip()
                if value:
                    continue
                empty_count += 1
                results.append(
                    _result(
                        "bom_cell",
                        "missing",
                        f"明细表第 {int(row['row']) + 1} 行“{label}”为空",
                        drawing=drawing,
                        page_number=table.get("page_number"),
                        table_index=table_human,
                        row_index=int(row["row"]) + 1,
                        column_index=int(column) + 1,
                        field_name=label,
                        bbox=table.get("bbox"),
                        confidence=table.get("confidence"),
                        detail=(
                            f"规则要求“{label}”必填。该单元格在已识别的明细表结构中没有文字；"
                            "这是存在性检查，不判断应填内容是否正确。"
                        ),
                        evidence={
                            **base_evidence,
                            "row_cells": cells,
                            "row_text": " | ".join(cells),
                            "column": label,
                        },
                        code="bom_empty_cell",
                    )
                )
        if not missing_headers and empty_count == 0:
            results.append(
                _result(
                    "bom_table",
                    "passed",
                    f"明细表 {table_human} 必填单元格已填写",
                    drawing=drawing,
                    page_number=table.get("page_number"),
                    table_index=table_human,
                    bbox=table.get("bbox"),
                    confidence=table.get("confidence"),
                    detail=(
                        f"已逐行检查 {len(table['data_rows'])} 行 × {len(required_keys)} 个必填列，"
                        "未发现空白单元格。未检查数值、文字内容或数量正确性。"
                    ),
                    evidence=base_evidence,
                    code="bom_passed",
                )
            )
        elif missing_headers:
            # The per-header pending result above is the actionable item.
            pass
    return results, references, tables


def _reference_scan_result(
    drawing: dict,
    tables: list[dict],
    references: list[dict],
) -> dict:
    if not tables:
        return _result(
            "reference_scan",
            "pending",
            "未识别到明细表，无法确认无下级图纸",
            drawing=drawing,
            confidence=0.35,
            detail=(
                "当前 OCR 证据中没有可确认的明细表结构。未识别到明细表不等于确认没有下级引用；"
                "请在原图上人工确认是否需要继续递归。"
            ),
            evidence={"scope": "reference_scan", "tables": 0},
            code="no_bom_table",
        )
    low_confidence = [table for table in tables if ocr.table_requires_review(table)[0]]
    if low_confidence and len(low_confidence) == len(tables):
        return _result(
            "reference_scan",
            "pending",
            "明细表引用需人工确认",
            drawing=drawing,
            confidence=min(
                [float(table.get("confidence") or 0) for table in low_confidence] or [0.4]
            ),
            detail="明细表结构置信度不足，不能自动确认下级引用是否完整。",
            evidence={"scope": "reference_scan", "tables": len(tables)},
            code="reference_low_confidence",
        )
    drawing_no_tables = [
        table for table in tables if table["columns"].get("drawing_no") is not None
    ]
    if not drawing_no_tables:
        return _result(
            "reference_scan",
            "pending",
            "明细表未识别到图号/代号列",
            drawing=drawing,
            confidence=0.42,
            detail="无法从明细表提取下级图号，不能确认是否存在未提交的下级图纸。",
            evidence={
                "scope": "reference_scan",
                "tables": len(tables),
                "headers": [ocr.table_header_labels(table) for table in tables],
            },
            code="no_drawing_no_column",
        )
    nonempty_codes = 0
    empty_codes = 0
    for table in drawing_no_tables:
        column = table["columns"]["drawing_no"]
        for row in table["data_rows"]:
            cells = row["cells"]
            if column < len(cells) and str(cells[column]).strip():
                nonempty_codes += 1
            else:
                empty_codes += 1
    if nonempty_codes == 0:
        return _result(
            "reference_scan",
            "pending",
            "明细表图号/代号列全部为空",
            drawing=drawing,
            confidence=0.5,
            detail="图号列没有任何可读取值，无法确认是否存在下级图纸；空单元格本身已按规则单独报告。",
            evidence={"scope": "reference_scan", "tables": len(tables)},
            code="empty_drawing_numbers",
        )
    if empty_codes:
        return _result(
            "reference_scan",
            "pending",
            "明细表图号/代号列存在空值",
            drawing=drawing,
            confidence=0.62,
            detail=(
                f"图号列有 {empty_codes} 行为空。已填写的图号会继续匹配，但空行可能隐藏下级引用，"
                "不能据此确认引用已完整；请人工核对原明细表。"
            ),
            evidence={
                "scope": "reference_scan",
                "tables": len(tables),
                "empty_drawing_number_rows": empty_codes,
                "nonempty_drawing_number_rows": nonempty_codes,
            },
            code="partial_drawing_numbers",
        )
    if not references:
        return _result(
            "reference_scan",
            "passed",
            "无需继续递归",
            drawing=drawing,
            confidence=min(
                [float(table.get("confidence") or 0) for table in drawing_no_tables]
                or [0.7]
            ),
            detail=(
                "已识别明细表和图号列，其中没有可匹配为图纸的图号（标准件代号已排除）。"
                "未发现需要继续递归的下级图纸。"
            ),
            evidence={"scope": "reference_scan", "tables": len(tables)},
            code="no_child_reference",
        )
    unique = len(
        {
            (reference["page_number"], reference["row_index"], reference["normalized_value"])
            for reference in references
        }
    )
    return _result(
        "reference_scan",
        "passed",
        "明细表引用扫描完成",
        drawing=drawing,
        confidence=0.8,
        detail=f"识别到 {len(references)} 条图号值，按页/行/图号去重后 {unique} 条，继续按本批次匹配。",
        evidence={"scope": "reference_scan", "tables": len(tables)},
        code="reference_scanned",
    )


def _dimension_results(drawing: dict, pages: list[dict]) -> list[dict]:
    path = config.UPLOAD_DIR / str(drawing.get("filename") or "")
    results = []
    if not path.is_file():
        return [
            _result(
                "dimension_scan",
                "failed",
                "尺寸标注检查失败",
                drawing=drawing,
                confidence=0.0,
                detail="原图文件不存在，无法进行视觉复核。检查失败不等同于尺寸缺失。",
                evidence={"scope": "dimensions"},
                code="dimension_file_missing",
            )
        ]
    try:
        inspection = dimensions.inspect_drawing(path, pages)
    except Exception as exc:
        LOGGER.exception("Dimension inspection failed for drawing %s", drawing.get("id"))
        return [
            _result(
                "dimension_scan",
                "failed",
                "尺寸标注视觉检查失败",
                drawing=drawing,
                confidence=0.0,
                detail=f"视觉检查执行失败：{exc}。检查失败不等同于尺寸缺失。",
                evidence={"scope": "dimensions"},
                code="dimension_error",
            )
        ]
    if inspection["failed"]:
        return [
            _result(
                "dimension_scan",
                "failed",
                "尺寸标注视觉检查失败",
                drawing=drawing,
                confidence=0.0,
                detail=inspection.get("error") or "无法渲染图纸页面。",
                evidence={"scope": "dimensions", "limitations": inspection.get("limitations") or []},
                code="dimension_render_failed",
            )
        ]
    candidates = inspection["candidates"][:12]
    omitted = max(0, len(inspection["candidates"]) - len(candidates))
    for candidate in candidates:
        results.append(
            _result(
                "dimension_candidate",
                "pending",
                f"{candidate['type']}需人工复核",
                drawing=drawing,
                page_number=candidate["page_number"],
                bbox=candidate["bbox"],
                confidence=candidate["confidence"],
                detail=candidate["detail"],
                evidence={
                    "scope": "dimension_geometry",
                    "candidate_type": candidate["type"],
                    "area_ratio": round(float(candidate.get("area_ratio") or 0), 6),
                    "fill_ratio": round(float(candidate.get("fill_ratio") or 0), 6),
                    "aspect_ratio": round(float(candidate.get("aspect") or 0), 4),
                    "near_dimension": candidate.get("near_dimension", ""),
                    "coordinate_association": candidate.get("coordinate_association", False),
                    "limitations": inspection.get("limitations") or [],
                },
                code="dimension_candidate",
            )
        )
    limitations = inspection.get("limitations") or []
    if omitted:
        limitations = [*limitations, f"另有 {omitted} 个候选区域未逐项列出"]
    if candidates:
        summary_detail = (
            f"发现 {len(inspection['candidates'])} 个疑似未标或无法关联尺寸的封闭几何特征，"
            "已逐项给出位置。所有候选都必须人工复核；自动检查不能证明整张图尺寸齐全。"
        )
    else:
        summary_detail = (
            f"已对 {inspection['pages_scanned']} 页进行有限几何扫描，未发现封闭孔/槽/轮廓候选。"
            "开放轮廓、位置关系、视图表达、低对比区域和 OCR 漏识别仍可能漏检，不能判定整张图尺寸齐全。"
        )
    if limitations:
        summary_detail += " " + "；".join(limitations)
    results.append(
        _result(
            "dimension_scan",
            "pending",
            "尺寸标注需人工复核",
            drawing=drawing,
            confidence=0.35 if not candidates else 0.55,
            detail=summary_detail,
            evidence={
                "scope": "dimensions",
                "pages_scanned": inspection["pages_scanned"],
                "page_count": inspection.get("page_count"),
                "candidate_count": len(inspection["candidates"]),
                "limitations": limitations,
            },
            code="dimension_review",
        )
    )
    return results


def _cycle_edges(edges: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Mark directed edges that participate in a cycle (iterative DFS)."""
    adjacency: dict[int, list[int]] = defaultdict(list)
    for source, target in edges:
        adjacency[source].append(target)
    color: dict[int, int] = {}
    cycle_edges: set[tuple[int, int]] = set()
    for start in list(adjacency):
        if color.get(start):
            continue
        stack = [(start, 0)]
        path = []
        while stack:
            node, index = stack[-1]
            if index == 0:
                if color.get(node) == 1:
                    stack.pop()
                    continue
                color[node] = 1
                path.append(node)
            neighbors = adjacency.get(node, [])
            if index < len(neighbors):
                stack[-1] = (node, index + 1)
                target = neighbors[index]
                if color.get(target) == 1:
                    cycle_edges.add((node, target))
                    if target in path:
                        position = path.index(target)
                        for path_index in range(position, len(path)):
                            next_node = path[(path_index + 1) % len(path)]
                            cycle_edges.add((path[path_index], next_node))
                elif not color.get(target):
                    stack.append((target, 0))
            else:
                color[node] = 2
                stack.pop()
                if path:
                    path.pop()
    return cycle_edges


def _reference_results(
    references: list[dict],
    own_numbers: dict[int, tuple[str, dict]],
    members: list[dict],
    start_ids: list[int],
) -> tuple[list[dict], list[dict]]:
    if not references:
        return [], []
    member_by_id = {int(member["id"]): member for member in members}
    number_index: dict[str, list[int]] = defaultdict(list)
    unidentified_drawings = []
    for drawing_id, (number, _field) in own_numbers.items():
        if number:
            number_index[number].append(drawing_id)
        else:
            unidentified_drawings.append(member_by_id[drawing_id])
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for reference in references:
        grouped[(int(reference["source_drawing_id"]), reference["normalized_value"])].append(
            reference
        )
    resolved = []
    for (source_id, normalized), occurrences in grouped.items():
        source = member_by_id[source_id]
        targets = number_index.get(normalized, [])
        confidence = min(
            [float(item.get("confidence") or 0.65) for item in occurrences] or [0.65]
        )
        target_number_explicit = bool(
            own_numbers.get(targets[0], ("", {}))[1].get("explicit")
        ) if len(targets) == 1 else False
        if len(targets) == 1 and confidence >= 0.7 and target_number_explicit:
            status = "passed"
            target = member_by_id[targets[0]]
            note = "已在本批次找到唯一匹配图纸"
        elif len(targets) == 1 and confidence >= 0.7:
            status = "pending"
            target = None
            note = "匹配图纸的图号来自版式/模式推断，无法确认是该图纸，需人工确认"
        elif len(targets) > 1:
            status = "pending"
            target = None
            note = (
                "本批次存在多张同图号图纸，无法确定匹配："
                + "、".join(str(member_by_id[item]["filename"]) for item in targets)
            )
        elif confidence < 0.7:
            status = "pending"
            target = None
            note = "明细表结构置信度不足，未把未匹配结果判定为明确缺失"
        elif unidentified_drawings:
            status = "pending"
            target = None
            note = (
                f"本批次有 {len(unidentified_drawings)} 张已选图纸未识别到标题栏图号，"
                "不能排除该图号对应其中一张，需人工确认"
            )
        else:
            status = "missing"
            target = None
            note = "本批次已选图纸中未找到该图号；图纸库其他文件不参与本批次匹配"
        resolved.append(
            {
                "source": source,
                "normalized": normalized,
                "occurrences": occurrences,
                "status": status,
                "target": target,
                "note": note,
                "confidence": confidence,
                "candidate_drawings": [
                    {
                        "id": int(member_by_id[item]["id"]),
                        "filename": str(member_by_id[item]["filename"]),
                    }
                    for item in targets
                ],
                "unidentified_drawings": [
                    {
                        "id": int(member["id"]),
                        "filename": str(member["filename"]),
                    }
                    for member in unidentified_drawings
                ],
            }
        )
    edges = {
        (int(item["source"]["id"]), int(item["target"]["id"]))
        for item in resolved
        if item["status"] == "passed" and item["target"]
    }
    cycle_edges = _cycle_edges(edges)
    roots = [drawing_id for drawing_id in start_ids if drawing_id in member_by_id]
    if not roots:
        indegree = {drawing_id: 0 for drawing_id in member_by_id}
        for source, target in edges:
            if target in indegree:
                indegree[target] += 1
        roots = [drawing_id for drawing_id, count in indegree.items() if count == 0]
    if not roots:
        roots = list(member_by_id)
    depth = {drawing_id: 0 for drawing_id in roots}
    queue = deque(roots)
    while queue:
        source = queue.popleft()
        if depth[source] >= 50:
            continue
        for target_source, target in edges:
            if target_source != source:
                continue
            if target not in depth or depth[target] > depth[source] + 1:
                depth[target] = depth[source] + 1
                queue.append(target)
    results = []
    stored_references = []
    for index, item in enumerate(resolved):
        source = item["source"]
        occurrences = item["occurrences"]
        first = occurrences[0]
        duplicate_count = len(occurrences)
        target = item["target"]
        cycle_note = ""
        if target and (int(source["id"]), int(target["id"])) in cycle_edges:
            cycle_note = "检测到循环引用，已停止继续递归。"
        elif target and int(source["id"]) == int(target["id"]):
            cycle_note = "图纸引用了自身，已停止递归。"
        level_note = ""
        if target:
            target_depth = depth.get(int(target["id"]))
            if target_depth is not None:
                level_note = f"引用层级约为第 {target_depth} 层。"
        if item["status"] == "passed":
            title = f"引用图纸 {item['normalized']} 已找到"
            detail = (
                f"来源：{source['filename']}，第 {first['page_number']} 页，"
                f"明细表 {int(first['table_index']) + 1} 第 {int(first['row_index']) + 1} 行；"
                f"匹配文件：{target['filename']}。{level_note}{cycle_note}"
            )
            if duplicate_count > 1:
                detail += f" 本图重复引用 {duplicate_count} 次，已合并，不重复递归。"
        elif item["status"] == "missing":
            title = f"缺少引用图纸 {item['normalized']}"
            detail = (
                f"来源：{source['filename']}，第 {first['page_number']} 页，"
                f"明细表 {int(first['table_index']) + 1} 第 {int(first['row_index']) + 1} 行；"
                f"图号原文：{first['raw_value']}。{item['note']}。"
            )
        else:
            title = f"引用图纸 {item['normalized']} 待确认"
            detail = (
                f"来源：{source['filename']}，第 {first['page_number']} 页，"
                f"明细表 {int(first['table_index']) + 1} 第 {int(first['row_index']) + 1} 行；"
                f"图号原文：{first['raw_value']}。{item['note']}。"
            )
        evidence = {
            "scope": "reference",
            "normalized_drawing_number": item["normalized"],
            "raw_value": first["raw_value"],
            "duplicate_count": duplicate_count,
            "occurrences": [
                {
                    "page_number": occurrence["page_number"],
                    "table_index": int(occurrence["table_index"]) + 1,
                    "row_index": int(occurrence["row_index"]) + 1,
                    "raw_value": occurrence["raw_value"],
                }
                for occurrence in occurrences
            ],
            "scope_note": "仅在本批次已选图纸内匹配",
            "cycle": bool(cycle_note),
            "depth": depth.get(int(target["id"])) if target else None,
            "candidate_drawings": item["candidate_drawings"],
            "unidentified_drawings": item["unidentified_drawings"],
            "table_source": first.get("source_text", "")[:1000],
        }
        results.append(
            _result(
                "reference",
                item["status"],
                title,
                drawing=source,
                page_number=first["page_number"],
                table_index=int(first["table_index"]) + 1,
                row_index=int(first["row_index"]) + 1,
                column_index=int(first.get("cell_index") or 0) + 1,
                field_name="图号",
                field_value=first["raw_value"],
                target=target,
                bbox=first.get("bbox"),
                confidence=item["confidence"],
                detail=detail,
                evidence=evidence,
                code=f"reference_{item['status']}",
                reference_index=index,
            )
        )
        stored_references.append(
            {
                "source_drawing_id": int(source["id"]),
                "page_number": int(first["page_number"]),
                "table_index": int(first["table_index"]) + 1,
                "row_index": int(first["row_index"]) + 1,
                "cell_index": int(first.get("cell_index") or 0) + 1,
                "raw_value": first["raw_value"],
                "normalized_value": item["normalized"],
                "target_drawing_id": int(target["id"]) if target else None,
                "match_status": item["status"],
                "note": item["note"],
                "bbox": first.get("bbox"),
                "confidence": item["confidence"],
                "source_text": first.get("source_text", ""),
                "duplicate_count": duplicate_count,
            }
        )
    return results, stored_references


def _build_results(batch: dict, members: list[dict]) -> tuple[list[dict], list[dict]]:
    rules = batch["rules"]
    own_numbers: dict[int, tuple[str, dict]] = {}
    all_references: list[dict] = []
    results: list[dict] = []

    for drawing in members:
        drawing_id = int(drawing["id"])
        try:
            pages, warnings = ocr.extract_saved_evidence(drawing)
            repository.save_evidence_snapshot(drawing_id, pages)
        except Exception as exc:
            LOGGER.exception("Unable to normalize OCR evidence for drawing %s", drawing_id)
            results.append(
                _result(
                    "ocr",
                    "failed",
                    "OCR 证据读取失败",
                    drawing=drawing,
                    confidence=0.0,
                    detail=f"无法读取该图纸已保存的 OCR 结果：{exc}。检查失败不等同于缺失。",
                    evidence={"scope": "ocr"},
                    code="ocr_evidence_failed",
                )
            )
            pages = []
            warnings = []
        title_text = _title_text_for(drawing, pages)
        number, number_field = ocr.own_drawing_number(title_text)
        if not number:
            number, number_field = _drawing_number_from_legacy_segments(drawing)
        own_numbers[drawing_id] = (number, number_field)
        if warnings:
            results.append(
                _result(
                    "ocr",
                    "pending",
                    "OCR 结构化证据有限",
                    drawing=drawing,
                    page_number=1,
                    confidence=0.45,
                    detail="；".join(warnings),
                    evidence={"scope": "ocr", "warnings": warnings},
                    code="ocr_partial",
                )
            )
        if not pages or not any(str(page.get("text") or "").strip() for page in pages):
            if not any(
                item.get("kind") == "ocr" and item.get("status") == "failed"
                for item in results
                if item.get("source_drawing_id") == drawing_id
            ):
                results.append(
                    _result(
                        "ocr",
                        "failed",
                        "没有可用于检查的 OCR 文本",
                        drawing=drawing,
                        confidence=0.0,
                        detail="图纸 OCR 文本为空，标题栏、明细表和引用检查均无法可靠执行。",
                        evidence={"scope": "ocr"},
                        code="ocr_empty",
                    )
                )
            # A failed OCR result is actionable; still run dimension inspection
            # because raster geometry may remain reviewable.
        if rules.get("required_title_fields"):
            results.extend(_title_results(drawing, pages, title_text, rules))
        bom_results, references, tables = _bom_results(drawing, pages, rules)
        results.extend(bom_results)
        for reference in references:
            reference["source_drawing_id"] = drawing_id
            all_references.append(reference)
        results.append(_reference_scan_result(drawing, tables, references))
        if rules.get("dimension_check"):
            try:
                results.extend(_dimension_results(drawing, pages))
            except Exception as exc:
                LOGGER.exception("Dimension result creation failed for drawing %s", drawing_id)
                results.append(
                    _result(
                        "dimension_scan",
                        "failed",
                        "尺寸标注视觉检查失败",
                        drawing=drawing,
                        confidence=0.0,
                        detail=f"检查执行失败：{exc}。检查失败不等同于尺寸缺失。",
                        evidence={"scope": "dimensions"},
                        code="dimension_unexpected",
                    )
                )

    reference_results, stored_references = _reference_results(
        all_references,
        own_numbers,
        members,
        batch.get("start_drawing_ids") or [],
    )
    # Reference results are inserted after references so their IDs can be linked.
    for index, result in enumerate(reference_results):
        results.append(result)
    for result in results:
        result["rules_version"] = int(batch.get("rules_version") or 1)
    return results, stored_references


def run_batch(batch_id: int) -> dict:
    raw_batch = repository.get_batch(batch_id)
    if not raw_batch:
        raise KeyError(batch_id)
    batch = _decode_batch(raw_batch)
    if not repository.get_members(batch_id):
        raise ValueError("批次没有图纸，无法开始检查")
    if not repository.claim_run(batch_id):
        raise BatchRunningError("该批次正在检查中，请勿重复提交")
    repository.delete_run_data(batch_id)
    try:
        members = repository.get_members(batch_id)
        results, references = _build_results(batch, members)
        if not results:
            results = [
                _result(
                    "run",
                    "failed",
                    "检查未产生任何结果",
                    confidence=0.0,
                    detail="检查流程未生成检查项，请检查规则和 OCR 数据。",
                    evidence={"scope": "run"},
                    code="empty_run",
                )
            ]
        reference_ids: dict[int, int] = {}
        if references:
            stored = [
                {
                    **reference,
                    "rule_version": batch.get("rules_version") or 1,
                }
                for reference in references
            ]
            ids = repository.add_references(batch_id, stored)
            reference_ids = {index: reference_id for index, reference_id in enumerate(ids)}
        for result in results:
            reference_index = result.pop("reference_index", None)
            if reference_index is not None:
                result["reference_id"] = reference_ids.get(reference_index)
        repository.replace_results(batch_id, results)
        summary = repository.get_summary(batch_id)
        if summary["total"] and summary["failed"] == summary["total"]:
            repository.finish_run(
                batch_id,
                status="failed",
                error="所有检查项均失败；请检查原图和 OCR 数据后重试",
            )
        else:
            repository.finish_run(batch_id, status="completed")
    except Exception as exc:
        LOGGER.exception("Completeness batch %s failed", batch_id)
        try:
            repository.replace_results(
                batch_id,
                [
                    {
                        "kind": "run",
                        "code": "run_failed",
                        "status": "failed",
                        "title": "齐套性检查未完成",
                        "detail": f"检查执行中断：{exc}。检查失败不等同于图纸缺失。",
                        "evidence": {"scope": "run"},
                        "rules_version": batch.get("rules_version") or 1,
                    }
                ],
            )
        except Exception:
            LOGGER.exception("Unable to persist failure result for batch %s", batch_id)
        repository.finish_run(batch_id, status="failed", error=f"检查执行失败：{exc}")
        raise CompletenessRunError(f"检查执行失败：{exc}") from exc
    return batch_payload(batch_id)


def review_result(result_id: int, confirmed_status: str, note: str = "") -> dict:
    result = repository.get_result(result_id)
    if not result:
        raise KeyError(result_id)
    repository.review_result(result_id, confirmed_status, note)
    updated = repository.get_result(result_id)
    updated["status_label"] = STATUS_LABELS.get(updated["status"], updated["status"])
    updated["kind_label"] = KIND_LABELS.get(updated["kind"], updated["kind"])
    updated["confirmation_label"] = CONFIRMATION_LABELS.get(
        updated.get("confirmed_status"), ""
    )
    updated["location"] = result_location(updated)
    return updated


def delete_batch(batch_id: int) -> None:
    batch = repository.get_batch(batch_id)
    if not batch:
        raise KeyError(batch_id)
    if batch["status"] == "running":
        raise BatchRunningError("检查正在进行，不能删除批次")
    repository.delete_batch(batch_id)


def _result_image_target(result: dict) -> Path:
    if not result:
        raise KeyError("result")
    source_id = result.get("source_drawing_id")
    if not source_id:
        raise FileNotFoundError("原图纸已删除，无法查看图像证据")
    drawing = drawings_repository.get(int(source_id))
    if not drawing:
        raise FileNotFoundError("原图纸已删除，无法查看图像证据")
    source = config.UPLOAD_DIR / str(drawing.get("filename") or "")
    if not source.is_file():
        raise FileNotFoundError("原图文件不存在")
    page_number = max(1, int(result.get("page_number") or 1))
    try:
        page_image = images.load_page(source, page_number, dpi=150)
    except Exception as exc:
        raise FileNotFoundError(f"无法渲染原图证据：{exc}") from exc
    image = page_image
    try:
        bbox = result.get("bbox")
        width, height = page_image.size
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                x0, y0, x1, y1 = [max(0.0, min(1.0, float(item))) for item in bbox]
            except (TypeError, ValueError):
                x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
            pad_x = max(0.012, (x1 - x0) * 0.15)
            pad_y = max(0.012, (y1 - y0) * 0.15)
            crop = (
                max(0, int((x0 - pad_x) * width)),
                max(0, int((y0 - pad_y) * height)),
                min(width, int((x1 + pad_x) * width)),
                min(height, int((y1 + pad_y) * height)),
            )
            if crop[2] > crop[0] and crop[3] > crop[1]:
                image = page_image.crop(crop)
        image.thumbnail((1800, 1400), Image.Resampling.LANCZOS)
        config.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        stamp = source.stat().st_mtime_ns
        target = config.PREVIEW_DIR / (
            f"completeness-result-{result['id']}-{page_number}-{stamp}.jpg"
        )
        if not target.exists() or not target.stat().st_size:
            image.save(target, format="JPEG", quality=88, optimize=True)
        return target
    finally:
        if image is not page_image:
            image.close()
        page_image.close()


def result_image(result_id: int) -> Path:
    return _result_image_target(repository.get_result(result_id))


def _csv_safe(value) -> str:
    text = str(value if value is not None else "")
    signed = re.match(r"^[+-]\d+(?:\.\d+)?", text)
    if signed:
        tail = text[signed.end():]
        if not re.search(r"[=+@()|'\"!\\-]", tail):
            return text
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def export_csv(batch_id: int) -> tuple[str, str]:
    payload = batch_payload(batch_id)
    batch = payload["batch"]
    summary = payload["summary"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    headers = [
        "记录类型",
        "批次",
        "规则版本",
        "序号",
        "状态",
        "检查项",
        "类别",
        "来源图纸",
        "来源文件",
        "页码",
        "表格",
        "行",
        "列",
        "字段/图号",
        "说明",
        "匹配图纸",
        "规则信心（非模型概率）",
        "人工确认",
        "确认说明",
        "OCR证据",
    ]
    writer.writerow(headers)
    for status in STATUS_ORDER:
        writer.writerow(
            [
                "汇总",
                _csv_safe(batch["name"]),
                batch["rules_version"],
                "",
                STATUS_LABELS[status],
                f"{STATUS_LABELS[status]}数量",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                summary.get(status, 0),
                "",
                "",
                "",
                "",
                "",
            ]
        )
    for index, result in enumerate(payload["results"], 1):
        writer.writerow(
            [
                "明细",
                _csv_safe(batch["name"]),
                result["rules_version"],
                index,
                result["status_label"],
                _csv_safe(result["title"]),
                result["kind_label"],
                _csv_safe(result.get("source_filename")),
                _csv_safe(result.get("source_filename")),
                result.get("page_number") or "",
                result.get("table_index") or "",
                result.get("row_index") or "",
                result.get("column_index") or "",
                _csv_safe(result.get("field_name") or result.get("field_value")),
                _csv_safe(result.get("detail")),
                _csv_safe(result.get("target_filename")),
                result.get("confidence") if result.get("confidence") is not None else "",
                result.get("confirmation_label") or "未确认",
                _csv_safe(result.get("confirmed_note")),
                _csv_safe(
                    json.dumps(result.get("evidence") or {}, ensure_ascii=False)[:1500]
                ),
            ]
        )
    safe_name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", batch["name"]).strip(" ._")[:60]
    filename = f"齐套性检查-{batch_id}-{safe_name or '批次'}.csv"
    return "\ufeff" + buffer.getvalue(), filename


def content_disposition(filename: str, fallback: str) -> str:
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def drawing_options() -> list[dict]:
    """Available drawings for the batch picker, independent of prior batches."""
    options = []
    for drawing in drawings_repository.list_all("DESC"):
        title_text = str(drawing.get("title_text") or "")
        number, _field = ocr.own_drawing_number(title_text)
        options.append(
            {
                "id": int(drawing["id"]),
                "filename": str(drawing.get("filename") or ""),
                "display_title": _display_title(drawing),
                "drawing_number": number,
                "file_type": str(drawing.get("file_type") or ""),
                "upload_time": str(drawing.get("upload_time") or ""),
            }
        )
    return options
