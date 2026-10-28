import re
from contextlib import closing
from pathlib import Path

from app.database import connect


FIELDS = (
    "id, filename, file_type, file_size, upload_time, title_text, tech_text, "
    "layout, all_text, original_filename, batch_id, page_count, pages_processed, ocr_errors"
)
_STORED_NAME_PREFIX = re.compile(r"^\d{14}_\d{6}_")


def _dict(row):
    return dict(row) if row else None


# 与 database.py 中 CREATE TABLE 的列一致；INSERT 列名只在此声明一次，
# 避免日后加列时 SQL 与参数元组错位。
CREATE_COLUMNS = (
    "filename", "file_type", "file_size", "upload_time",
    "title_text", "tech_text", "all_text", "layout",
    "original_filename", "batch_id", "page_count", "pages_processed", "ocr_errors",
)
_OPTIONAL_DEFAULTS = {
    "original_filename": None,
    "batch_id": "",
    "page_count": 0,
    "pages_processed": 0,
    "ocr_errors": None,
}


def display_filename(row: dict) -> str:
    """Return the original upload name, with a best-effort legacy fallback.

    Old rows only stored ``<timestamp>_<microseconds>_<sanitised name>``.  The
    exact spaces are unrecoverable for those rows, but removing the storage
    prefix is still better than showing a timestamped internal name.  New
    uploads store ``original_filename`` verbatim.
    """
    if not row:
        return ""
    original = str(row.get("original_filename") or "").strip()
    if original:
        return original
    stored = str(row.get("filename") or "")
    stem = Path(stored).name
    return _STORED_NAME_PREFIX.sub("", stem, count=1)


def create(data: dict) -> int:
    values = []
    for key in CREATE_COLUMNS:
        if key in data:
            values.append(data[key])
        elif key in _OPTIONAL_DEFAULTS:
            values.append(_OPTIONAL_DEFAULTS[key])
        else:
            raise KeyError(key)
    if not values[CREATE_COLUMNS.index("original_filename")]:
        values[CREATE_COLUMNS.index("original_filename")] = _STORED_NAME_PREFIX.sub(
            "", Path(str(data["filename"])).name, count=1
        )
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                f"""
                INSERT INTO drawings ({", ".join(CREATE_COLUMNS)})
                VALUES ({", ".join("?" for _ in CREATE_COLUMNS)})
                """,
                tuple(values),
            )
            return cursor.lastrowid


def get(drawing_id: int):
    with closing(connect()) as connection:
        return _dict(connection.execute(
            f"SELECT {FIELDS} FROM drawings WHERE id=?", (drawing_id,)
        ).fetchone())


def list_all(order: str = "ASC") -> list[dict]:
    direction = "DESC" if order.upper() == "DESC" else "ASC"
    with closing(connect()) as connection:
        rows = connection.execute(
            f"SELECT {FIELDS} FROM drawings ORDER BY upload_time {direction}"
        ).fetchall()
    return [dict(row) for row in rows]


def list_page(limit: int = 10, offset: int = 0) -> list[dict]:
    with closing(connect()) as connection:
        rows = connection.execute(
            f"SELECT {FIELDS} FROM drawings ORDER BY upload_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def search(keyword: str, limit: int = 10, offset: int = 0, order: str = "DESC") -> list[dict]:
    direction = "DESC" if order.upper() == "DESC" else "ASC"
    pattern = f"%{keyword}%"
    with closing(connect()) as connection:
        rows = connection.execute(
            f"""
            SELECT {FIELDS} FROM drawings
            WHERE filename LIKE ? OR title_text LIKE ? OR tech_text LIKE ? OR all_text LIKE ?
            ORDER BY upload_time {direction} LIMIT ? OFFSET ?
            """,
            (pattern, pattern, pattern, pattern, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def count() -> int:
    with closing(connect()) as connection:
        return connection.execute("SELECT COUNT(*) FROM drawings").fetchone()[0]


def delete(drawing_id: int) -> bool:
    with closing(connect()) as connection:
        with connection:
            return connection.execute("DELETE FROM drawings WHERE id=?", (drawing_id,)).rowcount > 0


def delete_all() -> list[str]:
    with closing(connect()) as connection:
        with connection:
            filenames = [row[0] for row in connection.execute("SELECT filename FROM drawings")]
            connection.execute("DELETE FROM drawings")
    return filenames
