# 平台运行时

平台目录只存放操作系统或硬件后端相关代码。跨平台业务请放在根目录 `app/`、`templates/` 和 `tests/`。

- `macos/`：Apple Silicon + Metal + llama.cpp
- `../mechvl_server/`：Windows/WSL + CUDA/WSL 模型服务
- `../model_scheduler/`：跨平台模型生命周期和互斥调度逻辑

新增平台时，优先实现统一的健康检查、启动、停止和模型调用协议，不要复制上传、OCR 结果保存或页面逻辑。
