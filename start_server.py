#!/usr/bin/env python3
"""
启动图纸管理系统服务器
"""
import os
import sys
import subprocess
import time

def check_dependencies():
    """检查依赖是否安装"""
    try:
        import fastapi
        import uvicorn
        import pdf2image
        import pytesseract
        from PIL import Image
        print("依赖检查通过")
        return True
    except ImportError as e:
        print(f"缺少依赖: {e}")
        return False

def install_dependencies():
    """安装依赖"""
    print("安装依赖...")
    dependencies = [
        "fastapi",
        "uvicorn",
        "pdf2image",
        "pytesseract",
        "Pillow"
    ]
    
    for dep in dependencies:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", dep],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"安装 {dep} 失败: {result.stderr}")
            return False
    print("依赖安装完成")
    return True

def start_server():
    """启动服务器"""
    print("启动服务器...")
    print("服务器将在 http://127.0.0.1:8000/主页 运行")
    print("按 Ctrl+C 停止服务器")
    
    # 启动 uvicorn 服务器
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload"]
    )

if __name__ == "__main__":
    # 检查依赖
    if not check_dependencies():
        # 安装依赖
        if not install_dependencies():
            print("依赖安装失败，无法启动服务器")
            sys.exit(1)
    
    # 启动服务器
    start_server()