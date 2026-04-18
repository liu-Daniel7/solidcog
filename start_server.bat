@echo off

REM 启动图纸管理系统服务器

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：未找到 Python，请先安装 Python
    pause
    exit /b 1
)

REM 检查依赖是否安装
echo 检查依赖...
pip list | findstr "fastapi uvicorn pdf2image pytesseract Pillow" >nul 2>&1
if %errorlevel% neq 0 (
    echo 安装依赖...
    pip install fastapi uvicorn pdf2image pytesseract Pillow
    if %errorlevel% neq 0 (
        echo 依赖安装失败
        pause
        exit /b 1
    )
)

REM 启动服务器
echo 启动服务器...
echo 服务器将在 http://127.0.0.1:8000/主页 运行
echo 按 Ctrl+C 停止服务器

uvicorn main:app --reload