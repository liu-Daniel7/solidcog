@echo off
chcp 65001 >nul
REM 启动图纸管理系统服务器

REM 切换到脚本所在目录，避免相对路径错误
cd /d "%~dp0"

REM 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 错误：未找到 Python，请先安装 Python 后再运行
    pause
    exit /b 1
)

REM 检查依赖是否安装
echo 🔍 正在检查项目依赖...
pip list | findstr /i "fastapi uvicorn opencv-python numpy pillow pdf2image paddlepaddle paddleocr" >nul 2>&1
if %errorlevel% neq 0 (
    echo 检测到缺失依赖，开始自动安装...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if %errorlevel% neq 0 (
        echo 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
    echo ✅ 依赖安装完成
)

REM 启动服务器
echo.
echo 正在启动图纸管理系统服务器...
echo 服务器将在 http://127.0.0.1:8000/主页 运行
echo 按 Ctrl+C 可以停止服务器
echo.
uvicorn main:app --reload --host 0.0.0.0 --port 8000
