@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Follow the installation steps in README.md first.
    pause
    exit /b 1
)

if not exist ".env" (
    echo Missing .env. Copy .env.example to .env and add your QWEN_API_KEY.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import fastapi, pypdfium2, uvicorn, requests, jinja2, openai; from PIL import Image" >nul 2>&1
if %errorlevel% neq 0 (
    echo Dependencies are incomplete. Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

curl.exe --noproxy "*" --silent --fail http://127.0.0.1:8090/health >nul 2>&1
if %errorlevel% neq 0 (
    for /f "delims=" %%i in ('wsl.exe -d Ubuntu -- wslpath -a "%CD%"') do set "WSL_REPO=%%i"
    echo Starting local model scheduler...
    wsl.exe -d Ubuntu -- bash -lc "mkdir -p ~/.local/share/solidcog/scheduler; cd '!WSL_REPO!/model_scheduler'; nohup ./start.sh > ~/.local/share/solidcog/scheduler/server.log 2>&1 </dev/null &"
    timeout /t 3 /nobreak >nul
)

echo SolidCog: http://127.0.0.1:8000/home
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
