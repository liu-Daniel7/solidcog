# SolidCog

> 面向机械工程图纸的本地智能解析、检索与审核工作台

[![License](https://img.shields.io/badge/license-Apache--2.0-2ea44f)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%2B%20WSL2-0078d4)](#完整安装)
[![GPU](https://img.shields.io/badge/GPU-RTX%204060%208GB%20verified-76b900)](#性能与证据)
[![OCR](https://img.shields.io/badge/OCR-MinerU2.5--Pro%20%7C%20Qwen3--VL-0ea5e9)](#mineru-文档解析能力)
[![Review](https://img.shields.io/badge/review-MechVL--4B--RL-111827)](#mechvl-机械图纸理解能力)

[核心能力](#核心能力) · [技术创新](#技术创新) · [模型依据](#mineru-文档解析能力) · [性能实测](#性能与证据) · [系统架构](#系统架构) · [快速开始](#快速开始) · [完整安装](#完整安装) · [论文引用](#论文引用)

---

SolidCog 将通用文档解析模型 `MinerU2.5-Pro`、机械图纸多模态模型 `MechVL-4B-RL` 与可选的 `Qwen3-VL-Plus` 云端 OCR 组合为一套可在消费级显卡上运行的图纸工作流。系统支持图纸上传、结构化 OCR、全文检索、结果导出和交互式审图，并通过本地调度器保证两个 GPU 模型不会同时驻留显存。

## 目录

- [项目要解决的问题](#项目要解决的问题)
- [核心能力](#核心能力)
- [技术创新](#技术创新)
- [MinerU 文档解析能力](#mineru-文档解析能力)
- [MechVL 机械图纸理解能力](#mechvl-机械图纸理解能力)
- [性能与证据](#性能与证据)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [完整安装](#完整安装)
- [完整流程验证](#完整流程验证)
- [本地模型调度器](#本地模型调度器)
- [开发者参考](#开发者参考)
- [数据与隐私](#数据与隐私)
- [常见问题](#常见问题)
- [论文引用](#论文引用)
- [许可证](#许可证)

## 项目要解决的问题

机械工程图纸不是普通的连续文本。标题栏、技术要求、多视图投影、尺寸与公差、表面粗糙度、公式和表格同时存在，文字往往尺寸小、分布稀疏，并依赖所在区域和视图才能正确解释。单一 OCR 模型可以读取文字，却不一定理解视图、装配和规范；单一机械视觉语言模型可以回答工程问题，但不适合承担批量结构化抽取与全文检索。

SolidCog 因此将任务拆分为三个层次：

1. MinerU 或 Qwen3-VL 将 PDF/PNG 转换为可检索的结构化内容。
2. SQLite 与本地文件系统管理图纸、OCR 结果和检索索引。
3. MechVL 结合图像与 OCR 上下文执行机械图纸问答和辅助审核。

## 核心能力

| 能力 | 实现 | 适用场景 |
| --- | --- | --- |
| 本地结构化 OCR | MinerU2.5-Pro 1.2B | 标题、正文、列表、公式、表格、布局和阅读顺序提取 |
| 云端 OCR | Qwen3-VL-Plus | 无可用本地 GPU 或希望按批次使用云端识别 |
| 机械图纸理解 | MechVL-4B-RL | 标注查找、视图关系、装配关系、尺寸推理和初步规范检查 |
| 图纸管理 | FastAPI + SQLite | 批量上传、结果查看、文本导出、删除和本地持久化 |
| 全文检索 | 文件名 + OCR 内容 | 按标题栏、技术要求、材料或任意识别文本查找图纸 |
| 单卡模型切换 | 本地调度器 | MinerU 与 MechVL 分时驻留，显示阶段、已用时间和预计剩余时间 |

支持批量上传 PDF、PNG 图纸，单文件最大 50 MB。OCR 后端按上传批次选择，聊天记录仅保留在当前浏览器标签页，并可手动清理。

## 技术创新

### 面向任务的双模型分工

MinerU 负责可复用的结构化解析结果，MechVL 负责需要领域知识的机械图纸理解。OCR、检索和审图不再被压入同一个提示词或同一个模型请求中，每个组件拥有清晰的输入、输出和故障边界。

### 消费级单卡互斥调度

RTX 4060 Laptop 8 GB 无法稳定同时容纳 MinerU 与 MechVL。SolidCog 调度器先停止当前模型进程组、等待服务端口关闭并释放显存，再启动目标模型；模型执行任务时拒绝切换，完成后继续驻留，兼顾显存安全与连续操作效率。

### 可观测的模型转换器

浏览器可查看 `idle`、`MinerU OCR` 和 `MechVL 审核` 三种状态。切换过程展示当前阶段与已用时间，并根据目标模型最近五次启动记录估算剩余时间。手动切换与 OCR/问答触发的自动切换使用同一状态机。

### 本地与云端可选择

敏感图纸可以使用 MinerU 本地处理；没有本地 GPU 或需要不同识别路径时，可以按批次选择 Qwen3-VL 云端 OCR。选择云端模式时，图纸页面会发送至 DashScope。

上述内容是 SolidCog 已实现的工程设计，不将模型组合、服务代理或计时功能表述为新的 OCR 算法。

## MinerU 文档解析能力

当前部署使用 `MinerU2.5-Pro-2605-1.2B`。其能力说明来自 MinerU 系列技术报告和公开评测，而不是根据项目界面反推。

### 从传统解析流水线到两阶段高分辨率解析

原始 MinerU 技术报告描述了一套由布局检测、分区 OCR、公式识别、表格解析、阅读顺序恢复及前后处理组成的文档解析流水线。分区域识别可以减少多栏文本被错误合并，并通过公式坐标遮罩与回填保持行内公式的位置。

MinerU2.5 采用由全局到局部的两阶段视觉语言模型：首先在低分辨率页面上分析整体布局，随后从原始高分辨率页面裁剪目标区域，并对文字、公式和表格进行精细识别。这种设计避免把整张高分辨率页面直接送入模型，在保留细小文字和复杂元素识别能力的同时控制视觉 token 与计算开销。

MinerU2.5-Pro 保持 1.2B 参数架构不变，重点通过数据构建、清洗和训练策略提高解析性能。这与 SolidCog 当前使用的 Pro 模型版本直接对应。

### 在 SolidCog 中承担的任务

- 识别标题、正文、列表和多栏区域，并恢复主要阅读顺序。
- 提取技术要求、材料、标准说明和标题栏等可检索文本。
- 识别公式并保留结构化表达。
- 解析表格单元格关系，输出 HTML/Markdown 表格内容。
- 保留图片、表格和其他页面元素的类型及布局信息。
- 输出 Markdown、content list 和中间布局结果，供 SolidCog 映射为标题栏、技术要求、全文和原始结构化记录。

MinerU 是通用文档解析模型，不负责判断机械投影关系、装配合理性或制图规范。低清扫描、极小标注、非标准字体、密集尺寸线和专用工程符号仍可能造成漏识别或误识别，关键结果必须由工程师复核。

## MechVL 机械图纸理解能力

MechVL-4B-RL 是论文 *MechVQA: Benchmarking and Enhancing Multimodal
LLMs on Comprehensive Mechanical Drawing Understanding* 中提出的机械图纸领域
多模态模型。它针对普通视觉语言模型在高密度标注、正投影关系和机械制图规范上的
不足进行训练，能够读取零件图、装配图以及包含多视图的复杂图纸，并结合图像与问题
给出带推理依据的回答。

论文将能力划分为三个层级、十个细分任务：

- **识别（Recognition）**：识别与计数图中实体；读取尺寸、标注和特殊符号；理解标题栏、技术要求、参数表和 BOM 等文字与表格；定位指定项目所在的视图或区域。
- **推理（Reasoning）**：理解图纸结构与语义；根据尺寸链和约束进行几何/尺寸计算；推断零件之间的装配关系；依据正投影规则在不同视图之间建立对应关系。
- **判定（Judging）**：检测缺失或冲突的标注和内容；检查图纸与制图标准、视图或尺寸之间的一致性。

这些能力适合用于图纸信息核对、尺寸和标注查找、视图关系分析、装配关系问答以及
初步的规范性审核。当前 SolidCog 通过 `/chat-with-drawing` 将选定图纸和用户问题
发送给本地 MechVL 服务，提供交互式问答；模型回答属于决策支持，不能替代工程师的
最终审图和设计签核。

论文中的 MechVQA 基准包含约 3.3K 张高密度机械图纸和 21K 个问答对。论文报告
`MechVL-4B-RL` 的总分为 **84.85**，在识别、推理和判定三类能力上的平均分分别为
**89.70、77.04 和 82.81**；该结果是论文基准评测，不代表本项目对任意实际图纸的
准确率保证。

## 性能与证据

### 论文报告

| 来源 | 论文报告结果 | 解释边界 |
| --- | --- | --- |
| MinerU2.5 | `olmOCR-bench` Overall 75.2 | 论文公开基准结果，不代表 SolidCog 对任意机械图纸的准确率 |
| MinerU2.5-Pro | `OmniDocBench v1.6` 95.69，较同架构基线提高 2.71 分 | Pro 论文配置与公开基准结果，不等于当前安装环境的复现实验 |
| MechVL-4B-RL | MechVQA 总分 84.85；识别 89.70、推理 77.04、判定 82.81 | MechVQA 论文基准结果，不代表实际生产审图通过率 |

MinerU2.5 论文还报告了其在多栏、表格、旧扫描件、页眉页脚和细小长文本等文档类型上的分项结果。不同论文使用的数据集、指标和推理配置不同，不应仅依据单一总分进行跨模型结论外推。

### SolidCog 本机实测

测试环境为 RTX 4060 Laptop 8 GB、Windows + WSL2，MinerU 与 MechVL 分时运行：

| 项目 | 实测结果 |
| --- | --- |
| MinerU 模型 | MinerU2.5-Pro 1.2B |
| MinerU 首次完整冷启动 | 约 86 秒 |
| MinerU 后续启动 | 约 53-106 秒 |
| 单张工程图解析 | 约 9-12 秒 |
| MinerU 峰值显存 | 7413 MiB |
| MechVL 缓存后启动 | 约 205-271 秒 |
| MinerU 卸载 | 约 3.8 秒 |
| 模型互斥 | 8100 与 8200 模型服务不同时监听 |

这些数据用于描述当前机器的可行性，不是跨设备性能承诺。页面尺寸、内容复杂度、模型缓存、磁盘速度、驱动和后台显存占用都会影响结果。

## 系统架构

```text
浏览器（http://127.0.0.1:8000/home）
  |
  +-- SolidCog / FastAPI（Windows，端口 8000）
       +-- SQLite：图纸元数据和 OCR 结果
       +-- uploads/：原始图纸文件
       +-- DashScope qwen3-vl-plus：可选云端 OCR
       +-- 模型调度器（WSL2，端口 8090）
            +-- MinerU（端口 8200）：本地图纸 OCR
            +-- MechVL（端口 8100）：本地图纸问答
```

`start_server.bat` 会自动启动轻量调度器。调度器默认不加载 GPU 模型，用户选择模式或执行 OCR/问答时才加载目标模型；当前模型会保持驻留到下一次切换。

## 快速开始

已经完成依赖和模型安装时，只需：

```powershell
cd C:\path\to\solidcog
.\start_server.bat
```

打开 <http://127.0.0.1:8000/home>。首次使用建议按以下顺序验证：

1. 在“OCR 后端”选择 MinerU 本地或 Qwen3-VL 云端。
2. 上传一张 PDF/PNG，等待 OCR 与模型切换完成。
3. 查看 OCR 结果，并用其中的材料或技术要求进行检索。
4. 选择图纸向 MechVL 提问，观察调度器从 MinerU 切换至 MechVL。

尚未安装环境时，从下一节开始执行。安装仅需一次，之后使用上述启动命令即可。

## 完整安装

### 运行要求

#### 必需条件

- Windows 10 22H2 或 Windows 11
- 支持 WSL2 的 x64 处理器
- Python 3.12 x64、Git
- NVIDIA 独立显卡及支持 WSL2 CUDA 的最新 Windows 驱动
- 阿里云 DashScope API Key，并已开通 `qwen3-vl-plus`
- 能访问 PyPI、PyTorch 和 Hugging Face

#### 硬件建议

| 项目 | 最低建议 | 推荐 |
| --- | --- | --- |
| NVIDIA 显存 | 8 GB，4-bit 推理 | 12 GB 或更高 |
| 系统内存 | 16 GB | 32 GB |
| 可用磁盘 | 40 GB | 60 GB |

RTX 4060 Laptop 8 GB 已验证 MinerU2.5 和 4-bit MechVL 分时运行。MinerU 峰值约 7413 MiB，必须关闭其他大显存程序并通过调度器互斥切换。没有 NVIDIA GPU 时仍可使用 Qwen OCR 和图纸管理。

### 一、安装基础软件

在 PowerShell 中确认 Git 和 Python：

```powershell
git --version
py -3.12 --version
```

若命令不存在，请先安装 [Git for Windows](https://git-scm.com/download/win) 和 [Python 3.12](https://www.python.org/downloads/)。安装 Python 时勾选 `Add python.exe to PATH`。

以管理员身份打开 PowerShell，安装 WSL2 Ubuntu：

```powershell
wsl --install -d Ubuntu
```

按提示重启 Windows，首次打开 Ubuntu 时创建 Linux 用户名和密码。随后在普通 PowerShell 中确认 WSL2：

```powershell
wsl --list --verbose
```

`Ubuntu` 的 `VERSION` 应为 `2`。若不是：

```powershell
wsl --set-version Ubuntu 2
```

安装最新 [NVIDIA Windows 驱动](https://www.nvidia.com/Download/index.aspx)，然后在 Ubuntu 终端中确认 WSL2 可以访问显卡：

```bash
nvidia-smi
```

这里必须显示 NVIDIA GPU。不要在 WSL2 内另外安装 Linux NVIDIA 显卡驱动。

### 二、克隆仓库并安装主程序

以下命令均在普通 PowerShell 中执行：

```powershell
git clone --branch branch_test https://github.com/liu-Daniel7/solidcog.git
cd solidcog
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

验证主程序依赖：

```powershell
.\.venv\Scripts\python.exe -c "import fastapi, pypdfium2, openai, PIL; print('SolidCog dependencies OK')"
```

预期输出为 `SolidCog dependencies OK`。

### 三、配置 Qwen3-VL-Plus

在仓库根目录执行：

```powershell
Copy-Item .env.example .env
notepad .env
```

把第一行替换为自己的 DashScope API Key：

```dotenv
QWEN_API_KEY=replace-with-your-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_VL_MODEL=qwen3-vl-plus
QWEN_OCR_MAX_PAGES=10
MECHVL_BASE_URL=http://127.0.0.1:8100
MECHVL_TIMEOUT_SECONDS=600
MECHVL_STARTUP_TIMEOUT=900
MODEL_SCHEDULER_BASE_URL=http://127.0.0.1:8090
MODEL_SWITCH_TIMEOUT_SECONDS=360
MINERU_TIMEOUT_SECONDS=600
```

不要把 `.env` 提交到 Git。中国内地 DashScope 使用上述北京地域地址；若 Key 属于其他地域，请按 DashScope 控制台提供的兼容模式地址修改 `QWEN_BASE_URL`。

### 四、安装本地模型与调度器

仍在仓库根目录的 PowerShell 中执行：

```powershell
wsl.exe -d Ubuntu -- bash -lc "sudo apt-get update && sudo apt-get install -y python3-venv"
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a "$PWD").Trim()
wsl.exe -d Ubuntu -- bash -lc "cd '$repoWsl/mechvl_server' && bash setup_wsl.sh"
wsl.exe -d Ubuntu -- bash -lc "cd '$repoWsl/mechvl_server' && bash download_model.sh"
wsl.exe -d Ubuntu -- bash -lc "mkdir -p ~/.local/share/solidcog/mineru && python3 -m venv ~/.local/share/solidcog/mineru/.venv"
wsl.exe -d Ubuntu -- bash -lc "~/.local/share/solidcog/mineru/.venv/bin/pip install --upgrade pip && ~/.local/share/solidcog/mineru/.venv/bin/pip install 'mineru[all]'"
wsl.exe -d Ubuntu -- bash -lc "cd '$repoWsl/model_scheduler' && bash setup.sh"
```

三个环境相互独立：`mechvl_server/.venv` 运行 MechVL，`~/.local/share/solidcog/mineru/.venv` 运行 MinerU，`model_scheduler/.venv` 只运行轻量调度服务。安装可能持续较长时间。

首次点击“MinerU OCR”时自动下载约 2.2 GB 的 MinerU2.5 模型。也可以用真实图纸提前触发下载：

```powershell
wsl.exe -d Ubuntu -- bash -lc "~/.local/share/solidcog/mineru/.venv/bin/mineru -p '$repoWsl/sample.png' -o /tmp/mineru-smoke -b vlm-engine"
```

下载完成后检查模型缓存：

```powershell
wsl.exe -d Ubuntu -- bash -lc "du -sh ~/.cache/huggingface/hub/models--XiaofengAlg--MechVL-4B-RL"
```

若下载中断，重新执行 `download_model.sh` 即可续传。

### 五、启动 SolidCog

在仓库根目录打开 PowerShell：

```powershell
.\start_server.bat
```

检查主服务：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8000/
```

然后打开：

- 工作台：<http://127.0.0.1:8000/home>
- 主服务 API：<http://127.0.0.1:8000/docs>
- 调度器状态：<http://127.0.0.1:8090/status>

启动脚本检测 8090 端口；调度器未运行时会在 WSL 后台启动。也可以用 `start_scheduler_wsl.bat` 在独立终端查看调度日志。

## 完整流程验证

1. 打开工作台，在 OCR 后端选择“MinerU 本地”。
2. 上传一张清晰的 PDF 或 PNG，观察 MinerU 切换计时器并等待 OCR 完成。
3. 点击“查看 OCR”，确认标题栏、技术要求或完整文字已有内容。
4. 返回工作台，输入刚识别出的文字，确认能检索到该图纸。
5. 在问答区选择图纸并提问，系统会自动停止 MinerU、释放显存并加载 MechVL。
6. 确认转换器显示 MechVL 已就绪并得到回答。
7. 将 OCR 后端改为 Qwen3-VL，上传另一张图纸，确认本地 GPU 模式不变。

完成以上七步即表示 DashScope OCR、SQLite、文件存储、全文检索和 MechVL 本地问答链路均正常。

### 日常启动

安装只需执行一次。以后只运行 `start_server.bat`。模型转换器支持“空闲”“MinerU OCR”“MechVL 审核”；自动操作与手动点击使用同一套互斥调度逻辑。

## 本地模型调度器

1. 浏览器通过 `/local-model/status` 读取调度器真实状态。
2. 手动切换调用 `/local-model/switch/{mode}`；接口立即返回，页面继续轮询阶段与计时。
3. MinerU 上传将 `ocr_backend=mineru` 传给 `/upload-drawing`，主服务把文件发送到调度器 `/mineru/parse`。
4. 调度器停止 MechVL 进程组，等待端口关闭和显存释放，再启动 `mineru-api`。
5. MinerU 返回 Markdown 和 content list；适配器映射为标题栏、技术要求、全文和布局，原始结果保存到 `mineru_results/`。
6. MechVL 问答发送到调度器 `/mechvl/analyze`；调度器按相反顺序切换并代理原有请求。
7. 调度器按模型记录最近五次切换耗时，状态接口返回已用时间与预计总时间，页面计算预计剩余时间。
8. 模型执行任务时 `busy=true`，所有切换请求被拒绝，任务完成后当前模型继续驻留。

手动检查和切换：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8090/status
curl.exe --noproxy "*" -X POST http://127.0.0.1:8090/switch/mineru
curl.exe --noproxy "*" -X POST http://127.0.0.1:8090/switch/mechvl
curl.exe --noproxy "*" -X POST http://127.0.0.1:8090/switch/idle
```

日志位于 WSL 的 `~/.local/share/solidcog/scheduler/`。`mineru.log` 和 `mechvl.log` 分别记录模型启动与推理错误，`timings.json` 保存切换历史。

## 开发者参考

### 测试

测试使用临时数据库和上传目录，不会修改正式数据：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### 项目结构

```text
solidcog/
├─ main.py                  # FastAPI 入口
├─ app/
│  ├─ application.py       # 应用创建与路由注册
│  ├─ config.py            # 环境变量和路径
│  ├─ database.py          # SQLite 初始化
│  ├─ repositories/        # 数据访问
│  ├─ routers/             # 页面、图纸和 AI HTTP 接口
│  └─ services/            # OCR、文件、Qwen、MechVL 业务逻辑
├─ mechvl_server/           # WSL2 本地模型服务
├─ model_scheduler/         # MinerU/MechVL 互斥调度服务
├─ templates/               # HTML/CSS/JavaScript 页面
├─ tests/                   # 标准库 unittest 测试
├─ requirements.txt        # Windows 主服务依赖
├─ start_server.bat        # SolidCog 与调度器日常启动入口
└─ start_scheduler_wsl.bat # 调度器前台诊断入口
```

### 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/` | 主服务状态 |
| `GET` | `/home` | 工作台 |
| `GET` | `/search` | 文件名与 OCR 全文检索 |
| `POST` | `/upload-drawing` | 上传并 OCR |
| `GET` | `/drawings` | 图纸列表 |
| `GET` | `/ocr/{id}` | OCR JSON |
| `GET` | `/view-ocr/{id}` | OCR 页面 |
| `GET` | `/export-ocr/{id}` | 导出 OCR 文本 |
| `POST` | `/chat-with-drawing` | MechVL 图纸问答 |
| `GET` | `/mechvl/health` | 由主程序检查 MechVL |
| `GET` | `/local-model/status` | 本地调度状态和双计时 |
| `POST` | `/local-model/switch/{mode}` | 手动切换本地 GPU 模式 |

## 数据与隐私

- `database.db` 保存图纸元数据和 OCR 文本。
- `uploads/` 保存上传的原始图纸。
- `.env` 保存 API Key。
- 浏览器聊天记录只存于当前标签页的 `sessionStorage`，关闭标签页后清除。
- 选择 Qwen OCR 时图纸页面会发送到 DashScope；选择 MinerU 时文件不离开本机。
- `mineru_results/` 保存 MinerU 原始结构化输出。
- MechVL 问答在本机 WSL2 中执行，不调用外部聊天模型。

上述本地文件均已被 Git 忽略。备份或迁移时应同时复制 `database.db` 与 `uploads/`。

## 常见问题

### `wsl` 不可用或没有 Ubuntu

以管理员身份执行 `wsl --install -d Ubuntu` 并重启。确认 `wsl --list --verbose` 中发行版名称为 `Ubuntu`；启动脚本当前使用这个名称。

### WSL2 中 `nvidia-smi` 失败

更新 Windows NVIDIA 驱动，然后执行：

```powershell
wsl --update
wsl --shutdown
```

重新打开 Ubuntu 后再运行 `nvidia-smi`。不要在 WSL2 中安装 Linux 内核显卡驱动。

### MechVL 报 CUDA out of memory

关闭其他占用显存的应用，执行 `wsl --shutdown` 后重启。可在 WSL2 中用 `watch -n 1 nvidia-smi` 查看显存。当前服务已使用 4-bit 量化；低于 8 GB 显存不属于支持配置。

### `/health` 打不开或主程序无法连接 MechVL

确认终端 A 仍在运行，并等待模型加载完成：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8100/health
```

SolidCog 访问 MechVL 时会忽略系统代理。若浏览器仍走代理，请为 `127.0.0.1` 和 `localhost` 设置例外。

### MechVL 分析超时

默认超时 600 秒。先确认 GPU 没有被其他程序占满，再尝试分辨率更低的图纸。需要延长时修改 `.env` 中的 `MECHVL_TIMEOUT_SECONDS` 并重启 SolidCog。

### OCR 提示 Key 无效、无额度或请求频繁

检查 Key、地域地址和 DashScope 控制台模型权限。若启用了“仅使用免费额度”，额度耗尽后需要关闭该限制或充值。修改 `.env` 后重启 SolidCog。

### 搜索不到已有图纸

搜索基于文件名和数据库中已有的 OCR 文本。早期导入但 OCR 字段为空的记录无法按内容搜索，需要重新上传并完成 OCR。

### 端口被占用

```powershell
Get-NetTCPConnection -LocalPort 8000,8090,8100,8200 -ErrorAction SilentlyContinue
```

停止占用进程，或同步修改启动脚本与 `.env` 中对应端口。

## 论文引用

| 主题 | 论文 | 链接 |
| --- | --- | --- |
| MinerU 流水线 | *MinerU: An Open-Source Solution for Precise Document Content Extraction* | [arXiv:2409.18839](https://arxiv.org/abs/2409.18839) |
| MinerU2.5 架构 | *MinerU2.5: A Decoupled Vision-Language Model for Efficient High-Resolution Document Parsing* | [arXiv:2509.22186](https://arxiv.org/abs/2509.22186) |
| 当前 Pro 模型 | *MinerU2.5-Pro: Pushing the Limits of Data-Centric Document Parsing at Scale* | [arXiv:2604.04771](https://arxiv.org/abs/2604.04771) |
| 文档解析评测 | *OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations* | [arXiv:2412.07626](https://arxiv.org/abs/2412.07626) |
| 机械图纸理解 | *MechVQA: Benchmarking and Enhancing Multimodal LLMs on Comprehensive Mechanical Drawing Understanding* | [arXiv:2605.30794](https://arxiv.org/abs/2605.30794) |

BibTeX 使用 `and others` 压缩超长作者列表；正式投稿时可从对应 arXiv 页面导出完整作者元数据。

```bibtex
@article{wang2024mineru,
  title   = {MinerU: An Open-Source Solution for Precise Document Content Extraction},
  author  = {Wang, Bin and others},
  journal = {arXiv preprint arXiv:2409.18839},
  year    = {2024}
}

@article{niu2025mineru25,
  title   = {MinerU2.5: A Decoupled Vision-Language Model for Efficient High-Resolution Document Parsing},
  author  = {Niu, Junbo and others},
  journal = {arXiv preprint arXiv:2509.22186},
  year    = {2025}
}

@article{wang2026mineru25pro,
  title   = {MinerU2.5-Pro: Pushing the Limits of Data-Centric Document Parsing at Scale},
  author  = {Wang, Bin and others},
  journal = {arXiv preprint arXiv:2604.04771},
  year    = {2026}
}

@article{ouyang2024omnidocbench,
  title   = {OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations},
  author  = {Ouyang, Linke and others},
  journal = {arXiv preprint arXiv:2412.07626},
  year    = {2024}
}

@article{kou2026mechvqa,
  title   = {MechVQA: Benchmarking and Enhancing Multimodal LLMs on Comprehensive Mechanical Drawing Understanding},
  author  = {Kou, Qian and Shi, Xiaofeng and Li, Yulin and Qiu, Xiaosong and Wang, Xinyang and Zhou, Hua and Cao, Dongxing},
  journal = {arXiv preprint arXiv:2605.30794},
  year    = {2026}
}
```

## 许可证

SolidCog 自有代码采用 [Apache License 2.0](LICENSE) 发布，版权归
`liu-Daniel7` 所有。第三方组件的归属信息见 [NOTICE](NOTICE)。

SolidCog 使用 [MechVL-4B-RL](https://huggingface.co/XiaofengAlg/MechVL-4B-RL)
提供机械图纸问答能力。该模型由其原作者发布，并采用 Apache-2.0；本仓库不包含、
不拥有且不重新授权其模型权重，安装脚本仅从原始 Hugging Face 仓库下载模型。

使用 MechVL-4B-RL 时请引用其论文：*MechVQA: Benchmarking and Enhancing
Multimodal LLMs on Comprehensive Mechanical Drawing Understanding*（2026）。
DashScope API、Qwen 模型和其他第三方依赖仍分别遵循其自身许可证与服务条款。
