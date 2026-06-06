import os
import logging
import traceback
import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _clean_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value

def load_env_file(path):
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8-sig") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue

                os.environ[key] = _clean_env_value(value)
    except Exception as exc:
        logger.warning("Failed to load .env file: %s", exc)

load_env_file(os.path.join(BASE_DIR, ".env"))

CONFIG_PLACEHOLDERS = {
    "replace-with-your-qwen-api-key",
    "replace-with-your-deepseek-api-key",
    "your-qwen-api-key",
    "your-deepseek-api-key",
}

def require_config(name, value):
    if not value or value.strip().lower() in CONFIG_PLACEHOLDERS:
        raise HTTPException(status_code=500, detail=f"Missing required configuration: {name}")
    return value

def get_configured_ocr_backend():
    """
    Resolve OCR backend while preserving the old USE_QWEN_VL_OCR switch.
    Explicit OCR_BACKEND always wins.
    """
    raw_backend = os.getenv("OCR_BACKEND", "").strip().lower()
    if raw_backend:
        return OCR_BACKEND_ALIASES.get(raw_backend, raw_backend)

    return "qwen_vl" if USE_QWEN_VL_OCR else "paddle"

def unavailable_ocr_backend_result(backend, detail):
    return {
        "title_block": detail,
        "tech_block": "",
        "all_text": "",
        "layout": "unknown",
        "backend": backend,
        "error": detail
    }

# 配置
UPLOAD_DIR = "uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")
USE_QWEN_VL_OCR = os.getenv("USE_QWEN_VL_OCR", "true").lower() in ("1", "true", "yes", "on")
SUPPORTED_OCR_BACKENDS = {"qwen_vl", "paddle", "ascend_cann", "mindx"}
OCR_BACKEND_ALIASES = {
    "qwen": "qwen_vl",
    "qwen-vl": "qwen_vl",
    "qwen_vl": "qwen_vl",
    "paddle": "paddle",
    "paddleocr": "paddle",
    "paddle_ocr": "paddle",
    "ascend": "ascend_cann",
    "ascend-cann": "ascend_cann",
    "ascend_cann": "ascend_cann",
    "cann": "ascend_cann",
    "mindx": "mindx",
}

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

def run_ascend_cann_ocr(file_path, images):
    """
    Placeholder for Atlas/CANN OCR inference.
    Real NPU inference should load OM models from ASCEND_MODEL_DIR and return
    the same OCR result schema used by Qwen-VL and PaddleOCR.
    """
    device_id = os.getenv("ASCEND_DEVICE_ID", "0")
    model_dir = os.getenv("ASCEND_MODEL_DIR", "models/ascend")
    detail = (
        "OCR_BACKEND=ascend_cann 已启用，但当前仓库尚未接入 Atlas/CANN OM 模型推理。"
        f"请先在 {model_dir} 放置文字检测/识别 .om 模型，并实现 CANN 推理后端。"
    )
    logger.warning(
        "Ascend CANN OCR backend is not implemented yet. "
        f"file={file_path}, pages={len(images)}, device_id={device_id}, model_dir={model_dir}"
    )
    return unavailable_ocr_backend_result("ascend_cann", detail)

def run_mindx_ocr(file_path, images):
    """
    Placeholder for MindX Pipeline OCR inference.
    A future implementation should POST the prepared image to MINDX_PIPELINE_URL.
    """
    pipeline_url = os.getenv("MINDX_PIPELINE_URL", "")
    detail = (
        "OCR_BACKEND=mindx 已启用，但当前仓库尚未接入 MindX Pipeline 推理服务。"
        "请先配置 MINDX_PIPELINE_URL 并实现 MindX 调用逻辑。"
    )
    logger.warning(
        "MindX OCR backend is not implemented yet. "
        f"file={file_path}, pages={len(images)}, pipeline_url={pipeline_url or 'unset'}"
    )
    return unavailable_ocr_backend_result("mindx", detail)

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

        backend = get_configured_ocr_backend()
        logger.info(f"使用 OCR 后端: {backend}")

        if backend not in SUPPORTED_OCR_BACKENDS:
            detail = (
                f"不支持的 OCR_BACKEND: {backend}。"
                f"支持的后端: {', '.join(sorted(SUPPORTED_OCR_BACKENDS))}"
            )
            logger.error(detail)
            return unavailable_ocr_backend_result(backend, detail)

        if backend == "ascend_cann":
            return run_ascend_cann_ocr(file_path, images)

        if backend == "mindx":
            return run_mindx_ocr(file_path, images)

        if backend == "qwen_vl":
            logger.info("使用千问VL进行OCR识别")
            
            # 保存临时图片用于千问VL
            temp_image_path = "temp_ocr_image.png"
            if len(images) > 0:
                images[0].save(temp_image_path, quality=95)
                
                # 使用千问VL进行OCR
                result = ocr_with_qwen_vl(temp_image_path)
                result["backend"] = backend
                
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
                    "layout": "unknown",
                    "backend": backend
                }

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
            "layout": layout,
            "backend": backend
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
def upload_drawing(request: Request, files: list[UploadFile] = File(...)):
    """上传 PDF 图纸或 PNG 图片（支持批量上传）"""
    try:
        # 检查文件数量
        if len(files) == 0:
            raise HTTPException(status_code=400, detail="请选择至少一个文件")

        # 处理每个文件
        uploaded_files = []

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
                drawing_id = cursor.lastrowid
                conn.commit()
                uploaded_files.append({
                    "id": drawing_id,
                    "filename": new_filename,
                    "original_filename": file.filename,
                    "file_size": file_size
                })
                logger.info(f"图纸上传成功: {new_filename}")
            finally:
                if 'conn' in locals():
                    conn.close()

        # 重定向到主页
        accept_header = request.headers.get("accept", "")
        if "application/json" in accept_header:
            return JSONResponse({
                "success": True,
                "files": uploaded_files
            })

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
                "图纸列表": 图纸列表,
                "drawings": 图纸列表
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
            "drawings": 图纸列表,
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
def upload_drawing_cn(request: Request, files: list[UploadFile] = File(...)):
    return upload_drawing(request, files)

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


@app.get("/view-ocr/{drawing_id}", response_class=HTMLResponse)
def view_ocr_cn(request: Request, drawing_id: int):
    return view_ocr(request, drawing_id)

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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

class ChatWithDrawingRequest(BaseModel):
    prompt: str
    drawing_id: int
    model: str = "qwen-vl-plus"


DRAWING_ASSISTANT_REFERENCE = """1、图纸知识库智能管理：面向非标产线及车间集成商，AI可对历史图纸进行深度语义解析与特征提取，实现图纸名称、技术要求、图形结构等多维度信息的智能匹配。通过构建可学习的知识图谱，系统能够精准推荐相似设计案例与可复用模块，大幅减少重复绘图工作，同时为工程师提供最优参考依据，使知识沉淀真正转化为设计效率的提升。

2、图纸智能审图：AI审图系统可自动依据《机械设计手册》、工程图制图国标及行标，对图纸进行合规性检查；同时支持嵌入企业自定义的审核规则（如特定材料库、工艺规范等）。审核覆盖尺寸标注的完整性、材料选型的合理性、表面处理与热处理的工艺适配性，以及尺寸链的闭环校核，精准识别潜在设计缺陷并给出修改建议，显著降低人工错漏率，保障非标设计的质量一致性。"""


DRAWING_ASSISTANT_SYSTEM_PROMPT = """你是 SolidCog 的受限工程图纸智能助手，服务对象是非标产线及车间集成商的工程师。

必须严格遵守以下规则：
1. 只回答用户当前提出的问题，不主动扩展到用户没有问的主题。
2. 回答依据只能来自三类内容：用户问题、当前图纸内容、系统提供的参考内容。
3. 如果图纸内容或参考内容不足以支持结论，必须明确说明“当前依据不足”，不能猜测、编造图号、材料、尺寸、工艺、标准条款或审查结论。
4. 当用户要求图纸知识库智能管理时，重点围绕图纸名称、技术要求、图形结构、相似案例、可复用模块和知识沉淀回答。
5. 当用户要求智能审图时，重点围绕合规性、尺寸标注完整性、材料选型、表面处理、热处理、工艺适配性、尺寸链闭环和修改建议回答。
6. 不能声称已经检查了未提供的企业私有规则、完整标准原文或外部知识库；只能说“可按该规则方向检查”或“需要补充规则后检查”。
7. 默认简要回答，除非用户明确要求“详细分析、完整报告、逐项展开”，否则不要写长篇报告。
8. 默认输出控制在 120-180 字；最多 5 条要点；每条只保留“结论/问题 + 依据 + 建议”，不要展开背景说明。
9. 智能审图默认只列最重要的 3-5 个风险或改进项，并给出简短处理建议；没有问题时直接说“当前未发现明显问题”，再列必要补充项。
10. 不要使用过多 Markdown 标题。默认格式为：一句总评 + 3-5 条编号要点 + 一句需要补充的信息。
11. 禁止使用 Markdown 装饰符，包括星号、井号、表格、分隔线、引用块和代码块；不要输出 **加粗**、### 标题或 --- 分隔线。
12. 使用纯文本回答。允许使用“1.”、“2.”这样的普通编号，但编号后直接写内容，不要加粗小标题。
13. 输出要专业、可执行、结论清晰。每条关键结论尽量标明来自图纸内容、参考内容，或说明依据不足。
"""


def build_drawing_context_messages(
    *,
    filename: str,
    ocr_title: str,
    ocr_tech: str,
    ocr_all: str,
    qwen_title: str,
    qwen_tech: str,
    qwen_all: str,
    user_prompt: str
):
    user_content = f"""【用户问题】
{user_prompt}

【当前图纸文件名】
{filename}

【当前图纸已入库 OCR 内容】
标题栏：
{ocr_title}

技术要求：
{ocr_tech}

全局 OCR：
{ocr_all}

【当前图纸 Qwen VL 视觉解析内容】
标题栏分析：
{qwen_title}

技术要求分析：
{qwen_tech}

完整视觉分析：
{qwen_all}

【系统参考内容】
{DRAWING_ASSISTANT_REFERENCE}

请严格基于“用户问题 + 当前图纸内容 + 系统参考内容”回答。默认简要回答，最多 5 条要点；只用纯文本普通编号，禁止星号、加粗、标题和分隔线；若依据不足，直接指出不足并说明还需要补充哪些图纸信息或审核规则。"""

    return [
        {"role": "system", "content": DRAWING_ASSISTANT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]


def sanitize_assistant_answer(answer: str) -> str:
    """Remove markdown decoration from model output before it reaches the UI."""
    if not answer:
        return ""

    text = str(answer)

    # Remove fenced code markers and markdown separators.
    text = re.sub(r"```+", "", text)
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", text)

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        # Strip markdown heading, quote, and list decoration.
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        line = re.sub(r"^\s*[-+*]\s+", "", line)

        # Convert "**标题**：内容" and similar variants to plain text.
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("*", "")
        line = line.replace("#", "")
        line = line.replace("`", "")
        line = line.replace("|", " ")

        cleaned_lines.append(line.strip())

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
        
        messages = build_drawing_context_messages(
            filename=filename,
            ocr_title=ocr_title,
            ocr_tech=ocr_tech,
            ocr_all=ocr_all,
            qwen_title=qwen_title,
            qwen_tech=qwen_tech,
            qwen_all=qwen_all,
            user_prompt=request.prompt
        )

        from openai import OpenAI
        client = OpenAI(
            api_key=require_config("QWEN_API_KEY", QWEN_API_KEY),
            base_url=QWEN_BASE_URL
        )
        
        completion = client.chat.completions.create(
            model=request.model,
            messages=messages,
            stream=False
        )
        
        answer = sanitize_assistant_answer(completion.choices[0].message.content)
        
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
                api_key=require_config("QWEN_API_KEY", QWEN_API_KEY),
                base_url=QWEN_BASE_URL
            )
            
            completion = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                stream=False
            )
            return sanitize_assistant_answer(completion.choices[0].message.content)
        else:
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Authorization": f"Bearer {require_config('DEEPSEEK_API_KEY', DEEPSEEK_API_KEY)}",
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
            return sanitize_assistant_answer(result["choices"][0]["message"]["content"])
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
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

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
            api_key=require_config("QWEN_API_KEY", QWEN_API_KEY),
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
            return {"success": True, "answer": sanitize_assistant_answer(result["result"])}
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
