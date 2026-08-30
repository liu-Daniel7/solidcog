from contextlib import closing

from app.database import connect


FIELDS = "id, filename, file_type, file_size, upload_time, title_text, tech_text, layout, all_text"


def _dict(row):
    return dict(row) if row else None


def create(data: dict) -> int:
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO drawings
                    (filename, file_type, file_size, upload_time, title_text, tech_text, all_text, layout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(data[key] for key in (
                    "filename", "file_type", "file_size", "upload_time",
                    "title_text", "tech_text", "all_text", "layout"
                )),
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
