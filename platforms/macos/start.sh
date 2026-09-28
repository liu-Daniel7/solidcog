#!/bin/zsh
# Keep the scheduler alive even when the web service is already running.
set -euo pipefail
ROOT="${0:A:h:h:h}"
cd "$ROOT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PYTHON="${SOLIDCOG_MAC_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || { echo "请先运行 platforms/macos/setup.sh"; exit 1; }
LOG_DIR="$HOME/Library/Logs/SolidCog"
mkdir -p "$LOG_DIR"
SCHEDULER_PID=""
WEB_PID=""
cleanup() {
  for pid in "$WEB_PID" "$SCHEDULER_PID"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
scheduler_ready() {
  "$PYTHON" -c 'import json, urllib.request; r=json.load(urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=2)); assert r.get("platform")=="macos" and r.get("status")=="ready"' >/dev/null 2>&1
}
port_open() {
  "$PYTHON" -c 'import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(s.connect_ex(("127.0.0.1",int(sys.argv[1]))))' "$1"
}
if ! scheduler_ready; then
  if port_open 8090; then
    echo "8090 已被非正常 macOS 调度器占用；请检查该进程，不会自动终止它。"
    exit 1
  fi
  "$PYTHON" -m uvicorn server:app --app-dir "$ROOT/platforms/macos" --host 127.0.0.1 --port 8090 >>"$LOG_DIR/scheduler.log" 2>&1 &
  SCHEDULER_PID=$!
  for attempt in {1..30}; do
    scheduler_ready && break
    kill -0 "$SCHEDULER_PID" 2>/dev/null || break
    sleep 1
  done
  if ! scheduler_ready; then
    echo "调度器启动失败，日志：$LOG_DIR/scheduler.log"
    tail -40 "$LOG_DIR/scheduler.log"
    exit 1
  fi
fi
if ! port_open 8000; then
  "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port 8000 &
  WEB_PID=$!
fi
WEB_READY=0
for attempt in {1..30}; do
  if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8000/home >/dev/null; then
    WEB_READY=1
    break
  fi
  if [[ -n "$WEB_PID" ]] && ! kill -0 "$WEB_PID" 2>/dev/null; then break; fi
  sleep 1
done
[[ "$WEB_READY" == 1 ]] || { echo "网页服务启动失败，请检查终端输出。"; exit 1; }
echo "SolidCog：http://127.0.0.1:8000/home"
if [[ -n "$SCHEDULER_PID$WEB_PID" ]]; then
  echo "macOS 调度器已连接。保持此终端打开；按 Control+C 停止本次启动的服务。"
else
  echo "网页和 macOS 调度器已在运行，复用现有服务。"
fi
if [[ "${SOLIDCOG_OPEN_BROWSER:-1}" == 1 ]]; then open http://127.0.0.1:8000/home || true; fi
# Monitor only children owned by this launcher; never kill an existing service.
while [[ -n "$SCHEDULER_PID$WEB_PID" ]]; do
  for pid in "$SCHEDULER_PID" "$WEB_PID"; do
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      echo "服务进程退出，请检查终端和 $LOG_DIR/scheduler.log"
      exit 1
    fi
  done
  sleep 2
done
