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
        import cv2
        import numpy
        import pdf2image
        from PIL import Image
        import paddle
        import paddleocr
        print("依赖检查通过")
        return True
    except ImportError as e:
        print(f"缺少依赖: {e}")
        return False

def install_dependencies():
    """安装依赖，从requirements.txt读取确保版本一致"""
    print("🔍 开始安装项目依赖...")
    # 使用清华源加速国内安装
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"依赖安装失败: {result.stderr}")
        return False
    print("依赖安装完成")
    return True

def start_server():
    """启动服务器"""
    print("\n正在启动图纸管理系统服务器...")
    print("本地访问地址：http://127.0.0.1:8000/主页")
    print("同局域网访问：使用本机IP+8000端口即可，例如http://192.168.1.100:8000/主页")
    print("按 Ctrl+C 可以停止服务器\n")
    
    # 支持局域网访问，固定端口8000
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
    )

if __name__ == "__main__":
    # 切换工作目录到脚本所在目录，避免相对路径错误
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    os.chdir(script_dir)
    
    # 检查依赖
    if not check_dependencies():
        # 安装依赖
        if not install_dependencies():
            print("依赖安装失败，无法启动服务器")
            sys.exit(1)
    
    # 启动服务器
    start_server()
