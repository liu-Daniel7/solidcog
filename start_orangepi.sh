#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="solidcog-py39"
MINIFORGE_DIR="${HOME}/miniforge3"

cd "${PROJECT_DIR}"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

source "${MINIFORGE_DIR}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
