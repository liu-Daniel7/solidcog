#!/usr/bin/env python3
"""
测试 PaddleOCR 返回格式
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

# 处理结果
if result:
    print("\n处理 OCR 结果:")
    for page_idx, page_result in enumerate(result):
        print(f"第 {page_idx+1} 个页面结果:")
        print(f"类型: {type(page_result)}")
        if isinstance(page_result, list):
            print(f"是列表，长度: {len(page_result)}")
            for box_idx, text_box in enumerate(page_result):
                print(f"第 {box_idx+1} 个文本框:")
                print(f"类型: {type(text_box)}")
                print(f"内容: {text_box}")
                if isinstance(text_box, list) and len(text_box) == 2:
                    print(f"第一个元素 (坐标): {text_box[0]}")
                    print(f"第二个元素 (文本和置信度): {text_box[1]}")
                    if isinstance(text_box[1], tuple) and len(text_box[1]) > 0:
                        print(f"识别到的文本: {text_box[1][0]}")
                        print(f"置信度: {text_box[1][1]}")
