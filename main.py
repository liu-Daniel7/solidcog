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
            det_db_thresh=0.2,
            det_db_box_thresh=0.3,
            rec_batch_num=6,
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
# 工具函数
# ==============================

def pdf_to_images(pdf_path):
    """PDF转图片"""
    from pdf2image import convert_from_path
    return convert_from_path(
        pdf_path,
        dpi=400,
        fmt="png",
        thread_count=2
    )

def enhance_image(img):
    """图像增强（提升30%准确率）"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 去噪
    gray = cv2.medianBlur(gray, 3)
    # 对比度增强
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8)
    )
    enhanced = clahe.apply(gray)
    return enhanced

def ocr_requirement_region(region_img):
    """技术要求专用OCR（更高分辨率）"""
    ocr = get_ocr()
    result = ocr.ocr(
        region_img,
        cls=True,
        rec=True,
        det=True
    )
    text_lines = []
    if result and len(result) > 0:
        for line in result[0]:
            if isinstance(line, list) and len(line) > 1:
                text = line[1][0] if len(line[1]) > 0 else ""
                conf = line[1][1] if len(line[1]) > 1 else 0
                if conf > 0.5:
                    text_lines.append(text)
    return "\n".join(text_lines)

def rotate_if_vertical_text(region):
    """旋转竖排文字"""
    h, w = region.shape[:2]
    if h > w:
        region = cv2.rotate(
            region,
            cv2.ROTATE_90_CLOCKWISE
        )
    return region

def split_regions_for_horizontal_drawing(img):
    """横版区域裁剪"""
    h, w = img.shape[:2]
    # 标题栏（左下）
    title_block = img[
        int(h * 0.72):h,
        0:int(w * 0.18)
    ]
    # 技术要求（左中下）
    tech_block = img[
        int(h * 0.45):int(h * 0.72),
        0:int(w * 0.35)
    ]
    # 旋转竖排文字
    title_block = rotate_if_vertical_text(title_block)
    tech_block = rotate_if_vertical_text(tech_block)
    return {
        "title_block": title_block,
        "tech_block": tech_block
    }

def extract_text(result):
    """文本过滤"""
    texts = []
    if not result or len(result) == 0:
        return ""
    for line in result[0]:
        if isinstance(line, list) and len(line) > 1:
            text = line[1][0] if len(line[1]) > 0 else ""
            conf = line[1][1] if len(line[1]) > 1 else 0
            if conf > 0.5 and len(text) >= 2:
                texts.append(text)
    return "\n".join(texts)

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
            images = pdf_to_images(file_path)
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

        all_text = []

        for i, img in enumerate(images, 1):
            try:
                logger.info(f"处理第 {i} 页图片")
                
                # 直接转 numpy（更稳定）
                cv_img = np.array(img)
                
                # 判断横版
                h, w = cv_img.shape[:2]
                
                if w > h:
                    logger.info("检测到横版图纸")
                    # 横版处理
                    regions = split_regions_for_horizontal_drawing(cv_img)
                    title_img = regions["title_block"]
                    tech_img = regions["tech_block"]
                    
                    # 图像增强
                    title_gray = enhance_image(title_img)
                    tech_gray = enhance_image(tech_img)
                    
                    # OCR
                    title_res = ocr.ocr(title_gray, cls=True)
                    tech_res = ocr.ocr(tech_gray, cls=True)
                    
                    # 提取文本
                    title_text = extract_text(title_res)
                    tech_text = extract_text(tech_res)
                    
                    # 构建结果
                    page_text = ""
                    if title_text:
                        page_text += "[标题栏]\n"
                        page_text += title_text
                        page_text += "\n\n"
                    if tech_text:
                        page_text += "[技术要求]\n"
                        page_text += tech_text
                    
                    if page_text:
                        all_text.append(page_text)
                    
                else:
                    logger.info("检测到竖版图纸")
                    # 竖版处理
                    gray = enhance_image(cv_img)
                    res = ocr.ocr(gray, cls=True)
                    text = extract_text(res)
                    if text:
                        all_text.append(text)
                
            except Exception as e:
                logger.warning(f"处理图片时OCR失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        final_text = "\n\n".join(all_text)

        logger.info(f"最终识别结果长度: {len(final_text)}")

        if final_text:
            logger.info(f"OCR 完成")
            return {"text": final_text, "layout": "horizontal" if len(images) > 0 and images[0].width > images[0].height else "vertical"}
        else:
            logger.warning("OCR 识别结果为空")
            return {"text": "OCR识别结果为空", "layout": "unknown"}

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

            ocr_result = run_ocr(file_path)

            # 处理 OCR 结果
            if isinstance(ocr_result, dict):
                ocr_text = ocr_result.get("text", "")
                layout = ocr_result.get("layout", "unknown")
            else:
                # 兼容性处理
                ocr_text = str(ocr_result)
                layout = "unknown"

            # 强制类型安全
            if not isinstance(ocr_text, str):
                logger.warning(f"OCR text 不是字符串: {type(ocr_text)}")
                ocr_text = str(ocr_text)
            if not isinstance(layout, str):
                layout = str(layout)

            # 防止内容过长（SQLite数据过大）
            ocr_text = ocr_text[:100000]

            logger.info(f"OCR 完成: {new_filename}, 识别长度: {len(ocr_text)}, 布局: {layout}")

            # 写入数据库
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO drawings
                    (filename, file_type, file_size, upload_time, ocr_text, layout)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_filename,
                        ext,
                        file_size,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ocr_text,
                        layout
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
    print(row)

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="未找到图纸")

    return templates.TemplateResponse(
        "ocr_view.html",
        {
            "request": request,
            "filename": row["filename"],
            "ocr_text": row["ocr_text"],
            "layout": row.get("layout", "unknown")
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