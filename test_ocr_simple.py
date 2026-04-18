#!/usr/bin/env python3
"""
简单测试 OCR 功能
"""
import os
import sys

# 导入 OCR 函数
from main import run_ocr

# 获取 uploads 目录中的第一个 PDF 文件
uploads_dir = os.path.join(os.getcwd(), 'uploads')
pdf_files = [f for f in os.listdir(uploads_dir) if f.endswith('.pdf')]

if not pdf_files:
    print("未找到 PDF 文件")
    sys.exit(1)

pdf_path = os.path.join(uploads_dir, pdf_files[0])
print(f"测试 PDF 文件: {pdf_path}")

# 调用 OCR 函数
print("开始调用 OCR 函数...")
result = run_ocr(pdf_path)
print(f"OCR 识别结果类型: {type(result)}")
print(f"OCR 识别结果长度: {len(result) if isinstance(result, (str, list)) else 0}")
print(f"OCR 识别结果: {result}")

if result and result != "OCR识别结果为空" and not result.startswith("OCR识别失败"):
    print("OCR 测试成功")
else:
    print("OCR 识别结果为空或失败")
