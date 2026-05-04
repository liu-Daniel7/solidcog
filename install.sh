#!/bin/bash

set -e

echo "=========================================="
echo "工程图纸智能管理系统 - 一键安装脚本"
echo "=========================================="
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/7] 检查Python环境..."
python3 --version || { echo "错误: 未安装Python 3"; exit 1; }

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info[1])')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
if [[ "$PYTHON_MAJOR" -ne 3 ]] || [[ "$PYTHON_VERSION" -lt 9 ]]; then
    echo "错误: 需要Python 3.9或更高版本（当前版本不支持）"
    exit 1
fi
echo "Python版本检查通过: $(python3 --version)"

echo
echo "[2/7] 升级pip..."
pip3 install --upgrade pip -q

echo
echo "[3/7] 安装系统依赖..."

if [[ "$(uname)" == "Darwin" ]]; then
    echo "检测到macOS，安装Poppler..."
    if command -v brew &> /dev/null; then
        brew install poppler
    else
        echo "警告: 未安装Homebrew，请手动安装Poppler: brew install poppler"
    fi

elif [[ "$(uname)" == "Linux" ]]; then
    echo "检测到Linux，安装系统依赖..."

    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq
        
        # Ubuntu 24.04+ 直接使用 libgl1（旧版用 libgl1-mesa-glx）
        # 统一使用更广泛的依赖列表
        DEPS="poppler-utils libgl1 libglib2.0-0 libsm6 libxrender1 libxext6"
        
        echo "安装依赖: $DEPS"
        sudo apt-get install -y -qq $DEPS || {
            echo "部分依赖安装失败，尝试备用方案..."
            sudo apt-get install -y -qq poppler-utils libgl1 libsm6 libxrender1 libxext6
        }
        
    elif command -v yum &> /dev/null; then
        sudo yum install -y poppler-utils mesa-libGL glib2 libSM libXrender libXext libgomp
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y poppler-utils mesa-libGL glib2 libSM libXrender libXext libgomp
    elif command -v apk &> /dev/null; then
        sudo apk add poppler-utils glib glibc libsm libxrender libxext
    else
        echo "警告: 无法自动安装系统依赖，请手动安装以下包:"
        echo "  poppler-utils, libgl1 (或 libgl1-mesa-glx), libglib2.0-0, libsm6, libxrender1, libxext6"
    fi
fi

echo
echo "[4/7] 安装Python基础依赖..."
pip3 install \
    fastapi==0.128.8 \
    uvicorn==0.39.0 \
    opencv-python==4.6.0.66 \
    numpy==1.24.3 \
    pillow==11.3.0 \
    python-multipart==0.0.20 \
    requests==2.32.5 \
    jinja2==3.1.2 \
    pdf2image==1.17.0 \
    pydantic==2.10.6 \
    -q

echo
echo "[5/7] 安装PaddlePaddle..."
pip3 install paddlepaddle==2.6.2 -q || pip3 install paddlepaddle -q

echo
echo "[6/7] 安装PaddleOCR..."
pip3 install paddleocr==2.6.1.3 -q

echo
echo "[7/7] 验证安装..."
python3 -c "
import fastapi
import uvicorn
import cv2
import numpy
import PIL
import pdf2image
import paddleocr
print('所有依赖验证通过')
"

echo
echo "=========================================="
echo "安装完成！"
echo "=========================================="
echo
echo "下一步操作："
echo "1. 配置API密钥："
echo "   打开 main.py，修改以下行："
echo "   - QWEN_API_KEY = \"你的千问API密钥\""
echo "   - DEEPSEEK_API_KEY = \"你的DeepSeek API密钥\""
echo
echo "2. 启动系统："
echo "   uvicorn main:app --reload --host 0.0.0.0 --port 8000"
echo
echo "3. 访问系统："
echo "   http://localhost:8000/主页"
echo