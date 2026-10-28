"""SQLite persistence for the assembly-drawing kit completeness check."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime

from app.database import connect


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json(value, default):
    return json.dumps(value if value is not None else default, ensure_ascii=False)


def create(mode: str, drawing_ids: list[int], root_drawing_ids: list[int] | None = None) -> int:
    stamp = _now()
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO kit_checks
                (mode, status, drawing_ids_json, root_drawing_ids_json,
                 total_drawings, processed_drawings, current_step, created_at, updated_at)
                VALUES (?, 'queued', ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    mode,
                    _json(drawing_ids, []),
                    _json(root_drawing_ids or [], []),
                    len(drawing_ids),
                    "等待检查",
                    stamp,
                    stamp,
                ),
            )
            return int(cursor.lastrowid)


def get(check_id: int) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute("SELECT * FROM kit_checks WHERE id=?", (check_id,)).fetchone()
    return _decode_check(dict(row)) if row else None


def list_recent(limit: int = 8) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM kit_checks ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [_decode_check(dict(row)) for row in rows]


def _decode_check(row: dict) -> dict:
    row["drawing_ids"] = _load_json(row.pop("drawing_ids_json", "[]"), [])
    row["root_drawing_ids"] = _load_json(row.pop("root_drawing_ids_json", "[]"), [])
    return row


def _load_json(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def update(check_id: int, **fields) -> None:
    allowed = {
        "status", "total_drawings", "processed_drawings", "current_step",
        "error", "finished_at", "root_drawing_ids_json",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in values)
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                f"UPDATE kit_checks SET {assignments} WHERE id=?",
                (*values.values(), check_id),
            )


def upsert_drawing(check_id: int, drawing_id: int, **fields) -> None:
    allowed = {
        "discovery", "depth", "parent_item_ids_json", "status", "page_count",
        "pages_processed", "table_code", "table_name", "issues_json",
        "started_at", "finished_at",
    }
    current = get_drawing(check_id, drawing_id)
    if current:
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        values["finished_at"] = values.get("finished_at", _now())
        assignments = ", ".join(f"{key}=?" for key in values)
        with closing(connect()) as connection:
            with connection:
                connection.execute(
                    f"UPDATE kit_check_drawings SET {assignments} WHERE check_id=? AND drawing_id=?",
                    (*values.values(), check_id, drawing_id),
                )
        return
    values = {
        "check_id": check_id,
        "drawing_id": drawing_id,
        "discovery": fields.get("discovery", "scan"),
        "depth": int(fields.get("depth") or 0),
        "parent_item_ids_json": fields.get("parent_item_ids_json", "[]"),
        "status": fields.get("status", "pending"),
        "page_count": int(fields.get("page_count") or 0),
        "pages_processed": int(fields.get("pages_processed") or 0),
        "table_code": fields.get("table_code", ""),
        "table_name": fields.get("table_name", ""),
        "issues_json": fields.get("issues_json", "[]"),
        "started_at": fields.get("started_at") or _now(),
        "finished_at": fields.get("finished_at"),
    }
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO kit_check_drawings
                (check_id, drawing_id, discovery, depth, parent_item_ids_json,
                 status, page_count, pages_processed, table_code, table_name,
                 issues_json, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["check_id"], values["drawing_id"], values["discovery"],
                    values["depth"], values["parent_item_ids_json"], values["status"],
                    values["page_count"], values["pages_processed"], values["table_code"],
                    values["table_name"], values["issues_json"], values["started_at"],
                    values["finished_at"],
                ),
            )


def get_drawing(check_id: int, drawing_id: int) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM kit_check_drawings WHERE check_id=? AND drawing_id=?",
            (check_id, drawing_id),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["parent_item_ids"] = _load_json(result.pop("parent_item_ids_json", "[]"), [])
    result["issues"] = _load_json(result.pop("issues_json", "[]"), [])
    return result


def list_drawings(check_id: int) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM kit_check_drawings WHERE check_id=? ORDER BY id", (check_id,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["parent_item_ids"] = _load_json(item.pop("parent_item_ids_json", "[]"), [])
        item["issues"] = _load_json(item.pop("issues_json", "[]"), [])
        result.append(item)
    return result


def add_parent_reference(check_id: int, drawing_id: int, item_id: int) -> None:
    current = get_drawing(check_id, drawing_id)
    if not current:
        return
    parents = list(current.get("parent_item_ids") or [])
    if item_id not in parents:
        parents.append(item_id)
        update_drawing_fields(check_id, drawing_id, parent_item_ids_json=_json(parents, []))


def update_drawing_fields(check_id: int, drawing_id: int, **fields) -> None:
    allowed = {
        "status", "page_count", "pages_processed", "table_code", "table_name",
        "issues_json", "parent_item_ids_json", "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                f"UPDATE kit_check_drawings SET {assignments} WHERE check_id=? AND drawing_id=?",
                (*values.values(), check_id, drawing_id),
            )


def replace_items(check_id: int, drawing_id: int, items: list[dict]) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM kit_check_items WHERE check_id=? AND drawing_id=?",
                (check_id, drawing_id),
            )
            for item in items:
                connection.execute(
                    """
                    INSERT INTO kit_check_items
                    (check_id, drawing_id, page_number, row_index, sequence_no, code,
                     name, note, quantity, raw_text, position_json, extraction_status,
                     extraction_issue, exempt_reason, match_status, matched_json,
                     candidates_json, ignored_json, edited, original_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        check_id,
                        drawing_id,
                        int(item.get("page_number") or 0),
                        int(item.get("row_index") or 0),
                        str(item.get("sequence_no") or ""),
                        str(item.get("code") or ""),
                        str(item.get("name") or ""),
                        str(item.get("note") or ""),
                        str(item.get("quantity") or ""),
                        str(item.get("raw_text") or ""),
                        _json(item.get("position") or {}, {}),
                        str(item.get("extraction_status") or "ok"),
                        str(item.get("extraction_issue") or ""),
                        item.get("exempt_reason"),
                        str(item.get("match_status") or "unchecked"),
                        _json(item.get("matches") or [], []),
                        _json(item.get("candidates") or [], []),
                        _json(item.get("ignored") or [], []),
                        int(bool(item.get("edited"))),
                        _json(item.get("original") or {}, {}),
                        _now(),
                    ),
                )


def delete_items_for_drawing(check_id: int, drawing_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM kit_check_items WHERE check_id=? AND drawing_id=?",
                (check_id, drawing_id),
            )


def add_item(check_id: int, drawing_id: int, item: dict) -> int:
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO kit_check_items
                (check_id, drawing_id, page_number, row_index, sequence_no, code,
                 name, note, quantity, raw_text, position_json, extraction_status,
                 extraction_issue, exempt_reason, match_status, matched_json,
                 candidates_json, ignored_json, edited, original_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check_id, drawing_id,
                    int(item.get("page_number") or 0),
                    int(item.get("row_index") or 0),
                    str(item.get("sequence_no") or ""),
                    str(item.get("code") or ""),
                    str(item.get("name") or ""),
                    str(item.get("note") or ""),
                    str(item.get("quantity") or ""),
                    str(item.get("raw_text") or ""),
                    _json(item.get("position") or {}, {}),
                    str(item.get("extraction_status") or "ok"),
                    str(item.get("extraction_issue") or ""),
                    item.get("exempt_reason"),
                    str(item.get("match_status") or "unchecked"),
                    _json(item.get("matches") or [], []),
                    _json(item.get("candidates") or [], []),
                    _json(item.get("ignored") or [], []),
                    int(bool(item.get("edited"))),
                    _json(item.get("original") or {}, {}),
                    _now(),
                ),
            )
            return int(cursor.lastrowid)


def list_items(check_id: int, drawing_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM kit_check_items WHERE check_id=?"
    params: list = [check_id]
    if drawing_id is not None:
        query += " AND drawing_id=?"
        params.append(drawing_id)
    query += " ORDER BY drawing_id, page_number, row_index, id"
    with closing(connect()) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["position"] = _load_json(item.pop("position_json", "{}"), {})
        item["matches"] = _load_json(item.pop("matched_json", "[]"), [])
        item["candidates"] = _load_json(item.pop("candidates_json", "[]"), [])
        item["ignored"] = _load_json(item.pop("ignored_json", "[]"), [])
        item["original"] = _load_json(item.pop("original_json", "{}"), {})
        result.append(item)
    return result


def get_item(item_id: int) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute("SELECT * FROM kit_check_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["position"] = _load_json(item.pop("position_json", "{}"), {})
    item["matches"] = _load_json(item.pop("matched_json", "[]"), [])
    item["candidates"] = _load_json(item.pop("candidates_json", "[]"), [])
    item["ignored"] = _load_json(item.pop("ignored_json", "[]"), [])
    item["original"] = _load_json(item.pop("original_json", "{}"), {})
    return item


def update_item(item_id: int, **fields) -> None:
    allowed = {
        "sequence_no", "code", "name", "note", "quantity", "extraction_status",
        "extraction_issue", "exempt_reason", "match_status", "matched_json",
        "candidates_json", "ignored_json", "edited", "original_json",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                f"UPDATE kit_check_items SET {assignments} WHERE id=?",
                (*values.values(), item_id),
            )


def clear_results(check_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM kit_check_items WHERE check_id=?", (check_id,))
            connection.execute("DELETE FROM kit_check_drawings WHERE check_id=?", (check_id,))


def cache_get(drawing_id: int, signature: str) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM kit_extractions WHERE drawing_id=? AND signature=?",
            (drawing_id, signature),
        ).fetchone()
    if not row:
        return None
    payload = _load_json(row["payload_json"], {})
    return payload if isinstance(payload, dict) else None


def cache_put(drawing_id: int, signature: str, status: str, payload: dict) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO kit_extractions (drawing_id, signature, status, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(drawing_id) DO UPDATE SET
                    signature=excluded.signature,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (drawing_id, signature, status, _json(payload, {}), _now()),
            )


def delete_extraction(drawing_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM kit_extractions WHERE drawing_id=?", (drawing_id,))


def delete_all_extractions() -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM kit_extractions")


def delete_for_drawing(drawing_id: int) -> None:
    """Remove live check rows for a deleted drawing.

    Historical check summaries remain queryable, but their missing-selected
    reconciliation will mark them incomplete instead of counting stale items.
    """
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM kit_check_items WHERE drawing_id=?", (drawing_id,))
            connection.execute("DELETE FROM kit_check_drawings WHERE drawing_id=?", (drawing_id,))


def delete_all_check_results() -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM kit_check_items")
            connection.execute("DELETE FROM kit_check_drawings")
