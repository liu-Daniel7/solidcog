#!/usr/bin/env python3
"""
简单测试 PaddleOCR
"""
import os
import sys

# 导入 PaddleOCR
from paddleocr import PaddleOCR

# 初始化 OCR 引擎
print("初始化 OCR 引擎...")
ocr = PaddleOCR(
    use_angle_cls=False,
    lang="ch"
)

# 测试图片路径
img_path = "temp_page_1.png"

if not os.path.exists(img_path):
    print(f"图片文件不存在: {img_path}")
    sys.exit(1)

# 调用 OCR API
print(f"开始识别图片: {img_path}")
result = ocr.ocr(img_path, cls=False)

# 打印结果
print(f"OCR 结果类型: {type(result)}")
print(f"OCR 结果长度: {len(result) if result else 0}")
print(f"OCR 结果: {result}")
