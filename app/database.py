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
            _init_completeness_schema(connection)
            _seed_glossary(connection)


def _init_completeness_schema(connection: sqlite3.Connection) -> None:
    """Add completeness-check tables without touching legacy OCR rows.

    The production database predates this feature and is upgraded in place.
    Every statement is additive and idempotent so an interrupted upgrade can be
    retried safely. Original drawings and OCR columns remain unchanged.
    """
    statements = [
        """
        CREATE TABLE IF NOT EXISTS completeness_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            rules_json TEXT NOT NULL DEFAULT '{}',
            rules_version INTEGER NOT NULL DEFAULT 1,
            start_drawing_ids_json TEXT NOT NULL DEFAULT '[]',
            scope_note TEXT NOT NULL DEFAULT '',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_batch_drawings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            drawing_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL,
            UNIQUE(batch_id, drawing_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_ocr_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drawing_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            width INTEGER,
            height INTEGER,
            backend TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE(drawing_id, page_number)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_ocr_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            block_index INTEGER NOT NULL,
            block_type TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            confidence REAL,
            source TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_ocr_tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            table_index INTEGER NOT NULL,
            bbox_json TEXT,
            confidence REAL,
            source TEXT NOT NULL DEFAULT '',
            row_count INTEGER NOT NULL DEFAULT 0,
            column_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_ocr_cells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id INTEGER NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            row_span INTEGER NOT NULL DEFAULT 1,
            column_span INTEGER NOT NULL DEFAULT 1,
            text TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            confidence REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            source_drawing_id INTEGER NOT NULL,
            source_page_number INTEGER,
            table_index INTEGER,
            row_index INTEGER,
            cell_index INTEGER,
            raw_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            target_drawing_id INTEGER,
            match_status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            confidence REAL,
            source_text TEXT NOT NULL DEFAULT '',
            duplicate_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS completeness_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            rules_version INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL,
            code TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            source_drawing_id INTEGER,
            source_filename TEXT NOT NULL DEFAULT '',
            page_number INTEGER,
            table_index INTEGER,
            row_index INTEGER,
            column_index INTEGER,
            field_name TEXT NOT NULL DEFAULT '',
            field_value TEXT NOT NULL DEFAULT '',
            target_drawing_id INTEGER,
            target_filename TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            confidence REAL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            reference_id INTEGER,
            confirmed_status TEXT NOT NULL DEFAULT 'unconfirmed',
            confirmed_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_completeness_members_batch ON completeness_batch_drawings(batch_id, position)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_members_drawing ON completeness_batch_drawings(drawing_id)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_pages_drawing ON completeness_ocr_pages(drawing_id, page_number)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_blocks_page ON completeness_ocr_blocks(page_id, block_index)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_tables_page ON completeness_ocr_tables(page_id, table_index)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_cells_table ON completeness_ocr_cells(table_id, row_index, column_index)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_refs_batch ON completeness_references(batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_refs_source ON completeness_references(source_drawing_id)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_results_batch ON completeness_results(batch_id, status, id)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_results_source ON completeness_results(source_drawing_id)",
        "CREATE INDEX IF NOT EXISTS idx_completeness_results_target ON completeness_results(target_drawing_id)",
    ]
    for statement in statements:
        connection.execute(statement)


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
