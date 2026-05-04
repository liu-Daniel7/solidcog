@echo off

echo ==============================
echo 工程图纸智能管理系统 - 依赖安装脚本
echo ==============================
echo.

REM 检查Python是否安装
echo 检查Python环境...
python --version
if %errorlevel% neq 0 (
    echo 错误: 未安装Python 3.9+
    echo 请先安装Python 3.9或更高版本
    pause
    exit /b 1
)

echo.
echo 升级pip...
pip install --upgrade pip
if %errorlevel% neq 0 (
    echo 错误: 升级pip失败
    pause
    exit /b 1
)

echo.
echo 安装Python依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo 错误: 安装Python依赖失败
    pause
    exit /b 1
)

echo.
echo 安装PaddlePaddle（使用百度镜像）...
pip install paddlepaddle==2.6.2 -i https://mirror.baidu.com/pypi/simple
if %errorlevel% neq 0 (
    echo 错误: 安装PaddlePaddle失败
    pause
    exit /b 1
)

echo.
echo 安装PaddleOCR（使用清华镜像）...
pip install paddleocr==2.6.1.3 -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo 错误: 安装PaddleOCR失败
    pause
    exit /b 1
)

echo.
echo ==============================
echo 依赖安装完成！
echo ==============================
echo.
echo 下一步：
echo 1. 下载并安装 Poppler（PDF处理工具）
echo    下载地址：https://github.com/oschwartz10612/poppler-windows/releases/
echo    将poppler的bin目录添加到系统环境变量PATH
echo.
echo 2. 配置API密钥：
echo    打开 main.py 文件，修改以下API密钥：
echo    - QWEN_API_KEY = "你的千问API密钥"
echo    - DEEPSEEK_API_KEY = "你的DeepSeek API密钥"
echo.
echo 3. 启动系统：
echo    运行命令：uvicorn main:app --reload --host 0.0.0.0 --port 8000
echo    然后访问：http://localhost:8000/主页
echo.
pause