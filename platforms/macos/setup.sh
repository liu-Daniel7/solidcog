#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h:h}"
if [[ -n "${SOLIDCOG_PYTHON:-}" ]]; then
  PYTHON="$SOLIDCOG_PYTHON"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
elif [[ -x "$HOME/.local/bin/python3.12" ]]; then
  PYTHON="$HOME/.local/bin/python3.12"
else
  echo "需要 Python 3.12。请先安装，或设置 SOLIDCOG_PYTHON。"
  exit 1
fi
cd "$ROOT"
if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
[[ -x .venv/bin/python ]] || { echo "现有 .venv 不可用，请先备份并修复；安装脚本不会删除它。"; exit 1; }
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r platforms/macos/requirements.txt
printf '\nmacOS 环境安装完成。运行：./start_macos.command\n'
