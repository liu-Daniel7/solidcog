# SolidCog macOS 平台层

此目录只包含 macOS/Apple Silicon 实现；`model_scheduler/scheduler.py` 是共享进程调度核心，`model_scheduler/server.py`、`mechvl_server/` 和 `.bat` 文件保留 Windows/WSL 实现，互不覆盖。

- 调度器：`127.0.0.1:8090`
- MinerU llama.cpp/Metal 服务：按需运行于 `8200`
- MechVL llama.cpp/Metal 服务：按需运行于 `8100`
- 两个大模型互斥运行，切换时释放统一内存

首次安装：`./platforms/macos/setup.sh`

启动：双击根目录 `start_macos.command`，或执行 `./start_macos.command`。

若 llama-server 移动，请在 `.env` 或 shell 中设置 `LLAMA_SERVER_PATH`。

启动器会先检查 macOS 调度器健康状态，再启动或复用网页服务。若网页已运行而调度器缺失，会补启调度器；退出时只关闭本次启动的进程。请保持启动器终端打开。调度器日志位于 `~/Library/Logs/SolidCog/scheduler.log`。

安装脚本会复用已有 `.venv`，不会删除它；已有 `.env`、模型和数据库也应保留。模型权重需要单独准备，不包含在仓库中。
