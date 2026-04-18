#!/usr/bin/env python3
"""
测试 PaddleOCR 导入和初始化
"""
import os
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_paddleocr_import():
    """测试 PaddleOCR 导入和初始化"""
    try:
        # 设置 PaddleOCR 缓存目录为当前目录，避免权限问题
        os.environ['PADDLEOCR_CACHE_DIR'] = os.path.join(os.getcwd(), '.paddleocr_cache')
        os.makedirs(os.environ['PADDLEOCR_CACHE_DIR'], exist_ok=True)
        logger.info(f"设置 PaddleOCR 缓存目录: {os.environ['PADDLEOCR_CACHE_DIR']}")
        
        # 导入 PaddleOCR
        from paddleocr import PaddleOCR
        logger.info("成功导入 PaddleOCR")
        
        # 初始化 PaddleOCR
        ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
        logger.info("成功初始化 PaddleOCR")
        
        logger.info("PaddleOCR 测试成功")
        return True
    except Exception as e:
        logger.error(f"PaddleOCR 测试失败: {e}")
        return False

if __name__ == "__main__":
    test_paddleocr_import()