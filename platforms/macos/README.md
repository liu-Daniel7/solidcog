# SolidCog macOS 平台层

此目录只包含 macOS/Apple Silicon 实现；根目录的 `model_scheduler/`、`mechvl_server/` 和 `.bat` 文件仍属于 Windows/WSL 版本，互不覆盖。

- 调度器：`127.0.0.1:8090`
- MinerU llama.cpp/Metal 服务：按需运行于 `8200`
- MechVL llama.cpp/Metal 服务：按需运行于 `8100`
- 两个大模型互斥运行，切换时释放统一内存

首次安装：`./platforms/macos/setup.sh`

启动：双击根目录 `start_macos.command`，或执行 `./start_macos.command`。

若 llama-server 移动，请在 `.env` 或 shell 中设置 `LLAMA_SERVER_PATH`。
