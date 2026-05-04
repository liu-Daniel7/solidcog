#!/bin/bash

echo "=============================="
echo "工程图纸智能管理系统 - 依赖安装脚本"
echo "=============================="
echo

# 检查Python是否安装
echo "检查Python环境..."
python3 --version
if [ $? -ne 0 ]; then
    echo "错误: 未安装Python 3.9+"
    echo "请先安装Python 3.9或更高版本"
    exit 1
fi

echo
echo "升级pip..."
pip3 install --upgrade pip
if [ $? -ne 0 ]; then
    echo "错误: 升级pip失败"
    exit 1
fi

echo
echo "安装Python依赖..."
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if [ $? -ne 0 ]; then
    echo "错误: 安装Python依赖失败"
    exit 1
fi

echo
echo "安装PaddlePaddle（使用百度镜像）..."
pip3 install paddlepaddle==2.6.2 -i https://mirror.baidu.com/pypi/simple
if [ $? -ne 0 ]; then
    echo "错误: 安装PaddlePaddle失败"
    exit 1
fi

echo
echo "安装PaddleOCR（使用清华镜像）..."
pip3 install paddleocr==2.6.1.3 -i https://pypi.tuna.tsinghua.edu.cn/simple
if [ $? -ne 0 ]; then
    echo "错误: 安装PaddleOCR失败"
    exit 1
fi

echo
echo "安装系统依赖（Poppler）..."

# 检测操作系统
if [[ "$(uname)" == "Darwin" ]]; then
    # macOS
    echo "安装Poppler（macOS）..."
    brew install poppler
    if [ $? -ne 0 ]; then
        echo "警告: 安装Poppler失败，请手动安装"
    fi
elif [[ "$(uname)" == "Linux" ]]; then
    # Linux
    echo "安装Poppler（Linux）..."
    if command -v apt &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y poppler-utils
    elif command -v yum &> /dev/null; then
        sudo yum install -y poppler-utils
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y poppler-utils
    else
        echo "警告: 无法自动安装Poppler，请手动安装"
    fi
fi

echo
echo "=============================="
echo "依赖安装完成！"
echo "=============================="
echo
echo "下一步："
echo "1. 配置API密钥："
echo "   打开 main.py 文件，修改以下API密钥："
echo "   - QWEN_API_KEY = \"你的千问API密钥\""
echo "   - DEEPSEEK_API_KEY = \"你的DeepSeek API密钥\""
echo
echo "2. 启动系统："
echo "   运行命令：uvicorn main:app --reload --host 0.0.0.0 --port 8000"
echo "   然后访问：http://localhost:8000/主页"
echo