#!/usr/bin/env python3
"""
测试 PaddleOCR API 返回格式
"""
import os
import sys
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 导入 PaddleOCR
from paddleocr import PaddleOCR

# 初始化 OCR 引擎
ocr = PaddleOCR(
    use_angle_cls=False,
    lang="ch"
)

# 测试图片路径
img_path = "temp_page_1.png"

if not os.path.exists(img_path):
    logger.error(f"图片文件不存在: {img_path}")
    sys.exit(1)

# 调用 OCR API
logger.info(f"开始识别图片: {img_path}")
result = ocr.ocr(img_path, cls=False)

# 打印结果
logger.info(f"OCR 结果类型: {type(result)}")
logger.info(f"OCR 结果长度: {len(result) if result else 0}")
logger.info(f"OCR 结果: {result}")

# 处理结果
if result:
    logger.info("\n处理 OCR 结果:")
    for i, item in enumerate(result):
        logger.info(f"第 {i+1} 个结果: {item}")
        logger.info(f"类型: {type(item)}")
        if isinstance(item, list):
            logger.info(f"是列表，长度: {len(item)}")
            for j, sub_item in enumerate(item):
                logger.info(f"子项 {j+1}: {sub_item}")
                logger.info(f"子项类型: {type(sub_item)}")
        elif isinstance(item, tuple):
            logger.info(f"是元组，长度: {len(item)}")
            for j, sub_item in enumerate(item):
                logger.info(f"子项 {j+1}: {sub_item}")
                logger.info(f"子项类型: {type(sub_item)}")
