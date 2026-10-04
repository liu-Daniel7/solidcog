"""Isolated fixture server for browser_workbench.cjs; never uses user drawings."""
import os
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="solidcog-browser-") as directory:
    os.environ["SOLIDCOG_DATABASE_PATH"] = str(Path(directory) / "database.db")
    os.environ["SOLIDCOG_UPLOAD_DIR"] = str(Path(directory) / "uploads")
    from app.application import create_app
    from app.repositories import drawings
    import uvicorn

    app = create_app()
    for name in (
        "BJ-SG2-1-09A-12_轴承座.pdf",
        "联轴器装配图_A3_技术要求与尺寸公差复核_修订版_2026-10-04.pdf",
        "传动齿轮_材料45钢.png",
        "安装支架_零件图.pdf",
    ):
        drawings.create({
            "filename": name, "file_type": Path(name).suffix,
            "file_size": 1258291, "upload_time": "2026-10-04 15:30:22",
            "title_text": "轴承座 · 材料45钢", "tech_text": "去除毛刺，未注公差按图纸要求。",
            "all_text": "轴承座 · 材料45钢\n去除毛刺，未注公差按图纸要求。", "layout": "horizontal",
        })
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(f"QA_URL=http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[sock])
