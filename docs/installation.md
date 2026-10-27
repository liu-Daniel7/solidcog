# 安装与排障

[返回 README](../README.md)

选择一种运行方式：Apple Silicon 本地模型、Windows / WSL2 本地模型，或仅使用云端 OCR。Qwen API Key 在云端 OCR 或 Qwen-MT 通道中需要。

## macOS / Apple Silicon

### 1. 安装依赖

需要 Apple Silicon、Python 3.12 和支持多模态模型的 `llama-server`。历史验证机器为 M2 / 24 GB 统一内存；这不是所有图纸和量化配置的最低内存保证。

```sh
brew install python@3.12 llama.cpp
git clone https://github.com/liu-Daniel7/solidcog.git
cd solidcog
./platforms/macos/setup.sh
```

安装脚本复用已有 `.venv`，安装主应用及 macOS 运行库，不下载模型。更新现有环境前先停止启动器，再执行同一安装命令。

### 2. 准备模型

本地推理需要两组 GGUF 模型及匹配的视觉投影文件：

| 配置项 | 默认路径 |
| --- | --- |
| `MINERU_MODEL_PATH` | `models/mineru/mineru-text-q8.gguf` |
| `MINERU_MMPROJ_PATH` | `models/mineru/mineru-vision-f16.gguf` |
| `MECHVL_MODEL_PATH` | `models/mechvl/mechvl-model-q5.gguf` |
| `MECHVL_MMPROJ_PATH` | `models/mechvl/mechvl-vision-f16.gguf` |

原始权重来源：[MinerU2.5-Pro](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B)、[MechVL-4B-RL](https://huggingface.co/XiaofengAlg/MechVL-4B-RL)。原始权重不能直接改名为 GGUF 使用；需准备兼容的转换产物与视觉投影文件。本仓库没有一键下载或转换流程。

文件名可以不同，在 `.env` 中修改相应路径即可；相对路径按仓库根目录解析。

### 3. 配置并启动

```sh
# 保留已有配置；首次使用时才复制
[ -f .env ] || cp .env.example .env
command -v llama-server
```

若启动器不能自动找到 llama-server，在 `.env` 写入实际路径，例如：

```dotenv
LLAMA_SERVER_PATH=/opt/homebrew/bin/llama-server
```

只有使用 Qwen 时才填写 `QWEN_API_KEY`。随后运行：

```sh
./start_macos.command
```

打开 [工作台](http://127.0.0.1:8000/home)。启动器先检查调度器，再启动或复用网页服务；默认不加载大模型，选择本地 OCR 或问答后才按需加载。

## Windows / WSL2

### 当前兼容性边界

- **MechVL 流式问答已接通。** WSL MechVL 服务通过 `/analyze/stream` 返回 Server-Sent Events；WSL 模型调度器 `/mechvl/analyze/stream` 负责加载/切换模型并转发事件，供主应用 `/chat-with-drawing/stream` 使用。CUDA 不可用或模型加载失败时，本地问答仍不可用。
- **MinerU 需要匹配旧接口。** 调度器启动 `~/.local/share/solidcog/mineru/.venv/bin/mineru-api`，并调用 `/file_parse`，依赖 `--enable-vlm-preload` 参数及旧响应结构。不能假定任意 MinerU 4.x 完整服务兼容；更新 MinerU 前应先检查这份 API 契约。
- **模型权重独立于代码。** `mechvl_server/download_model.sh` 从 Hugging Face 下载 MechVL；权重不随仓库发布。MinerU 安装和模型缓存也需单独准备。

只需要上传、识别和检索时，也可以先使用[云端 OCR](#仅使用云端-ocr)。

### 基础环境

- Windows 10 22H2 / Windows 11，x64，Python 3.12、Git。
- 本地 CUDA 需要 WSL2 Ubuntu 与支持 WSL2 CUDA 的 NVIDIA Windows 驱动。
- 历史验证设备为 RTX 4060 Laptop / 8 GB；建议至少 16 GB 系统内存、40 GB 可用磁盘，预留更多空间供权重与缓存使用。
- 需要访问 PyPI、PyTorch 和 Hugging Face 下载依赖与模型。

管理员 PowerShell：

```powershell
wsl --install -d Ubuntu
```

按提示重启并完成 Ubuntu 用户创建，检查 `wsl --list --verbose` 显示 WSL2。在 Ubuntu 中运行 `nvidia-smi` 确认 GPU 可见；WSL 内无需另装 Linux NVIDIA 驱动。

### 主应用安装

普通 PowerShell：

```powershell
git clone https://github.com/liu-Daniel7/solidcog.git
cd solidcog
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 首次使用时复制；保留已有 .env
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

### 本地 MechVL 与调度器

在 WSL Ubuntu 中进入仓库目录，例如 `/mnt/c/path/to/solidcog`，执行：

```sh
sudo apt-get update
sudo apt-get install -y python3-venv
bash mechvl_server/setup_wsl.sh
bash mechvl_server/download_model.sh
bash model_scheduler/setup.sh
```

MechVL 安装脚本安装 CUDA PyTorch，并尝试安装 FlashAttention；不可用时回退到 SDPA。`download_model.sh` 从 Hugging Face 下载模型权重。更新现有环境前先停止 SolidCog 和 WSL 模型服务；不要删除已有模型缓存或项目数据。

**MinerU 需单独配置。** 调度器预期可执行文件位于 `~/.local/share/solidcog/mineru/.venv/bin/mineru-api`，并要求支持 `--enable-vlm-preload` 参数、`/file_parse` 及旧返回结构。当前已在 `mineru 3.4.5` 的现有 WSL 环境中完成一页真实 PDF 解析验证；这是已验证的兼容示例，不代表任意版本或全新安装自动满足契约。

兼容环境准备完成后，在 Windows 仓库根目录运行：

```powershell
.\start_server.bat
```

启动器会尝试在 WSL 后台启动调度器。需要前台诊断时使用 `start_scheduler_wsl.bat`。调度器健康状态可在 `http://127.0.0.1:8090/status` 查看；首次切换模型会加载权重，耗时取决于模型缓存和磁盘速度。

### 验证记录（2026-10-27）

在 Windows + WSL2、RTX 4060 Laptop 8 GB 上完成以下验证（复用既有环境，并非全新安装）：

| 组件 | 本机实测版本 |
| --- | --- |
| Windows 主应用 Python | 3.12.14 |
| WSL Python | 3.12.3 |
| PyTorch / CUDA | 2.11.0+cu128 |
| Transformers / bitsandbytes | 5.16.1 / 0.50.2 |
| MinerU / mineru-vl-utils | 3.4.5 / 1.2.1 |

- 主应用全套 Python 回归测试通过；调度器 `/mechvl/analyze/stream` 的中文 SSE 转发、上游失败资源释放和忙碌拒绝有自动化测试。
- MechVL 使用现有缓存启动成功，CUDA 与 FlashAttention 2 可用；真实图纸问答经调度器返回 27 个 SSE 数据事件。
- MinerU 使用 `mineru 3.4.5`，真实单页 PDF 经 `/file_parse` 返回完整结果，冷启动至返回结果约 84 秒，包含模型切换、预载和解析，不是纯 OCR 推理耗时。

本机 WSL 无法访问 Hugging Face 时，模型加载使用的是已有缓存；重新部署时需联网下载权重。上述记录证明这套本机环境可用，不构成对其他 MinerU 安装、模型缓存或图纸识别准确率的保证。

## 仅使用云端 OCR

不需要本地 GPU、GGUF、WSL 或模型调度器；不提供 MechVL 本地问答。

安装 Python 3.12 后克隆仓库，在根目录创建虚拟环境：

```sh
# macOS / Linux
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

Windows 使用上面的“主应用安装”命令。编辑 `.env`：

```dotenv
QWEN_API_KEY=replace-with-your-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_VL_MODEL=qwen3-vl-plus
QWEN_MT_MODEL=qwen-mt-plus
QWEN_OCR_MAX_PAGES=10
```

也可以先启动工作台，在右上角“处理设置”中填写 Key；保存后会写回本机 `.env`（POSIX 下权限 0600），页面只显示掩码。同一个 Key 默认同时供 Qwen-VL 与 Qwen-MT 使用；保存后可用“测试两个模型”验证。将占位值替换成自己的 Key，并确认对应模型可用。示例是中国内地北京地域兼容地址；其他地域按 DashScope 控制台配置。不要提交 `.env`。

直接启动主应用，避免平台启动器尝试启动本地调度器：

```sh
# macOS / Linux
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

```powershell
# Windows
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 [工作台](http://127.0.0.1:8000/home)，在“处理设置”中选 Qwen。调度器未连接的提示不影响云端 OCR 与图纸管理。页面图像会发送至 DashScope，分块补读可能增加调用次数与费用。

## 常见问题

| 现象 | 排查方式 |
| --- | --- |
| macOS 调度器未连接 | 用 `./start_macos.command` 启动；检查 `http://127.0.0.1:8090/health` 及 `~/Library/Logs/SolidCog/scheduler.log` |
| 模型加载失败 | 核对 GGUF、视觉投影文件与 `LLAMA_SERVER_PATH`；模型文件不随代码下载 |
| 本地模型切换慢 | 冷启动包含权重加载；先集中 OCR 再集中问答，减少切换 |
| 任务忙碌时无法切换 | 等待当前任务完成；调度器在推理期间拒绝切换以保护资源 |
| Qwen 配置或额度错误 | 核对 API Key、地域地址、模型权限与额度 |
| MinerU 返回 404 / 解析结构不兼容 | WSL 检查旧 API 兼容性；macOS 应使用仓库包装服务及锁定依赖 |
| Windows 网页问答失败 | 检查 `/local-model/status`、WSL 调度器 8090、MechVL 8100 和模型加载日志；流式转发路径见上文 |
| OCR 遗漏尺寸或页数不足 | 查看页面告警与原始输出；Qwen 页数由 `QWEN_OCR_MAX_PAGES` 控制，关键标注需要人工复核 |

### 日志与服务地址

| 项目 | 地址 / 位置 |
| --- | --- |
| 工作台 / API 文档 / 关于 | `:8000/home` / `:8000/docs` / `:8000/about` |
| 调度器健康 / 状态 | `127.0.0.1:8090/health` / `127.0.0.1:8090/status` |
| macOS 模型日志与切换记录 | `~/Library/Application Support/SolidCog/scheduler/` |
| WSL 模型日志与切换记录 | `~/.local/share/solidcog/scheduler/` |
| macOS MinerU 运行库版本 | 模型就绪后查看 `127.0.0.1:8200/health` 中的 `runtime_versions` |

服务默认用于本机访问。本仓库没有开箱即用的公网账号、租户隔离或部署鉴权方案；对外提供服务前需要额外的部署设计，并保留 [MinerU 标识](attribution.md)。
