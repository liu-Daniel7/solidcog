@echo off
cd /d "%~dp0"

for /f "delims=" %%i in ('wsl.exe -d Ubuntu -- wslpath -a "%CD%"') do set "WSL_REPO=%%i"
wsl.exe -d Ubuntu -- bash -lc "cd '%WSL_REPO%/model_scheduler' && ./start.sh"
