#!/usr/bin/env python3
"""
测试 PaddleOCR 功能
"""
import os
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_paddleocr():
    """测试 PaddleOCR 功能"""
    try:
        # 设置缓存目录
        os.environ['PADDLEOCR_CACHE_DIR'] = os.path.join(os.getcwd(), '.paddleocr_cache')
        os.makedirs(os.environ['PADDLEOCR_CACHE_DIR'], exist_ok=True)
        logger.info(f"设置 PaddleOCR 缓存目录: {os.environ['PADDLEOCR_CACHE_DIR']}")
        
        # 导入 PaddleOCR
        from paddleocr import PaddleOCR
        logger.info("成功导入 PaddleOCR")
        
        # 初始化 PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        logger.info("成功初始化 PaddleOCR")
        
        # 测试识别
        # 这里可以添加测试图片路径
        # result = ocr.ocr("test_image.png")
        # logger.info(f"识别结果: {result}")
        
        logger.info("PaddleOCR 测试成功")
        return True
    except Exception as e:
        logger.error(f"PaddleOCR 测试失败: {e}")
        return False

if __name__ == "__main__":
    test_paddleocr()