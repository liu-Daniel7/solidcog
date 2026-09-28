# SolidCog 架构与平台协作约定

## 目录边界

```text
app/                 跨平台业务核心：API、业务服务、数据库、任务队列
templates/           跨平台 Web 界面
model_scheduler/     跨平台模型生命周期与互斥调度
platforms/macos/     Apple Silicon/Metal 的运行时实现
mechvl_server/       Windows/WSL 的 MechVL 运行时实现
start_macos.command  macOS 启动入口
start_server.bat     Windows/WSL 启动入口
```

业务代码应优先放在 `app/`，只有涉及操作系统、GPU 后端、进程启动命令或模型文件路径时，才放入 `platforms/` 或现有平台运行时目录。

## Git 分支约定

- `main`：稳定的跨平台主干。
- `feature/<name>`：跨平台功能，例如 `feature/async-ocr-progress`。
- `platform/macos-<name>`：macOS 专属适配，完成后尽快合并回 `main`。
- `platform/windows-<name>`：Windows/WSL 专属适配，完成后尽快合并回 `main`。

不要长期维护一套完全独立的 `macos` 和 `windows` 业务分支。跨平台功能应通过 Pull Request 合并到 `main`；平台差异保留在平台目录和启动脚本中。

## Pull Request 分类

### 应合并到 `main`

- `app/`、`templates/`、`tests/` 的通用功能
- API、数据库、OCR 结果契约、异步任务和并发控制
- 前端交互和错误处理

### 平台适配提交

- Metal/CUDA/WSL 参数
- `llama-server`、模型进程和健康检查
- 安装、启动、停止脚本
- 平台专属依赖

平台适配提交也应尽量只修改对应目录，不要复制一份业务实现。

## 当前运行入口

```text
macOS:       ./start_macos.command
Windows/WSL: start_server.bat
```

两种平台应尽量提供相同的 Web API 和页面行为；只允许底层模型运行方式不同。

## 任务与并发

OCR、MechVL 等长任务使用任务 ID 和进度接口，不应让浏览器长时间保持一个同步 HTTP 请求。当前 macOS 使用单进程、单 OCR worker，适配本地 GPU 互斥模型；未来多进程部署时，应将任务状态和锁迁移到 SQLite/Redis 等共享存储。
