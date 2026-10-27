"""Persistence for completeness batches, evidence and reviewable results."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime

from app.database import connect


RESULT_FIELDS = """
    id, batch_id, rules_version, kind, code, status, title, detail,
    source_drawing_id, source_filename, page_number, table_index, row_index,
    column_index, field_name, field_value, target_drawing_id, target_filename,
    bbox_json, confidence, evidence_json, reference_id, confirmed_status,
    confirmed_note, created_at, updated_at
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dict(row):
    return dict(row) if row else None


def create_batch(
    *,
    name: str,
    drawing_ids: list[int],
    start_drawing_ids: list[int],
    rules: dict,
    scope_note: str,
) -> int:
    now = _now()
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO completeness_batches
                (name, status, rules_json, rules_version, start_drawing_ids_json,
                 scope_note, created_at, updated_at)
                VALUES (?, 'draft', ?, 1, ?, ?, ?, ?)
                """,
                (
                    name,
                    json.dumps(rules, ensure_ascii=False, sort_keys=True),
                    json.dumps(start_drawing_ids, ensure_ascii=False),
                    scope_note,
                    now,
                    now,
                ),
            )
            batch_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO completeness_batch_drawings
                (batch_id, drawing_id, position, added_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (batch_id, drawing_id, position, now)
                    for position, drawing_id in enumerate(drawing_ids)
                ],
            )
            return batch_id


def list_batches() -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM completeness_batch_drawings d
                     WHERE d.batch_id = b.id) AS member_count
            FROM completeness_batches b
            ORDER BY b.id DESC
            """
        ).fetchall()
    batches = []
    for row in rows:
        item = dict(row)
        item["summary"] = get_summary(item["id"])
        batches.append(item)
    return batches


def get_batch(batch_id: int) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM completeness_batches WHERE id=?", (batch_id,)
        ).fetchone()
    return _dict(row)


def get_members(batch_id: int) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT m.id AS member_id, m.position, d.*
            FROM completeness_batch_drawings m
            JOIN drawings d ON d.id = m.drawing_id
            WHERE m.batch_id=?
            ORDER BY m.position, m.id
            """,
            (batch_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_batch_fields(batch_id: int, **fields) -> None:
    allowed = {
        "status",
        "rules_json",
        "rules_version",
        "start_drawing_ids_json",
        "scope_note",
        "error",
        "started_at",
        "completed_at",
    }
    update_fields = {key: value for key, value in fields.items() if key in allowed}
    if not update_fields:
        return
    update_fields["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in update_fields)
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                f"UPDATE completeness_batches SET {assignments} WHERE id=?",
                (*update_fields.values(), batch_id),
            )


def claim_run(batch_id: int) -> bool:
    """Atomically move a batch into running state, rejecting double starts."""
    now = _now()
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                UPDATE completeness_batches
                SET status='running', started_at=?, completed_at=NULL, error=NULL,
                    updated_at=?
                WHERE id=? AND status!='running'
                """,
                (now, now, batch_id),
            )
            return cursor.rowcount == 1


def finish_run(batch_id: int, *, status: str, error: str | None = None) -> None:
    now = _now()
    update_batch_fields(
        batch_id,
        status=status,
        error=error,
        completed_at=now if status in {"completed", "failed"} else None,
    )


def recover_interrupted_runs() -> int:
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                UPDATE completeness_batches
                SET status='failed',
                    error='上次检查在服务退出时中断，请重新发起检查',
                    completed_at=?, updated_at=?
                WHERE status='running'
                """,
                (_now(), _now()),
            )
            return cursor.rowcount


def update_rules(batch_id: int, rules: dict) -> int:
    """Persist a new rule revision; old results remain inspectable but stale."""
    with closing(connect()) as connection:
        with connection:
            row = connection.execute(
                "SELECT rules_version, status FROM completeness_batches WHERE id=?",
                (batch_id,),
            ).fetchone()
            if not row:
                raise KeyError(batch_id)
            version = int(row["rules_version"]) + 1
            status = "stale" if row["status"] in {"completed", "failed"} else row["status"]
            connection.execute(
                """
                UPDATE completeness_batches
                SET rules_json=?, rules_version=?, status=?, error=NULL, updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(rules, ensure_ascii=False, sort_keys=True),
                    version,
                    status,
                    _now(),
                    batch_id,
                ),
            )
            return version


def delete_batch(batch_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            drawing_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT drawing_id FROM completeness_batch_drawings WHERE batch_id=?",
                    (batch_id,),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM completeness_results WHERE batch_id=?", (batch_id,)
            )
            connection.execute(
                "DELETE FROM completeness_references WHERE batch_id=?", (batch_id,)
            )
            connection.execute(
                "DELETE FROM completeness_batch_drawings WHERE batch_id=?", (batch_id,)
            )
            connection.execute(
                "DELETE FROM completeness_batches WHERE id=?", (batch_id,)
            )
            for drawing_id in drawing_ids:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM completeness_batch_drawings WHERE drawing_id=?",
                    (drawing_id,),
                ).fetchone()[0]
                if remaining:
                    continue
                page_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM completeness_ocr_pages WHERE drawing_id=?",
                        (drawing_id,),
                    ).fetchall()
                ]
                for page_id in page_ids:
                    table_ids = [
                        row[0]
                        for row in connection.execute(
                            "SELECT id FROM completeness_ocr_tables WHERE page_id=?",
                            (page_id,),
                        ).fetchall()
                    ]
                    for table_id in table_ids:
                        connection.execute(
                            "DELETE FROM completeness_ocr_cells WHERE table_id=?",
                            (table_id,),
                        )
                    connection.execute(
                        "DELETE FROM completeness_ocr_tables WHERE page_id=?", (page_id,)
                    )
                    connection.execute(
                        "DELETE FROM completeness_ocr_blocks WHERE page_id=?", (page_id,)
                    )
                connection.execute(
                    "DELETE FROM completeness_ocr_pages WHERE drawing_id=?", (drawing_id,)
                )


def delete_run_data(batch_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM completeness_results WHERE batch_id=?", (batch_id,)
            )
            connection.execute(
                "DELETE FROM completeness_references WHERE batch_id=?", (batch_id,)
            )


def save_evidence_snapshot(drawing_id: int, pages: list[dict]) -> None:
    """Replace normalized OCR evidence for one drawing in one transaction."""
    now = _now()
    with closing(connect()) as connection:
        with connection:
            old_page_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM completeness_ocr_pages WHERE drawing_id=?",
                    (drawing_id,),
                ).fetchall()
            ]
            for page_id in old_page_ids:
                table_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM completeness_ocr_tables WHERE page_id=?",
                        (page_id,),
                    ).fetchall()
                ]
                for table_id in table_ids:
                    connection.execute(
                        "DELETE FROM completeness_ocr_cells WHERE table_id=?",
                        (table_id,),
                    )
                connection.execute(
                    "DELETE FROM completeness_ocr_tables WHERE page_id=?", (page_id,)
                )
                connection.execute(
                    "DELETE FROM completeness_ocr_blocks WHERE page_id=?", (page_id,)
                )
            connection.execute(
                "DELETE FROM completeness_ocr_pages WHERE drawing_id=?", (drawing_id,)
            )
            for page in pages:
                cursor = connection.execute(
                    """
                    INSERT INTO completeness_ocr_pages
                    (drawing_id, page_number, width, height, backend, model,
                     source, text, warnings_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        drawing_id,
                        int(page["page_number"]),
                        page.get("width"),
                        page.get("height"),
                        page.get("backend") or "",
                        page.get("model") or "",
                        page.get("source") or "",
                        page.get("text") or "",
                        json.dumps(page.get("warnings") or [], ensure_ascii=False),
                        now,
                    ),
                )
                page_id = int(cursor.lastrowid)
                connection.executemany(
                    """
                    INSERT INTO completeness_ocr_blocks
                    (page_id, block_index, block_type, text, bbox_json,
                     confidence, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            page_id,
                            int(block.get("index", index)),
                            block.get("type") or "",
                            block.get("text") or "",
                            json.dumps(block["bbox"], ensure_ascii=False)
                            if block.get("bbox") is not None
                            else None,
                            block.get("confidence"),
                            block.get("source") or "",
                        )
                        for index, block in enumerate(page.get("blocks") or [])
                    ],
                )
                for table in page.get("tables") or []:
                    table_cursor = connection.execute(
                        """
                        INSERT INTO completeness_ocr_tables
                        (page_id, table_index, bbox_json, confidence, source,
                         row_count, column_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            page_id,
                            int(table.get("table_index", 0)),
                            json.dumps(table["bbox"], ensure_ascii=False)
                            if table.get("bbox") is not None
                            else None,
                            table.get("confidence"),
                            table.get("source") or "",
                            int(table.get("row_count") or 0),
                            int(table.get("column_count") or 0),
                        ),
                    )
                    table_id = int(table_cursor.lastrowid)
                    connection.executemany(
                        """
                        INSERT INTO completeness_ocr_cells
                        (table_id, row_index, column_index, row_span, column_span,
                         text, bbox_json, confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                table_id,
                                int(cell.get("row", 0)),
                                int(cell.get("col", 0)),
                                int(cell.get("row_span", 1)),
                                int(cell.get("col_span", 1)),
                                cell.get("text") or "",
                                json.dumps(cell["bbox"], ensure_ascii=False)
                                if cell.get("bbox") is not None
                                else None,
                                cell.get("confidence"),
                            )
                            for cell in table.get("cells") or []
                        ],
                    )


def list_pages(drawing_id: int) -> list[dict]:
    with closing(connect()) as connection:
        pages = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM completeness_ocr_pages
                WHERE drawing_id=? ORDER BY page_number, id
                """,
                (drawing_id,),
            ).fetchall()
        ]
        for page in pages:
            page["warnings"] = json.loads(page.pop("warnings_json") or "[]")
            page["blocks"] = [
                {
                    **block,
                    "bbox": json.loads(block["bbox_json"])
                    if block.get("bbox_json")
                    else None,
                }
                for block in (
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT * FROM completeness_ocr_blocks
                        WHERE page_id=? ORDER BY block_index, id
                        """,
                        (page["id"],),
                    ).fetchall()
                )
            ]
            tables = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM completeness_ocr_tables
                    WHERE page_id=? ORDER BY table_index, id
                    """,
                    (page["id"],),
                ).fetchall()
            ]
            for table in tables:
                table["bbox"] = (
                    json.loads(table["bbox_json"]) if table.get("bbox_json") else None
                )
                table["cells"] = [
                    {
                        **cell,
                        "bbox": json.loads(cell["bbox_json"])
                        if cell.get("bbox_json")
                        else None,
                    }
                    for cell in (
                        dict(row)
                        for row in connection.execute(
                            """
                            SELECT * FROM completeness_ocr_cells
                            WHERE table_id=? ORDER BY row_index, column_index, id
                            """,
                            (table["id"],),
                        ).fetchall()
                    )
                ]
            page["tables"] = tables
    return pages


def delete_evidence_for_drawing(drawing_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            page_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM completeness_ocr_pages WHERE drawing_id=?",
                    (drawing_id,),
                ).fetchall()
            ]
            for page_id in page_ids:
                table_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM completeness_ocr_tables WHERE page_id=?",
                        (page_id,),
                    ).fetchall()
                ]
                for table_id in table_ids:
                    connection.execute(
                        "DELETE FROM completeness_ocr_cells WHERE table_id=?",
                        (table_id,),
                    )
                connection.execute(
                    "DELETE FROM completeness_ocr_tables WHERE page_id=?", (page_id,)
                )
                connection.execute(
                    "DELETE FROM completeness_ocr_blocks WHERE page_id=?", (page_id,)
                )
            connection.execute(
                "DELETE FROM completeness_ocr_pages WHERE drawing_id=?", (drawing_id,)
            )


def add_references(batch_id: int, references: list[dict]) -> list[int]:
    ids = []
    now = _now()
    with closing(connect()) as connection:
        with connection:
            for reference in references:
                cursor = connection.execute(
                    """
                    INSERT INTO completeness_references
                    (batch_id, source_drawing_id, source_page_number, table_index,
                     row_index, cell_index, raw_value, normalized_value,
                     target_drawing_id, match_status, note, bbox_json,
                     confidence, source_text, duplicate_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        reference["source_drawing_id"],
                        reference.get("page_number"),
                        reference.get("table_index"),
                        reference.get("row_index"),
                        reference.get("cell_index"),
                        reference.get("raw_value") or "",
                        reference.get("normalized_value") or "",
                        reference.get("target_drawing_id"),
                        reference.get("match_status") or "pending",
                        reference.get("note") or "",
                        json.dumps(reference["bbox"], ensure_ascii=False)
                        if reference.get("bbox") is not None
                        else None,
                        reference.get("confidence"),
                        reference.get("source_text") or "",
                        int(reference.get("duplicate_count") or 1),
                        now,
                    ),
                )
                ids.append(int(cursor.lastrowid))
    return ids


def replace_results(batch_id: int, results: list[dict]) -> None:
    now = _now()
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM completeness_results WHERE batch_id=?", (batch_id,)
            )
            for result in results:
                connection.execute(
                    """
                    INSERT INTO completeness_results
                    (batch_id, rules_version, kind, code, status, title, detail,
                     source_drawing_id, source_filename, page_number, table_index,
                     row_index, column_index, field_name, field_value,
                     target_drawing_id, target_filename, bbox_json, confidence,
                     evidence_json, reference_id, confirmed_status,
                     confirmed_note, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, 'unconfirmed', '', ?, ?)
                    """,
                    (
                        batch_id,
                        int(result.get("rules_version") or 1),
                        result["kind"],
                        result.get("code") or "",
                        result["status"],
                        result["title"],
                        result.get("detail") or "",
                        result.get("source_drawing_id"),
                        result.get("source_filename") or "",
                        result.get("page_number"),
                        result.get("table_index"),
                        result.get("row_index"),
                        result.get("column_index"),
                        result.get("field_name") or "",
                        result.get("field_value") or "",
                        result.get("target_drawing_id"),
                        result.get("target_filename") or "",
                        json.dumps(result["bbox"], ensure_ascii=False)
                        if result.get("bbox") is not None
                        else None,
                        result.get("confidence"),
                        json.dumps(result.get("evidence") or {}, ensure_ascii=False),
                        result.get("reference_id"),
                        now,
                        now,
                    ),
                )


def list_results(
    batch_id: int, *, status: str | None = None, kind: str | None = None
) -> list[dict]:
    conditions = ["batch_id=?"]
    parameters: list = [batch_id]
    if status in {"passed", "missing", "pending", "failed"}:
        conditions.append("status=?")
        parameters.append(status)
    if kind:
        conditions.append("kind=?")
        parameters.append(kind)
    with closing(connect()) as connection:
        rows = connection.execute(
            f"""
            SELECT {RESULT_FIELDS}
            FROM completeness_results
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE status WHEN 'failed' THEN 0 WHEN 'missing' THEN 1
                            WHEN 'pending' THEN 2 ELSE 3 END,
                source_drawing_id, page_number, id
            """,
            parameters,
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["bbox"] = json.loads(item.pop("bbox_json")) if item.get("bbox_json") else None
        item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        results.append(item)
    return results


def get_summary(batch_id: int) -> dict:
    summary = {"passed": 0, "missing": 0, "pending": 0, "failed": 0, "total": 0}
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM completeness_results WHERE batch_id=?
            GROUP BY status
            """,
            (batch_id,),
        ).fetchall()
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] = int(row["count"])
            summary["total"] += int(row["count"])
    return summary


def get_result(result_id: int) -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            f"SELECT {RESULT_FIELDS} FROM completeness_results WHERE id=?",
            (result_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["bbox"] = json.loads(item.pop("bbox_json")) if item.get("bbox_json") else None
    item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
    return item


def review_result(result_id: int, confirmed_status: str, note: str) -> None:
    if confirmed_status not in {"unconfirmed", "confirmed", "dismissed"}:
        raise ValueError("无效的人工确认状态")
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                UPDATE completeness_results
                SET confirmed_status=?, confirmed_note=?, updated_at=?
                WHERE id=?
                """,
                (confirmed_status, note.strip()[:500], _now(), result_id),
            )


def drawing_in_running_batch(drawing_id: int) -> bool:
    with closing(connect()) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM completeness_batch_drawings m
            JOIN completeness_batches b ON b.id=m.batch_id
            WHERE m.drawing_id=? AND b.status='running'
            LIMIT 1
            """,
            (drawing_id,),
        ).fetchone()
    return bool(row)


def invalidate_for_drawing(drawing_id: int, reason: str) -> int:
    """Remove memberships and mark affected results pending/stale.

    Results and evidence snapshots are retained so reviewers can see what the
    earlier check found. Original OCR and the drawing row are deleted separately
    by the existing drawing service after this function runs.
    """
    now = _now()
    affected = 0
    with closing(connect()) as connection:
        with connection:
            batch_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT batch_id FROM completeness_batch_drawings WHERE drawing_id=?",
                    (drawing_id,),
                ).fetchall()
            ]
            for batch_id in batch_ids:
                batch = connection.execute(
                    "SELECT status FROM completeness_batches WHERE id=?", (batch_id,)
                ).fetchone()
                connection.execute(
                    "DELETE FROM completeness_batch_drawings WHERE batch_id=? AND drawing_id=?",
                    (batch_id, drawing_id),
                )
                result_rows = connection.execute(
                    """
                    SELECT id, detail FROM completeness_results
                    WHERE batch_id=? AND (source_drawing_id=? OR target_drawing_id=?)
                    """,
                    (batch_id, drawing_id, drawing_id),
                ).fetchall()
                for row in result_rows:
                    detail = str(row["detail"] or "")
                    suffix = reason
                    if suffix not in detail:
                        detail = f"{detail}\n{suffix}".strip()
                    connection.execute(
                        """
                        UPDATE completeness_results
                        SET status='pending', code='invalidated',
                            source_drawing_id=CASE WHEN source_drawing_id=? THEN NULL ELSE source_drawing_id END,
                            target_drawing_id=CASE WHEN target_drawing_id=? THEN NULL ELSE target_drawing_id END,
                            detail=?, confirmed_status='unconfirmed',
                            confirmed_note='', updated_at=?
                        WHERE id=?
                        """,
                        (drawing_id, drawing_id, detail, now, row["id"]),
                    )
                    affected += 1
                if batch and batch["status"] != "draft":
                    connection.execute(
                        """
                        UPDATE completeness_batches
                        SET status='stale',
                            error='批次中的图纸已删除，原检查结果已失效，请重新检查',
                            updated_at=?
                        WHERE id=?
                        """,
                        (now, batch_id),
                    )
            connection.execute(
                "DELETE FROM completeness_references WHERE source_drawing_id=?",
                (drawing_id,),
            )
            connection.execute(
                """
                UPDATE completeness_references
                SET target_drawing_id=NULL, match_status='pending',
                    note='匹配图纸已删除，需重新检查'
                WHERE target_drawing_id=?
                """,
                (drawing_id,),
            )
    return affected


def list_result_drawings(batch_id: int) -> list[dict]:
    """Return result counts grouped by the source drawing snapshot."""
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT source_drawing_id, source_filename,
                   SUM(CASE WHEN status='passed' THEN 1 ELSE 0 END) AS passed,
                   SUM(CASE WHEN status='missing' THEN 1 ELSE 0 END) AS missing,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                   COUNT(*) AS total
            FROM completeness_results
            WHERE batch_id=? AND source_drawing_id IS NOT NULL
            GROUP BY source_drawing_id, source_filename
            ORDER BY source_drawing_id
            """,
            (batch_id,),
        ).fetchall()
    return [dict(row) for row in rows]
