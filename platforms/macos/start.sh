#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h:h}"
cd "$ROOT"
[[ -x .venv/bin/python ]] || { echo "请先运行 platforms/macos/setup.sh"; exit 1; }
mkdir -p "$HOME/Library/Logs/SolidCog"
.venv/bin/python -m uvicorn server:app --app-dir platforms/macos --host 127.0.0.1 --port 8090 &
SCHEDULER_PID=$!
trap 'kill "$SCHEDULER_PID" 2>/dev/null || true' EXIT INT TERM
sleep 2
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
