"""Isolated fixture server for browser QA of the assembly kit check page.

It copies real sample PDFs into a temporary upload directory, seeds deterministic
extraction results for UI interaction testing, and never touches the user's
library/database.  Real extraction is exercised separately by the unit tests
and the sample validation script.
"""
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLE_DIR = Path(os.getenv("KIT_SAMPLE_DIR", Path(__file__).resolve().parents[1] / "齐套性检查示例图纸"))

ROWS = {
    "SK.CNC.0001": [
        ("SK.CNC.0002", "工作台框", "", "1"),
        ("SK.CNC.0029", "工作台架", "", "1"),
        ("SK.CNC.0043", "机头", "", "1"),
        ("SK.CNC.0066", "工作台板", "", "1"),
        ("SK0001", "螺钉", "标准件", "8"),
    ],
    "SK.CNC.0002": [
        ("SK.CNC.0003", "外框", "", "2"),
        ("SK.CNC.0004", "后框", "", "2"),
        ("SK.CNC.0005", "内框", "无图", "1"),
        ("SK0001", "螺钉", "标准件", "4"),
        ("SK.CNC.0071", "控制器", "", "1"),
    ],
    "SK.CNC.0029": [
        ("SK.CNC.0030", "立柱", "", "1"),
        ("SK.CNC.0031", "横梁", "", "1"),
        ("SK.CNC.0007", "立柱垫板", "", "1"),
        ("SK.CNC.0032", "安装板", "", "1"),
        ("SK0002", "螺母", "外购件", "1"),
        ("SK0001", "螺钉", "标准件", "1"),
    ],
    "SK.CNC.0043": [
        ("SK.CNC.0044", "安装板", "", "1"),
        ("SK.CNC.0045", "导向部件", "无图", "2"),
        ("SK0001", "螺钉", "标准件", "2"),
    ],
    "SK.CNC.0066": [
        ("SK.CNC.0067", "台板", "", "1"),
        ("SK.CNC.0045", "导向部件", "无图", "2"),
        ("SK0001", "螺钉", "标准件", "3"),
    ],
    "SK.CNC.0071": [
        ("SK.CNC.0072", "控制盒", "", "1"),
        ("SK.CNC.0073", "按钮", "", "1"),
    ],
}
ASSEMBLIES = set(ROWS)


def main():
    with tempfile.TemporaryDirectory(prefix="solidcog-kit-browser-") as directory:
        root = Path(directory)
        uploads = root / "uploads"
        uploads.mkdir()
        os.environ["SOLIDCOG_DATABASE_PATH"] = str(root / "database.db")
        os.environ["SOLIDCOG_UPLOAD_DIR"] = str(uploads)

        from app.application import create_app
        from app.repositories import drawings
        from app.services import bom_extraction
        from app.services import kit_checks as service
        import uvicorn

        app = create_app()
        ids = {}
        for pdf in sorted(SAMPLE_DIR.glob("*.pdf")):
            original = pdf.name
            stored = f"20261028120000_000001_{original}"
            shutil.copy2(pdf, uploads / stored)
            stem = pdf.stem
            code = stem.split("-", 1)[0]
            title = stem.split("-", 1)[1] if "-" in stem else stem
            drawing_id = drawings.create({
                "filename": stored,
                "original_filename": original,
                "file_type": ".pdf",
                "file_size": pdf.stat().st_size,
                "upload_time": "2026-10-28 12:00:00",
                "title_text": f"{code} {title}",
                "tech_text": "",
                "all_text": ("明细表 序号 代号 名称 备注 数量" if code in ASSEMBLIES else "零件图 技术要求"),
                "layout": "horizontal",
                "page_count": 1,
                "pages_processed": 1,
            })
            ids[code] = drawing_id
        # A model file that must never satisfy a drawing row.
        model_path = SAMPLE_DIR / "SK.CNC.0007-立柱垫板.SLDPRT"
        if model_path.exists():
            shutil.copy2(model_path, uploads / f"20261028120000_000002_{model_path.name}")
            drawings.create({
                "filename": f"20261028120000_000002_{model_path.name}",
                "original_filename": model_path.name,
                "file_type": ".sldprt",
                "file_size": model_path.stat().st_size,
                "upload_time": "2026-10-28 12:00:00",
                "title_text": "",
                "tech_text": "",
                "all_text": "",
                "layout": "unknown",
                "page_count": 0,
                "pages_processed": 0,
            })

        def fake_extract(drawing, force=False):
            code = str(drawing.get("title_text") or "").split(" ", 1)[0]
            time.sleep(0.35)
            rows = ROWS.get(code)
            if not rows:
                return {
                    "status": "no_table",
                    "rows": [],
                    "pages_total": 1,
                    "pages_processed": 1,
                    "issues": [],
                    "table_code": code,
                    "table_name": "",
                    "source": "vision",
                }
            return {
                "status": "ok",
                "rows": [
                    {
                        "page_number": 1,
                        "row_index": index,
                        "sequence_no": str(index),
                        "code": row_code,
                        "name": row_name,
                        "note": row_note,
                        "quantity": row_qty,
                        "raw_text": f"{index} {row_code} {row_name} {row_note} {row_qty}".strip(),
                        "position": {"page": 1},
                        "extraction_status": "ok",
                        "extraction_issue": "",
                    }
                    for index, (row_code, row_name, row_note, row_qty) in enumerate(rows, 1)
                ],
                "pages_total": 1,
                "pages_processed": 1,
                "issues": [],
                "table_code": code,
                "table_name": str(drawing.get("title_text") or "").split(" ", 1)[-1],
                "source": "vision",
            }

        bom_extraction.extract_drawing = fake_extract

        # Seed one completed result for immediate review. Two required files
        # are intentionally left out of the selected set to demonstrate missing
        # rows; the model file is deliberately in the library but not selected.
        selected = [drawing_id for code, drawing_id in ids.items() if code not in {"SK.CNC.0030", "SK.CNC.0067"}]
        seeded = service.create_check(selected, mode="all", root_ids=[], start=False)
        service.run_check(seeded["id"])

        # Also expose the full PDF set for the manual-mode browser flow.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        print(f"QA_URL=http://127.0.0.1:{sock.getsockname()[1]} SEEDED_CHECK={seeded['id']}", flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[sock])


if __name__ == "__main__":
    main()
