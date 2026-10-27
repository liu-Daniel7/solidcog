import csv
import sqlite3
from contextlib import closing

from app.config import BASE_DIR, DATABASE_PATH


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS drawings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT,
                    file_type TEXT,
                    file_size INTEGER,
                    upload_time TEXT,
                    title_text TEXT,
                    tech_text TEXT,
                    layout TEXT,
                    all_text TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(drawings)")
            }
            if "all_text" not in columns:
                connection.execute("ALTER TABLE drawings ADD COLUMN all_text TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS drawing_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drawing_id INTEGER NOT NULL,
                    segment_key TEXT NOT NULL,
                    section TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    source_text TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    translatable INTEGER NOT NULL DEFAULT 1,
                    meta_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(drawing_id, segment_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_drawing_segments_drawing ON drawing_segments(drawing_id, ordinal)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    segment_id INTEGER NOT NULL,
                    target_lang TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'qwen-mt',
                    model TEXT NOT NULL,
                    translated_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'translated',
                    source_hash TEXT NOT NULL,
                    glossary_hash TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(segment_id, target_lang, provider)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_translations_lookup ON translations(segment_id, target_lang, provider)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS glossary_terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_lang, target_lang, source)
                )
                """
            )
            _seed_glossary(connection)


def _seed_glossary(connection: sqlite3.Connection) -> None:
    count = connection.execute(
        "SELECT COUNT(*) FROM glossary_terms WHERE source_lang='zh' AND target_lang='en'"
    ).fetchone()[0]
    if count:
        return
    path = BASE_DIR / "data" / "glossary" / "mechanical_zh_en.csv"
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            (row["source"].strip(), row["target"].strip(), (row.get("note") or "").strip())
            for row in csv.DictReader(handle)
            if (row.get("source") or "").strip() and (row.get("target") or "").strip()
        ]
    connection.executemany(
        """
        INSERT OR IGNORE INTO glossary_terms
        (source_lang, target_lang, source, target, enabled, note)
        VALUES ('zh', 'en', ?, ?, 1, ?)
        """,
        rows,
    )
