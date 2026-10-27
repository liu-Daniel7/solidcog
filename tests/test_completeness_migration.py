import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app import config


class CompletenessMigrationTests(unittest.TestCase):
    def test_old_database_upgrades_in_place_and_keeps_ocr_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE drawings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT,
                    file_type TEXT,
                    file_size INTEGER,
                    upload_time TEXT,
                    title_text TEXT,
                    tech_text TEXT,
                    layout TEXT
                );
                INSERT INTO drawings
                (filename, file_type, file_size, upload_time, title_text, tech_text, layout)
                VALUES ('legacy.pdf', '.pdf', 12, '2026-01-01 10:00:00',
                        '图号: LEGACY-01', '旧技术要求', 'horizontal');
                """
            )
            connection.commit()
            connection.close()
            with patch.object(database, "DATABASE_PATH", path):
                database.init_database()
                database.init_database()
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                columns = [row["name"] for row in connection.execute("PRAGMA table_info(drawings)")]
                self.assertIn("all_text", columns)
                row = connection.execute("SELECT * FROM drawings").fetchone()
                self.assertEqual(row["filename"], "legacy.pdf")
                self.assertEqual(row["title_text"], "图号: LEGACY-01")
                tables = {
                    item[0]
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for name in (
                    "completeness_batches",
                    "completeness_batch_drawings",
                    "completeness_ocr_pages",
                    "completeness_ocr_blocks",
                    "completeness_ocr_tables",
                    "completeness_ocr_cells",
                    "completeness_references",
                    "completeness_results",
                ):
                    self.assertIn(name, tables)
            finally:
                connection.close()

    def test_interrupted_runs_are_marked_failed_not_stuck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with patch.object(database, "DATABASE_PATH", path), patch.object(
                config, "UPLOAD_DIR", Path(directory) / "uploads"
            ):
                Path(directory, "uploads").mkdir()
                database.init_database()
                from app.repositories import completeness as repository

                batch_id = repository.create_batch(
                    name="running",
                    drawing_ids=[],
                    start_drawing_ids=[],
                    rules={"catalog_version": "x"},
                    scope_note="",
                )
                repository.update_batch_fields(batch_id, status="running")
                self.assertEqual(repository.recover_interrupted_runs(), 1)
                batch = repository.get_batch(batch_id)
                self.assertEqual(batch["status"], "failed")
                self.assertIn("中断", batch["error"])


if __name__ == "__main__":
    unittest.main()
