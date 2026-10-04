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

## MinerU 版本与更新

macOS 服务直接调用 `mineru-vl-utils[llama-cpp]`，而非完整 `mineru`
主程序。当前验证版本为 `mineru-vl-utils==2.0.5` 和
`mineru-llama-cpp==0.1.2`。更新现有环境时，先停止启动器，再运行
`./platforms/macos/setup.sh`，随后重新启动应用。

MinerU 服务的 `GET http://127.0.0.1:8200/health` 会返回
`runtime_versions`；每次解析结果和原始结果文件也保留实际运行库版本。
服务按需加载，未启动时该端口不监听。

截至 2026-10-04，官方完整主程序是 `mineru 4.0.10`，其默认 VLM 仍为
`MinerU2.5-Pro-2605-1.2B`。完整 4.x 带有额外的小模型、文档库、SDK
和 V1 HTTP API；它不提供当前适配使用的旧 `/file_parse` 接口，不能
通过替换版本号直接接入此服务。完整主程序版本、VLM 模型版本和运行库
版本应分别核对。见 [版本核对与验证](../../docs/mineru-upgrade-2026-10-04.md)。
