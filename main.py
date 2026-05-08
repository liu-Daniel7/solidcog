import os
import logging
import traceback
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests

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
        logger.info("初始化 PaddleOCR")

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
    """
    横版工程图通用裁切（更大、更稳）
    """

    h, w = img.shape[:2]

    # =========================
    # 标题栏（左下）——扩大
    # =========================

    title_block = img[
        int(h * 0.65):h,
        0:int(w * 0.25)
    ]

    # =========================
    # 技术要求（左中）
    # =========================

    tech_block = img[
        int(h * 0.35):int(h * 0.70),
        0:int(w * 0.50)
    ]

    # 旋转竖排文字
    title_block = rotate_if_vertical_text(title_block)
    tech_block = rotate_if_vertical_text(tech_block)

    # 调试图
    cv2.imwrite("debug_horizontal_title.png", title_block)
    cv2.imwrite("debug_horizontal_tech.png", tech_block)

    return {
        "title_block": title_block,
        "tech_block": tech_block
    }

def split_regions_for_vertical_drawing(img):
    """
    竖版工程图通用裁切（更大、更稳、更通用）
    适用于 90% 机械图纸
    """

    h, w = img.shape[:2]

    # =========================
    # 标题栏（右下）——放大范围
    # =========================

    title_block = img[
        int(h * 0.65):h,
        int(w * 0.50):w
    ]

    # =========================
    # 技术要求（左侧大区域）
    # =========================

    tech_block = img[
        int(h * 0.10):int(h * 0.70),
        0:int(w * 0.70)
    ]

    # ===== 调试用（强烈建议保留） =====

    cv2.imwrite("debug_vertical_title.png", title_block)
    cv2.imwrite("debug_vertical_tech.png", tech_block)

    return {
        "title_block": title_block,
        "tech_block": tech_block
    }

def run_global_ocr(img):
    """
    全局OCR（整页）
    """

    ocr = get_ocr()

    gray = enhance_image(img)

    result = ocr.ocr(
        gray,
        cls=True
    )

    return extract_text(result)

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

        # 检查是否使用千问VL OCR
        if USE_QWEN_VL_OCR:
            logger.info("使用千问VL进行OCR识别")
            
            # 保存临时图片用于千问VL
            temp_image_path = "temp_ocr_image.png"
            if len(images) > 0:
                images[0].save(temp_image_path, quality=95)
                
                # 使用千问VL进行OCR
                result = ocr_with_qwen_vl(temp_image_path)
                
                # 清理临时文件
                if os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
                
                logger.info(f"千问VL OCR完成，标题栏: {len(result.get('title_block', ''))} 字符, 技术要求: {len(result.get('tech_block', ''))} 字符")
                return result
            else:
                logger.error("没有图片可处理")
                return {
                    "title_block": "",
                    "tech_block": "",
                    "all_text": "",
                    "layout": "unknown"
                }
        else:
            logger.info("使用PaddleOCR进行识别")
            # 使用全局 OCR 引擎
            ocr = get_ocr()

        all_text = []

        for i, img in enumerate(images, 1):
            try:
                logger.info(f"处理第 {i} 页图片")
                
                # 直接转 numpy（更稳定）
                cv_img = np.array(img)
                
                # =========================
                # 全局 OCR（整页）
                # =========================
                
                global_text = run_global_ocr(cv_img)
                
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
                    
                    # 保存结果
                    page_result = {
                        "title_block": title_text,
                        "tech_block": tech_text,
                        "all_text": global_text,
                        "layout": "horizontal"
                    }
                    all_text.append(page_result)
                    
                else:
                    logger.info("检测到竖版图纸")
                    # 竖版处理
                    regions = split_regions_for_vertical_drawing(cv_img)
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
                    
                    # 保存结果
                    page_result = {
                        "title_block": title_text,
                        "tech_block": tech_text,
                        "all_text": global_text,
                        "layout": "vertical"
                    }
                    all_text.append(page_result)
                
            except Exception as e:
                logger.warning(f"处理图片时OCR失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        # 合并结果
        title_texts = []
        tech_texts = []
        all_texts = []
        layout = "unknown"
        
        for page_result in all_text:
            if isinstance(page_result, dict):
                title_texts.append(page_result.get("title_block", ""))
                tech_texts.append(page_result.get("tech_block", ""))
                all_texts.append(page_result.get("all_text", ""))
                if layout == "unknown":
                    layout = page_result.get("layout", "unknown")
        
        final_title = "\n\n".join(filter(None, title_texts))
        final_tech = "\n\n".join(filter(None, tech_texts))
        final_all = "\n\n".join(filter(None, all_texts))

        logger.info(f"OCR 完成，标题栏长度: {len(final_title)}, 技术要求长度: {len(final_tech)}, 全局OCR长度: {len(final_all)}")

        return {
            "title_block": final_title,
            "tech_block": final_tech,
            "all_text": final_all,
            "layout": layout
        }

    except Exception as e:
        logger.error(f"OCR 失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            "title_block": f"OCR识别失败: {str(e)}",
            "tech_block": "",
            "all_text": "",
            "layout": "unknown"
        }

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
            title_text TEXT,
            tech_text TEXT,
            layout TEXT
        )
        """)

        # 自动补充字段（安全）
        cursor.execute("""
        PRAGMA table_info(drawings)
        """)

        columns = [col[1] for col in cursor.fetchall()]

        if "all_text" not in columns:
            cursor.execute("""
            ALTER TABLE drawings
            ADD COLUMN all_text TEXT
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
                title_text = ocr_result.get("title_block", "")
                tech_text = ocr_result.get("tech_block", "")
                all_text = ocr_result.get("all_text", "")
                layout = ocr_result.get("layout", "unknown")
            else:
                # 兼容性处理
                title_text = str(ocr_result)
                tech_text = ""
                all_text = ""
                layout = "unknown"

            # 强制类型安全
            if not isinstance(title_text, str):
                logger.warning(f"title_text 不是字符串: {type(title_text)}")
                title_text = str(title_text)
            if not isinstance(tech_text, str):
                logger.warning(f"tech_text 不是字符串: {type(tech_text)}")
                tech_text = str(tech_text)
            if not isinstance(all_text, str):
                logger.warning(f"all_text 不是字符串: {type(all_text)}")
                all_text = str(all_text)
            if not isinstance(layout, str):
                layout = str(layout)

            # 防止内容过长（SQLite数据过大）
            title_text = title_text[:100000]
            tech_text = tech_text[:100000]
            all_text = all_text[:100000]

            logger.info(f"OCR 完成: {new_filename}, 标题栏长度: {len(title_text)}, 技术要求长度: {len(tech_text)}, 全局OCR长度: {len(all_text)}, 布局: {layout}")

            # 写入数据库
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO drawings
                    (
                        filename,
                        file_type,
                        file_size,
                        upload_time,
                        title_text,
                        tech_text,
                        all_text,
                        layout
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_filename,
                        ext,
                        file_size,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        title_text,
                        tech_text,
                        all_text,
                        layout
                    )
                )
                conn.commit()
                logger.info(f"图纸上传成功: {new_filename}")
            finally:
                if 'conn' in locals():
                    conn.close()

        # 重定向到主页
        return RedirectResponse(url="/home", status_code=303)
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
        return RedirectResponse(url="/home", status_code=303)
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


@app.get("/search", response_class=HTMLResponse)
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
           OR title_text LIKE ?
           OR tech_text LIKE ?
           OR all_text LIKE ?
        ORDER BY upload_time {order_sql}
        LIMIT ? OFFSET ?
        """,
        (
            f"%{keyword}%",
            f"%{keyword}%",
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
@app.get("/status")
def system_status_cn():
    return system_status()

@app.post("/upload")
def upload_drawing_cn(files: list[UploadFile] = File(...)):
    return upload_drawing(files)

@app.get("/drawings-list")
def get_drawings_cn():
    return get_drawings()

@app.get("/delete-drawing/{drawing_id}")
def delete_drawing_cn(drawing_id: int):
    return delete_drawing(drawing_id)


@app.get("/delete-all-drawings")
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
        return RedirectResponse(url="/home", status_code=303)
    except Exception as e:
        logger.error(f"删除所有图纸失败: {e}")
        raise HTTPException(status_code=500, detail="删除所有图纸失败")
    finally:
        if 'conn' in locals():
            conn.close()


@app.get("/home", response_class=HTMLResponse)
def home_cn(request: Request):
    return home(request)


@app.get("/ocr/{drawing_id}")
def get_ocr_text(drawing_id: int):

    try:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT filename, title_text, tech_text, all_text FROM drawings WHERE id=?",
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
            "标题栏": row["title_text"] or "",
            "技术要求": row["tech_text"] or "",
            "全局OCR": row["all_text"] or ""
        }

    finally:

        conn.close()


@app.get("/view-ocr/{drawing_id}", response_class=HTMLResponse)
def view_ocr(request: Request, drawing_id: int):

    try:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT 
                filename, 
                title_text, 
                tech_text, 
                all_text,
                layout
            FROM drawings
            WHERE id=?
            """,
            (drawing_id,)
        )

        row = cursor.fetchone()

        conn.close()

        if not row:

            raise HTTPException(
                status_code=404,
                detail="未找到图纸"
            )

        # 防止 None
        filename = row["filename"] or ""

        title_text = row["title_text"] or ""

        tech_text = row["tech_text"] or ""

        all_text = row["all_text"] or ""

        layout = row["layout"] or "unknown"

        # 强制字符串（非常关键）
        filename = str(filename)

        title_text = str(title_text)

        tech_text = str(tech_text)

        all_text = str(all_text)

        layout = str(layout)

        # 防止文本过大导致模板崩
        if len(title_text) > 200000:

            title_text = title_text[:200000]

        if len(tech_text) > 200000:

            tech_text = tech_text[:200000]

        if len(all_text) > 200000:

            all_text = all_text[:200000]

        return templates.TemplateResponse(
            "ocr_view.html",
            {
                "request": request,
                "filename": filename,
                "title_text": title_text,
                "tech_text": tech_text,
                "all_text": all_text,
                "layout": layout
            }
        )

    except Exception as e:

        traceback.print_exc()

        return HTMLResponse(
            content=f"""
            <h2>查看 OCR 出错</h2>
            <p>{str(e)}</p>
            """,
            status_code=500
        )


@app.get("/export-ocr/{drawing_id}")
def export_ocr(drawing_id: int):

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT filename, title_text, tech_text, all_text FROM drawings WHERE id=?",
        (drawing_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="未找到图纸")

    filename = row["filename"]
    title_text = row["title_text"] or ""
    tech_text = row["tech_text"] or ""
    all_text = row["all_text"] or ""
    text = f"标题栏:\n{title_text}\n\n技术要求:\n{tech_text}\n\n全局OCR:\n{all_text}"

    export_name = filename + ".txt"

    return PlainTextResponse(
        text,
        headers={
            "Content-Disposition": f"attachment; filename={export_name}"
        }
    )

# ==============================
# DeepSeek API 集成
# ==============================

# 定义请求体结构
class ChatRequest(BaseModel):
    prompt: str
    # 可选 deepseek-chat 或 deepseek-reasoner
    model: str = "deepseek-chat"

# 注意：实际开发中请从环境变量读取
DEEPSEEK_API_KEY = "sk-b30d812092ab409ab787baf82f263e69"

class ChatWithDrawingRequest(BaseModel):
    prompt: str
    drawing_id: int
    model: str = "qwen-vl-plus"

@app.post("/chat-with-drawing")
def chat_with_drawing(request: ChatWithDrawingRequest):
    """
    使用图纸上下文进行聊天
    1. 获取图纸的OCR信息
    2. 用千问VL重新分析图纸图片
    3. 结合OCR和分析结果回答问题
    """
    try:
        drawing_id = request.drawing_id
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filename, title_text, tech_text, all_text FROM drawings WHERE id=?",
            (drawing_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail="未找到该图纸")
        
        filename = row["filename"]
        ocr_title = row["title_text"] or ""
        ocr_tech = row["tech_text"] or ""
        ocr_all = row["all_text"] or ""
        
        image_path = os.path.join(UPLOAD_DIR, filename)
        if not os.path.exists(image_path):
            raise HTTPException(status_code=404, detail="图纸文件不存在")
        
        ocr_with_qwen_vl_result = ocr_with_qwen_vl(image_path, "plus")
        
        qwen_title = ocr_with_qwen_vl_result.get("title_block", "")
        qwen_tech = ocr_with_qwen_vl_result.get("tech_block", "")
        qwen_all = ocr_with_qwen_vl_result.get("all_text", "")
        
        context_prompt = f"""你是一个专业的工程图纸技术助手。请根据以下图纸信息回答用户的问题。

【图纸文件名】
{filename}

【PaddleOCR识别结果】
【标题栏】
{ocr_title}

【技术要求】
{ocr_tech}

【全局OCR】
{ocr_all}

【千问VL智能分析结果】
【标题栏分析】
{qwen_title}

【技术要求分析】
{qwen_tech}

【完整分析】
{qwen_all}

【用户问题】
{request.prompt}

请基于以上图纸信息，专业、准确地回答用户的问题。如果信息不足，请明确指出。"""

        from openai import OpenAI
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url=QWEN_BASE_URL
        )
        
        completion = client.chat.completions.create(
            model=request.model,
            messages=[{"role": "user", "content": context_prompt}],
            stream=False
        )
        
        answer = completion.choices[0].message.content
        
        return {
            "success": True,
            "answer": answer,
            "drawing_id": drawing_id,
            "filename": filename,
            "ocr_info": {
                "title": ocr_title,
                "tech": ocr_tech,
                "all": ocr_all
            },
            "qwen_analysis": {
                "title": qwen_title,
                "tech": qwen_tech,
                "all": qwen_all
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图纸上下文聊天失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"聊天失败: {str(e)}")

@app.post("/chat")
def chat(request: ChatRequest):
    try:
        if "qwen" in request.model.lower():
            from openai import OpenAI
            client = OpenAI(
                api_key=QWEN_API_KEY,
                base_url=QWEN_BASE_URL
            )
            
            completion = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                stream=False
            )
            return completion.choices[0].message.content
        else:
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": request.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False
            }

            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"API 调用失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"API 调用失败: {str(e)}")

# ==============================
# 千问VL API 集成（混合策略）
# ==============================

import base64
from io import BytesIO
from PIL import Image

# 千问API配置 - 阿里云DashScope
QWEN_API_KEY = "REDACTED_QWEN_API_KEY"  # 千问API密钥
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 千问VL模型选择
QWEN_VL_MODELS = {
    "plus": "qwen-vl-plus",      # 性价比最高，主力使用
    "max": "qwen-vl-max",        # 最高精度，复杂图纸使用
    "chat": "qwen-vl-chat"       # 轻量级，简单问答使用
}

class QwenVLRequest(BaseModel):
    image_path: str
    prompt: str = "请分析这张图纸，提取标题栏内容、技术要求、图号、比例尺、设计单位等关键信息，并判断是横版还是竖版图纸。"
    model: str = "plus"  # 可选: plus, max, chat

def encode_image_to_base64(image_path):
    """将图片编码为base64字符串"""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"图片编码失败: {e}")
        raise HTTPException(status_code=500, detail=f"图片编码失败: {str(e)}")

def analyze_with_qwen_vl(image_path: str, prompt: str, model_type: str = "plus") -> dict:
    """
    使用千问VL分析图纸（支持混合策略）
    
    Args:
        image_path: 图片路径
        prompt: 分析提示词
        model_type: 模型类型 (plus/max/chat)
    
    Returns:
        dict: 包含分析结果的字典
    """
    try:
        # 选择模型
        model_name = QWEN_VL_MODELS.get(model_type, "qwen-vl-plus")
        logger.info(f"使用千问VL模型: {model_name} 分析图纸: {image_path}")
        
        # 编码图片
        image_base64 = encode_image_to_base64(image_path)
        
        # 使用OpenAI SDK调用阿里云DashScope API
        from openai import OpenAI
        client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url=QWEN_BASE_URL
        )
        
        # 构建消息内容
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]
        
        # 调用API
        completion = client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=False
        )
        
        # 获取结果
        analysis_text = completion.choices[0].message.content
        
        logger.info(f"千问VL分析完成，结果长度: {len(analysis_text)}")
        
        return {
            "success": True,
            "model": model_name,
            "result": analysis_text,
            "image_path": image_path
        }
        
    except Exception as e:
        logger.error(f"千问VL分析失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/analyze-drawing")
def analyze_drawing(request: QwenVLRequest):
    """
    使用千问VL分析图纸的API端点
    
    支持三种模式:
    - plus: 性价比最高，适合一般图纸分析
    - max: 最高精度，适合复杂图纸
    - chat: 轻量级，适合简单问答
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(request.image_path):
            raise HTTPException(status_code=404, detail="图纸文件不存在")
        
        # 调用千问VL分析
        result = analyze_with_qwen_vl(
            image_path=request.image_path,
            prompt=request.prompt,
            model_type=request.model
        )
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "分析失败"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析图纸失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"分析图纸失败: {str(e)}")

@app.post("/analyze-drawing-simple")
def analyze_drawing_simple(
    image_path: str,
    question: str = "这张图纸的主要内容是什么？",
    model: str = "chat"
):
    """
    简单问答模式 - 使用轻量级模型快速回答
    """
    try:
        if not os.path.exists(image_path):
            raise HTTPException(status_code=404, detail="图纸文件不存在")
        
        result = analyze_with_qwen_vl(
            image_path=image_path,
            prompt=question,
            model_type=model
        )
        
        if result["success"]:
            return {"success": True, "answer": result["result"]}
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "分析失败"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"简单问答失败: {e}")
        raise HTTPException(status_code=500, detail=f"简单问答失败: {str(e)}")

# ==============================
# 千问VL OCR 函数
# ==============================

def ocr_with_qwen_vl(image_path: str, model_type: str = "plus") -> dict:
    """
    使用千问VL进行OCR识别（替代PaddleOCR）
    
    Args:
        image_path: 图片路径
        model_type: 模型类型 (plus/max/chat)
    
    Returns:
        dict: 包含识别结果的字典
    """
    try:
        logger.info(f"使用千问VL进行OCR识别: {image_path}")
        
        # 构建提示词
        prompt = """请分析这张工程图纸，提取以下信息：
1. 标题栏内容（包括图号、名称、比例尺等）
2. 技术要求部分的所有文字
3. 图纸中的尺寸标注和公差信息
4. 其他重要的技术信息

请按照以下格式输出：
【标题栏】
...

【技术要求】
...

【尺寸标注】
...

【其他信息】
..."""
        
        # 调用千问VL分析
        result = analyze_with_qwen_vl(
            image_path=image_path,
            prompt=prompt,
            model_type=model_type
        )
        
        if result["success"]:
            # 解析结果
            analysis_text = result["result"]
            
            # 提取不同部分的内容
            title_text = ""
            tech_text = ""
            all_text = analysis_text
            
            # 简单的结果解析
            lines = analysis_text.split('\n')
            current_section = ""
            
            for line in lines:
                line = line.strip()
                if "【标题栏】" in line:
                    current_section = "title"
                elif "【技术要求】" in line:
                    current_section = "tech"
                elif "【尺寸标注】" in line or "【其他信息】" in line:
                    current_section = "other"
                elif line:
                    if current_section == "title":
                        title_text += line + "\n"
                    elif current_section == "tech":
                        tech_text += line + "\n"
            
            return {
                "title_block": title_text.strip(),
                "tech_block": tech_text.strip(),
                "all_text": all_text,
                "layout": "unknown",  # 千问VL会在结果中分析布局
                "model_used": result["model"]
            }
        else:
            logger.error(f"千问VL OCR失败: {result.get('error', '未知错误')}")
            return {
                "title_block": "",
                "tech_block": "",
                "all_text": "",
                "layout": "unknown",
                "error": result.get('error', '识别失败')
            }
            
    except Exception as e:
        logger.error(f"千问VL OCR异常: {e}")
        import traceback
        traceback.print_exc()
        return {
            "title_block": "",
            "tech_block": "",
            "all_text": "",
            "layout": "unknown",
            "error": str(e)
        }

# ==============================
# 启用千问VL OCR模式
# ==============================

USE_QWEN_VL_OCR = True  # 设置为True使用千问VL，False使用PaddleOCR

# ==============================
# 千问VL 工具函数
# ==============================

class QwenToolRequest(BaseModel):
    tool_call: str
    parameters: dict

@app.post("/qwen-tool")
def qwen_tool(request: QwenToolRequest):
    """
    千问VL工具调用端点
    支持的工具：
    1. 查询数据库 - 查询图纸信息
    2. OCR分析 - 分析图纸内容
    3. 搜索图纸 - 搜索图纸信息
    """
    try:
        tool_call = request.tool_call
        parameters = request.parameters
        
        logger.info(f"千问VL工具调用: {tool_call}")
        logger.info(f"参数: {parameters}")
        
        if tool_call == "query_database":
            # 查询数据库
            return query_database(parameters)
        elif tool_call == "analyze_drawing":
            # 分析图纸
            return analyze_drawing_tool(parameters)
        elif tool_call == "search_drawings":
            # 搜索图纸
            return search_drawings_tool(parameters)
        else:
            return {
                "success": False,
                "error": f"未知工具: {tool_call}"
            }
            
    except Exception as e:
        logger.error(f"千问VL工具调用失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }

def query_database(parameters: dict) -> dict:
    """
    查询数据库中的图纸信息
    
    参数示例:
    {
        "query_type": "list",  # list, detail, count
        "drawing_id": 1,       # 当query_type为detail时需要
        "limit": 10,           # 限制返回数量
        "offset": 0            # 偏移量
    }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_type = parameters.get("query_type", "list")
        
        if query_type == "list":
            limit = parameters.get("limit", 10)
            offset = parameters.get("offset", 0)
            
            cursor.execute(
                "SELECT id, filename, file_type, file_size, upload_time, layout "
                "FROM drawings ORDER BY upload_time DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            rows = cursor.fetchall()
            
            drawings = []
            for row in rows:
                drawings.append({
                    "id": row["id"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "file_size": row["file_size"],
                    "upload_time": row["upload_time"],
                    "layout": row["layout"]
                })
            
            return {
                "success": True,
                "data": {
                    "drawings": drawings,
                    "total": len(drawings)
                }
            }
            
        elif query_type == "detail":
            drawing_id = parameters.get("drawing_id")
            if not drawing_id:
                return {
                    "success": False,
                    "error": "缺少drawing_id参数"
                }
            
            cursor.execute(
                "SELECT * FROM drawings WHERE id=?",
                (drawing_id,)
            )
            row = cursor.fetchone()
            
            if row:
                return {
                    "success": True,
                    "data": {
                        "id": row["id"],
                        "filename": row["filename"],
                        "file_type": row["file_type"],
                        "file_size": row["file_size"],
                        "upload_time": row["upload_time"],
                        "title_text": row["title_text"],
                        "tech_text": row["tech_text"],
                        "all_text": row["all_text"],
                        "layout": row["layout"]
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"未找到ID为{drawing_id}的图纸"
                }
                
        elif query_type == "count":
            cursor.execute("SELECT COUNT(*) as count FROM drawings")
            row = cursor.fetchone()
            count = row["count"] if row else 0
            
            return {
                "success": True,
                "data": {
                    "count": count
                }
            }
            
        else:
            return {
                "success": False,
                "error": f"未知查询类型: {query_type}"
            }
            
    except Exception as e:
        logger.error(f"数据库查询失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if 'conn' in locals():
            conn.close()

def analyze_drawing_tool(parameters: dict) -> dict:
    """
    分析图纸内容
    
    参数示例:
    {
        "drawing_id": 1,       # 图纸ID
        "image_path": "path/to/image.png",  # 图片路径
        "model_type": "plus"   # plus, max, chat
    }
    """
    try:
        drawing_id = parameters.get("drawing_id")
        image_path = parameters.get("image_path")
        model_type = parameters.get("model_type", "plus")
        
        if drawing_id:
            # 通过ID获取图纸信息
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT filename FROM drawings WHERE id=?",
                (drawing_id,)
            )
            row = cursor.fetchone()
            conn.close()
            
            if row:
                filename = row["filename"]
                image_path = os.path.join(UPLOAD_DIR, filename)
                if not os.path.exists(image_path):
                    return {
                        "success": False,
                        "error": f"图纸文件不存在: {image_path}"
                    }
            else:
                return {
                    "success": False,
                    "error": f"未找到ID为{drawing_id}的图纸"
                }
        
        if not image_path:
            return {
                "success": False,
                "error": "缺少image_path参数"
            }
        
        if not os.path.exists(image_path):
            return {
                "success": False,
                "error": f"文件不存在: {image_path}"
            }
        
        # 使用千问VL分析图纸
        result = ocr_with_qwen_vl(image_path, model_type)
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"图纸分析失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }

def search_drawings_tool(parameters: dict) -> dict:
    """
    搜索图纸
    
    参数示例:
    {
        "keyword": "钢筋",      # 搜索关键词
        "limit": 10            # 限制返回数量
    }
    """
    try:
        keyword = parameters.get("keyword", "")
        limit = parameters.get("limit", 10)
        
        if not keyword:
            return {
                "success": False,
                "error": "缺少keyword参数"
            }
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 搜索标题栏、技术要求和全局OCR内容
        cursor.execute(
            "SELECT id, filename, file_type, upload_time, layout "
            "FROM drawings "
            "WHERE title_text LIKE ? OR tech_text LIKE ? OR all_text LIKE ? "
            "ORDER BY upload_time DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
        )
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "filename": row["filename"],
                "file_type": row["file_type"],
                "upload_time": row["upload_time"],
                "layout": row["layout"]
            })
        
        return {
            "success": True,
            "data": {
                "results": results,
                "total": len(results)
            }
        }
        
    except Exception as e:
        logger.error(f"图纸搜索失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if 'conn' in locals():
            conn.close()