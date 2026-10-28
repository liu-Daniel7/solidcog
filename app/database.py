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
                    all_text TEXT,
                    batch_id TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(drawings)")
            }
            # Additive migrations keep existing local databases usable without a
            # separate migration tool. New columns are nullable or have defaults.
            for column, definition in (
                ("all_text", "TEXT"),
                ("original_filename", "TEXT"),
                ("batch_id", "TEXT"),
                ("page_count", "INTEGER NOT NULL DEFAULT 0"),
                ("pages_processed", "INTEGER NOT NULL DEFAULT 0"),
                ("ocr_errors", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE drawings ADD COLUMN {column} {definition}")
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
                CREATE TABLE IF NOT EXISTS kit_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    drawing_ids_json TEXT NOT NULL,
                    root_drawing_ids_json TEXT NOT NULL DEFAULT '[]',
                    total_drawings INTEGER NOT NULL DEFAULT 0,
                    processed_drawings INTEGER NOT NULL DEFAULT 0,
                    current_step TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kit_checks_created ON kit_checks(created_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kit_check_drawings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_id INTEGER NOT NULL,
                    drawing_id INTEGER NOT NULL,
                    discovery TEXT NOT NULL DEFAULT 'scan',
                    depth INTEGER NOT NULL DEFAULT 0,
                    parent_item_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    page_count INTEGER NOT NULL DEFAULT 0,
                    pages_processed INTEGER NOT NULL DEFAULT 0,
                    table_code TEXT NOT NULL DEFAULT '',
                    table_name TEXT NOT NULL DEFAULT '',
                    issues_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(check_id, drawing_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kit_check_drawings_check ON kit_check_drawings(check_id, id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kit_check_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_id INTEGER NOT NULL,
                    drawing_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL DEFAULT 0,
                    row_index INTEGER NOT NULL DEFAULT 0,
                    sequence_no TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    quantity TEXT NOT NULL DEFAULT '',
                    raw_text TEXT NOT NULL DEFAULT '',
                    position_json TEXT NOT NULL DEFAULT '{}',
                    extraction_status TEXT NOT NULL DEFAULT 'ok',
                    extraction_issue TEXT NOT NULL DEFAULT '',
                    exempt_reason TEXT,
                    match_status TEXT NOT NULL DEFAULT 'unchecked',
                    matched_json TEXT NOT NULL DEFAULT '[]',
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    ignored_json TEXT NOT NULL DEFAULT '[]',
                    edited INTEGER NOT NULL DEFAULT 0,
                    original_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kit_check_items_check ON kit_check_items(check_id, drawing_id, row_index)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kit_extractions (
                    drawing_id INTEGER PRIMARY KEY,
                    signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS drawing_scans (
                    drawing_id INTEGER PRIMARY KEY,
                    signature TEXT NOT NULL,
                    scanner TEXT NOT NULL DEFAULT '',
                    pages_json TEXT NOT NULL,
                    blocks_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
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
    # Keep adding newly shipped terms without deleting customer overrides.
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
