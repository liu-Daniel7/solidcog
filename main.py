import os
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sqlite3
import shutil
import cv2
import numpy as np
from pdf2image import convert_from_path

from layout_splitter import split_regions

# ==============================
# 全局 OCR（只初始化一次）
# ==============================

ocr_engine = None

def get_ocr():

    global ocr_engine

    if ocr_engine is None:

        logger.info("初始化 PaddleOCR（仅一次）")

        from paddleocr import PaddleOCR

        ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            show_log=False
        )

    return ocr_engine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 配置
UPLOAD_DIR = "uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

app = FastAPI(title="工程数字图纸智能管理系统")

templates = Jinja2Templates(directory="templates")

# 创建 uploads 文件夹
if not os.path.exists(UPLOAD_DIR):
    try:
        os.makedirs(UPLOAD_DIR)
        logger.info(f"创建上传目录: {UPLOAD_DIR}")
    except Exception as e:
        logger.error(f"创建上传目录失败: {e}")

# 挂载静态文件
app.mount(
    "/uploads",
    StaticFiles(directory=UPLOAD_DIR),
    name="uploads"
)

# ==============================
# 数据库操作工具
# ==============================

def get_db_connection():
    """获取数据库连接"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"连接数据库失败: {e}")
        raise HTTPException(status_code=500, detail="数据库连接失败")


# ==============================
# OCR 识别函数
# ==============================

def run_ocr(file_path):
    """
    对 PDF 文件或 PNG 图片执行 OCR
    """
    try:
        logger.info("开始 OCR 识别")

        # 使用全局 OCR 引擎
        ocr = get_ocr()

        # 检查文件类型
        ext = os.path.splitext(file_path)[1].lower()
        images = []

        if ext == ".pdf":
            # PDF 转图片，设置DPI为400以提高识别率
            images = convert_from_path(
                file_path,
                dpi=400
            )
            logger.info(f"PDF 转图片完成，共 {len(images)} 页")
        elif ext == ".png":
            # 直接使用 PNG 图片
            from PIL import Image
            img = Image.open(file_path)
            images.append(img)
            logger.info("PNG 图片加载完成")
        else:
            logger.error(f"不支持的文件类型: {ext}")
            return {"text": "不支持的文件类型", "layout": "unknown"}

        text_result = []
        layout = "unknown"

        for i, img in enumerate(images, 1):
            try:
                logger.info(f"处理第 {i} 页图片")
                
                # 直接转 numpy（更稳定）
                cv_img = np.array(img)
                
                try:
                    regions = split_regions(cv_img)
                    layout = regions["layout"]
                    title_block = regions["title_block"]
                    tech_block = regions["tech_requirement"]
                except Exception as e:
                    logger.warning(f"区域分割失败，使用整图 OCR: {e}")
                    layout = "unknown"
                    title_block = cv_img
                    tech_block = cv_img
                
                # 提升识别率
                title_gray = cv2.cvtColor(title_block, cv2.COLOR_BGR2GRAY)
                tech_gray = cv2.cvtColor(tech_block, cv2.COLOR_BGR2GRAY)
                
                # 对标题栏进行OCR
                title_result = ocr.ocr(title_gray, cls=True)
                if title_result:
                    for page_result in title_result:
                        if isinstance(page_result, list):
                            for text_box in page_result:
                                if isinstance(text_box, list) and len(text_box) == 2:
                                    if isinstance(text_box[1], tuple) and len(text_box[1]) > 0:
                                        text = text_box[1][0]
                                        text_result.append(f"[标题栏] {text}")
                                        logger.info(f"标题栏识别到: {text}")
                
                # 对技术要求进行OCR
                tech_result = ocr.ocr(tech_gray, cls=True)
                if tech_result:
                    for page_result in tech_result:
                        if isinstance(page_result, list):
                            for text_box in page_result:
                                if isinstance(text_box, list) and len(text_box) == 2:
                                    if isinstance(text_box[1], tuple) and len(text_box[1]) > 0:
                                        text = text_box[1][0]
                                        text_result.append(f"[技术要求] {text}")
                                        logger.info(f"技术要求识别到: {text}")
                
                logger.info(f"当前识别到的文本数量: {len(text_result)}")
            except Exception as e:
                logger.warning(f"处理图片时OCR失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        final_text = "\n".join(text_result)

        logger.info(f"最终识别结果长度: {len(final_text)}")
        logger.info(f"图纸布局: {layout}")

        if final_text:
            logger.info(f"OCR 完成，识别到 {len(text_result)} 行文本")
            return {"text": final_text, "layout": layout}
        else:
            logger.warning("OCR 识别结果为空")
            return {"text": "OCR识别结果为空", "layout": layout}

    except Exception as e:
        logger.error(f"OCR 失败: {e}")
        import traceback
        traceback.print_exc()
        return {"text": f"OCR识别失败: {str(e)}", "layout": "unknown"}

# ==============================
# 初始化数据库
# ==============================

def init_database():
    """初始化数据库"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS drawings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            file_type TEXT,
            file_size INTEGER,
            upload_time TEXT,
            ocr_text TEXT,
            layout TEXT
        )
        """)

        conn.commit()
        logger.info("数据库初始化成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()

init_database()

# ==============================
# 首页（系统状态）
# ==============================

@app.get("/")
def system_status():
    """系统状态接口"""
    try:
        return {
            "系统名称": "工程数字图纸智能管理系统",
            "运行状态": "系统运行正常",
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"系统状态接口错误: {e}")
        raise HTTPException(status_code=500, detail="系统状态查询失败")

# ==============================
# 上传 PDF 图纸
# ==============================

@app.post("/upload-drawing")
def upload_drawing(files: list[UploadFile] = File(...)):
    """上传 PDF 图纸或 PNG 图片（支持批量上传）"""
    try:
        # 检查文件数量
        if len(files) == 0:
            raise HTTPException(status_code=400, detail="请选择至少一个文件")

        # 处理每个文件
        for file in files:
            # 检查文件类型
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in [".pdf", ".png"]:
                raise HTTPException(status_code=400, detail=f"只允许上传 PDF 或 PNG 格式文件，{file.filename} 不是支持的格式")

            # 检查文件大小
            file.file.seek(0, 2)  # 移动到文件末尾
            file_size = file.file.tell()  # 获取文件大小
            file.file.seek(0)  # 重置文件指针

            if file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"文件过大，最大允许 50MB，{file.filename} 超过限制")

            # 生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S_%f")
            # 清理文件名，防止路径遍历攻击
            safe_filename = "".join(c for c in file.filename if c.isalnum() or c in ('_', '-', '.'))
            new_filename = f"{timestamp}_{safe_filename}"
            file_path = os.path.join(UPLOAD_DIR, new_filename)

            # 保存文件（流式处理）
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # ==============================
            # 执行 OCR
            # ==============================

            ocr_text = run_ocr(file_path)
            logger.info(f"OCR 完成: {new_filename}, 识别长度: {len(ocr_text)}")

            # 写入数据库
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO drawings
                    (filename, file_type, file_size, upload_time, ocr_text)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        new_filename,
                        ext,
                        file_size,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ocr_text
                    )
                )
                conn.commit()
                logger.info(f"图纸上传成功: {new_filename}")
            finally:
                if 'conn' in locals():
                    conn.close()

        # 重定向到主页
        return RedirectResponse(url="/主页", status_code=303)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error("上传图纸失败")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# ==============================
# 查看图纸列表
# ==============================

@app.get("/drawings")
def get_drawings():
    """获取图纸列表"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, filename, file_type, file_size, upload_time FROM drawings ORDER BY upload_time ASC"
        )
        rows = cursor.fetchall()

        drawings_list = []
        for i, row in enumerate(rows, 1):
            drawings_list.append({
                "编号": i,
                "id": row["id"],
                "文件名": row["filename"],
                "文件类型": row["file_type"],
                "文件大小(字节)": row["file_size"],
                "上传时间": row["upload_time"]
            })

        return {
            "图纸数量": len(drawings_list),
            "图纸信息": drawings_list
        }
    except Exception as e:
        logger.error(f"获取图纸列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取图纸列表失败")
    finally:
        if 'conn' in locals():
            conn.close()

# ==============================
# 删除图纸
# ==============================

@app.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int):
    """删除图纸"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 查询文件名
        cursor.execute(
            "SELECT filename FROM drawings WHERE id=?",
            (drawing_id,)
        )
        result = cursor.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="未找到该图纸")

        filename = result["filename"]
        file_path = os.path.join(UPLOAD_DIR, filename)

        # 删除文件
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"删除文件成功: {file_path}")
            except Exception as e:
                logger.error(f"删除文件失败: {e}")
                # 继续执行数据库删除，不因为文件删除失败而中断

        # 删除数据库记录
        cursor.execute(
            "DELETE FROM drawings WHERE id=?",
            (drawing_id,)
        )
        conn.commit()
        logger.info(f"删除图纸成功: ID={drawing_id}")

        # 重定向到主页
        return RedirectResponse(url="/主页", status_code=303)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除图纸失败: {e}")
        raise HTTPException(status_code=500, detail="删除图纸失败")
    finally:
        if 'conn' in locals():
            conn.close()

# ==============================
# 主页
# ==============================

@app.get("/home", response_class=HTMLResponse)
def home(request: Request):
    """显示主页"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, filename, file_type, file_size, upload_time FROM drawings ORDER BY upload_time ASC"
        )
        rows = cursor.fetchall()

        # 转换为列表，包含动态编号
        图纸列表 = []
        for i, row in enumerate(rows, 1):
            # 保持与前端模板兼容的格式，使用元组
            图纸列表.append((i, row["filename"], row["file_type"], row["file_size"], row["upload_time"], row["id"]))

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "图纸列表": 图纸列表
            }
        )
    except Exception as e:
        logger.error(f"显示主页失败: {e}")
        raise HTTPException(status_code=500, detail="显示主页失败")
    finally:
        if 'conn' in locals():
            conn.close()


@app.get("/搜索图纸", response_class=HTMLResponse)
def search_drawings(
    request: Request,
    keyword: str = "",
    page: int = 1,
    sort: str = "desc"
):

    PAGE_SIZE = 10

    conn = get_db_connection()
    cursor = conn.cursor()

    offset = (page - 1) * PAGE_SIZE

    order_sql = "DESC" if sort == "desc" else "ASC"

    cursor.execute(
        f"""
        SELECT id, filename, file_type, file_size, upload_time
        FROM drawings
        WHERE filename LIKE ?
           OR ocr_text LIKE ?
        ORDER BY upload_time {order_sql}
        LIMIT ? OFFSET ?
        """,
        (
            f"%{keyword}%",
            f"%{keyword}%",
            PAGE_SIZE,
            offset
        )
    )

    rows = cursor.fetchall()

    图纸列表 = []

    for i, row in enumerate(rows, 1):

        图纸列表.append(
            (
                i,
                row["filename"],
                row["file_type"],
                row["file_size"],
                row["upload_time"],
                row["id"]
            )
        )

    conn.close()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "图纸列表": 图纸列表,
            "search_keyword": keyword,
            "page": page,
            "sort": sort
        }
    )

# 保留原有中文路由以保持兼容性
@app.get("/系统状态")
def system_status_cn():
    return system_status()

@app.post("/上传图纸")
def upload_drawing_cn(files: list[UploadFile] = File(...)):
    return upload_drawing(files)

@app.get("/图纸列表")
def get_drawings_cn():
    return get_drawings()

@app.get("/删除图纸/{drawing_id}")
def delete_drawing_cn(drawing_id: int):
    return delete_drawing(drawing_id)


@app.get("/删除所有图纸")
def delete_all_drawings():
    """删除所有图纸"""
    try:
        # 连接数据库
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 获取所有文件名
        cursor.execute("SELECT filename FROM drawings")
        files = cursor.fetchall()
        
        # 删除数据库记录
        cursor.execute("DELETE FROM drawings")
        conn.commit()
        
        # 删除文件系统中的文件
        for file in files:
            filename = file["filename"]
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"删除文件成功: {file_path}")
                except Exception as e:
                    logger.error(f"删除文件失败: {e}")
        
        logger.info("删除所有图纸成功")
        
        # 重定向到主页
        return RedirectResponse(url="/主页", status_code=303)
    except Exception as e:
        logger.error(f"删除所有图纸失败: {e}")
        raise HTTPException(status_code=500, detail="删除所有图纸失败")
    finally:
        if 'conn' in locals():
            conn.close()


@app.get("/主页", response_class=HTMLResponse)
def home_cn(request: Request):
    return home(request)


@app.get("/ocr/{drawing_id}")
def get_ocr_text(drawing_id: int):

    try:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT filename, ocr_text FROM drawings WHERE id=?",
            (drawing_id,)
        )

        row = cursor.fetchone()

        if not row:

            raise HTTPException(
                status_code=404,
                detail="未找到图纸"
            )

        return {
            "文件名": row["filename"],
            "OCR识别内容": row["ocr_text"]
        }

    finally:

        conn.close()


@app.get("/查看OCR/{drawing_id}", response_class=HTMLResponse)
def view_ocr(request: Request, drawing_id: int):

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT filename, ocr_text, layout FROM drawings WHERE id=?",
        (drawing_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="未找到图纸")

    return templates.TemplateResponse(
        "ocr_view.html",
        {
            "request": request,
            "filename": row["filename"],
            "ocr_text": row["ocr_text"],
            "layout": row["layout"]
        }
    )


@app.get("/导出OCR/{drawing_id}")
def export_ocr(drawing_id: int):

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT filename, ocr_text FROM drawings WHERE id=?",
        (drawing_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="未找到图纸")

    filename = row["filename"]
    text = row["ocr_text"] or ""

    export_name = filename + ".txt"

    return PlainTextResponse(
        text,
        headers={
            "Content-Disposition": f"attachment; filename={export_name}"
        }
    )