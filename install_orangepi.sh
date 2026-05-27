#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="solidcog-py39"
MINIFORGE_DIR="${HOME}/miniforge3"
INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh"
INSTALLER_PATH="/tmp/Miniforge3-Linux-aarch64.sh"

cd "${PROJECT_DIR}"

if [ "$(uname -m)" != "aarch64" ]; then
    echo "This installer targets Linux aarch64. Current arch: $(uname -m)"
    exit 1
fi

if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "${VERSION_CODENAME:-}" != "jammy" ]; then
        echo "Warning: this script is tuned for Ubuntu 22.04 jammy; detected ${PRETTY_NAME:-unknown}."
    fi
fi

echo "[1/5] Installing Ubuntu system packages..."
sudo apt-get update
sudo apt-get install -y \
    ca-certificates \
    curl \
    bzip2 \
    git \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgomp1

echo "[2/5] Installing Miniforge for aarch64 if needed..."
if [ ! -x "${MINIFORGE_DIR}/bin/conda" ]; then
    curl -L "${INSTALLER_URL}" -o "${INSTALLER_PATH}"
    bash "${INSTALLER_PATH}" -b -p "${MINIFORGE_DIR}"
fi

source "${MINIFORGE_DIR}/etc/profile.d/conda.sh"

echo "[3/5] Creating or updating conda environment ${ENV_NAME}..."
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    conda env update -n "${ENV_NAME}" -f environment-orangepi.yml --prune
else
    conda env create -f environment-orangepi.yml
fi

echo "[4/5] Installing Python service dependencies..."
conda run -n "${ENV_NAME}" python -m pip install -r requirements-orangepi.txt

echo "[5/5] Preparing runtime directories..."
mkdir -p uploads

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Edit .env before starting the service."
fi

echo
echo "Orange Pi setup complete."
echo "Next steps:"
echo "  1. Edit .env and set QWEN_API_KEY / DEEPSEEK_API_KEY."
echo "  2. Start the service with: bash start_orangepi.sh"
