"""Persistence for coordinate-aware drawing scans."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime

from app.database import connect


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def get(drawing_id: int, signature: str) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM drawing_scans WHERE drawing_id=? AND signature=?",
            (drawing_id, signature),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["pages"] = _load(result.pop("pages_json", "[]"), [])
    result["blocks"] = _load(result.pop("blocks_json", "[]"), [])
    result["available"] = True
    return result


def put(drawing_id: int, signature: str, result: dict) -> None:
    now = _now()
    pages = result.get("pages") or []
    blocks = result.get("blocks") or []
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO drawing_scans
                (drawing_id, signature, scanner, pages_json, blocks_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(drawing_id) DO UPDATE SET
                    signature=excluded.signature,
                    scanner=excluded.scanner,
                    pages_json=excluded.pages_json,
                    blocks_json=excluded.blocks_json,
                    updated_at=excluded.updated_at
                """,
                (
                    drawing_id,
                    signature,
                    str(result.get("scanner") or ""),
                    json.dumps(pages, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(blocks, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )


def delete_for_drawing(drawing_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM drawing_scans WHERE drawing_id=?", (drawing_id,))


def delete_all() -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM drawing_scans")
