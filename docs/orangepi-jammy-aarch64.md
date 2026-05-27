# Orange Pi Ubuntu 22.04 aarch64 Deployment

This guide targets Orange Pi boards running Ubuntu 22.04 Jammy on `aarch64`.

## Runtime Strategy

- Use Miniforge because it has good `linux-aarch64` support through conda-forge.
- Create a dedicated conda environment named `solidcog-py39`.
- Pin Python to `3.9.13`.
- Use Qwen-VL OCR first by setting `USE_QWEN_VL_OCR=true`.
- Treat local PaddleOCR as a later optional step, because PaddlePaddle wheels on ARM Linux can be version-sensitive.

## Install

```bash
git clone https://github.com/liu-Daniel7/solidcog.git
cd solidcog
bash install_orangepi.sh
```

Edit `.env` after installation:

```bash
QWEN_API_KEY=your-qwen-api-key
DEEPSEEK_API_KEY=your-deepseek-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
USE_QWEN_VL_OCR=true
```

Start the service:

```bash
bash start_orangepi.sh
```

Open the app from another machine on the same network:

```text
http://<orange-pi-ip>:8000/home
```

## Notes

- The app stores uploaded files in `uploads/`.
- The SQLite database file is `database.db`.
- `.env`, `uploads/`, and `*.db` are already ignored by Git.
- Rotate any API keys that were previously committed or shared.

## Optional Local OCR

The Orange Pi migration keeps PaddleOCR optional. After the web service is running with Qwen-VL, test PaddlePaddle/PaddleOCR separately in the `solidcog-py39` environment before setting:

```bash
USE_QWEN_VL_OCR=false
```

If local PaddleOCR is needed, prefer testing it on the Orange Pi itself because package availability depends on the exact board image, Python build, and PaddlePaddle release.
