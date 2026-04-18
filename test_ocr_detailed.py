#!/usr/bin/env python3
"""
详细测试 OCR 功能
"""
import os
import sys
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 添加当前目录到路径
sys.path.append(os.getcwd())

# 导入 OCR 函数
from main import run_ocr

def test_ocr():
    """测试 OCR 功能"""
    try:
        # 获取 uploads 目录中的第一个 PDF 文件
        uploads_dir = os.path.join(os.getcwd(), 'uploads')
        pdf_files = [f for f in os.listdir(uploads_dir) if f.endswith('.pdf')]
        
        if not pdf_files:
            logger.error("未找到 PDF 文件")
            return False
        
        pdf_path = os.path.join(uploads_dir, pdf_files[0])
        logger.info(f"测试 PDF 文件: {pdf_path}")
        
        # 调用 OCR 函数
        result = run_ocr(pdf_path)
        logger.info(f"OCR 识别结果: {result}")
        
        if result and result != "OCR识别结果为空" and not result.startswith("OCR识别失败"):
            logger.info("OCR 测试成功")
            return True
        else:
            logger.error("OCR 识别结果为空或失败")
            return False
            
    except Exception as e:
        logger.error(f"OCR 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_ocr()
