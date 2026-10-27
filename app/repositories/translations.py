import json
from contextlib import closing
from datetime import datetime

from app.database import connect


SEGMENT_FIELDS = """
    id, drawing_id, segment_key, section, ordinal, label,
    source_text, source_hash, translatable, meta_json, created_at
"""


def count_segments(drawing_id: int) -> int:
    with closing(connect()) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM drawing_segments WHERE drawing_id=?",
            (drawing_id,),
        ).fetchone()[0]


def create_segments(drawing_id: int, segments: list[dict]) -> None:
    if not segments:
        return
    with closing(connect()) as connection:
        with connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO drawing_segments
                (drawing_id, segment_key, section, ordinal, label, source_text,
                 source_hash, translatable, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        segment["segment_key"],
                        segment["section"],
                        segment["ordinal"],
                        segment.get("label") or "",
                        segment["source_text"],
                        segment["source_hash"],
                        1 if segment.get("translatable", True) else 0,
                        json.dumps(segment.get("meta") or {}, ensure_ascii=False),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    for segment in segments
                ],
            )


def list_segments(drawing_id: int) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            f"SELECT {SEGMENT_FIELDS} FROM drawing_segments WHERE drawing_id=? ORDER BY ordinal, id",
            (drawing_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_rows(drawing_id: int, target_lang: str, provider: str = "qwen-mt") -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.drawing_id, s.segment_key, s.section, s.ordinal,
                   s.label, s.source_text, s.source_hash, s.translatable,
                   s.meta_json, s.created_at,
                   t.translated_text, t.status AS translation_status,
                   t.error, t.source_hash AS translation_source_hash,
                   t.glossary_hash, t.updated_at, t.model,
                   t.input_tokens, t.output_tokens
            FROM drawing_segments s
            LEFT JOIN translations t
              ON t.segment_id = s.id
             AND t.target_lang = ?
             AND t.provider = ?
            WHERE s.drawing_id = ?
            ORDER BY s.ordinal, s.id
            """,
            (target_lang, provider, drawing_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_cached(segment_id: int, target_lang: str, provider: str = "qwen-mt") -> dict | None:
    with closing(connect()) as connection:
        row = connection.execute(
            """
            SELECT * FROM translations
            WHERE segment_id=? AND target_lang=? AND provider=?
            """,
            (segment_id, target_lang, provider),
        ).fetchone()
    return dict(row) if row else None


def upsert_translation(
    segment_id: int,
    target_lang: str,
    model: str,
    translated_text: str,
    status: str,
    source_hash: str,
    glossary_hash: str,
    provider: str = "qwen-mt",
    error: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO translations
                (segment_id, target_lang, provider, model, translated_text, status,
                 source_hash, glossary_hash, error, input_tokens, output_tokens, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_id, target_lang, provider) DO UPDATE SET
                    model=excluded.model,
                    translated_text=excluded.translated_text,
                    status=excluded.status,
                    source_hash=excluded.source_hash,
                    glossary_hash=excluded.glossary_hash,
                    error=excluded.error,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    updated_at=excluded.updated_at
                """,
                (
                    segment_id,
                    target_lang,
                    provider,
                    model,
                    translated_text,
                    status,
                    source_hash,
                    glossary_hash,
                    error,
                    input_tokens,
                    output_tokens,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )


def delete_for_drawing(drawing_id: int) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM translations WHERE segment_id IN "
                "(SELECT id FROM drawing_segments WHERE drawing_id=?)",
                (drawing_id,),
            )
            connection.execute("DELETE FROM drawing_segments WHERE drawing_id=?", (drawing_id,))


def delete_all() -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM translations")
            connection.execute("DELETE FROM drawing_segments")


def list_glossary(source_lang: str, target_lang: str) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT source, target FROM glossary_terms
            WHERE source_lang=? AND target_lang=? AND enabled=1
            ORDER BY length(source) DESC, source
            """,
            (source_lang, target_lang),
        ).fetchall()
    return [dict(row) for row in rows]
