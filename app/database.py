import sqlite3
from contextlib import closing

from app.config import DATABASE_PATH


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
