#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate
python -c "from huggingface_hub import snapshot_download; snapshot_download('XiaofengAlg/MechVL-4B-RL')"
