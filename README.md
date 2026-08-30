# SolidCog

SolidCog 是面向机械工程图纸的本地管理与 AI 审核工作台。系统使用阿里云 DashScope 的 `qwen3-vl-plus` 提取 PDF/PNG 图纸文字，并使用运行在 WSL2 中的 `MechVL-4B-RL` 回答图纸问题。图纸原文件、OCR 结果和聊天上下文均保留在本机；只有 OCR 图片会发送给 DashScope API。

## 功能

- 批量上传 PDF、PNG 图纸，单文件最大 50 MB
- 使用 Qwen3-VL-Plus 分页提取标题栏、技术要求和完整文字
- 按文件名和 OCR 内容检索图纸
- 查看、导出 OCR 结果，管理本地图纸记录
- 使用本地 MechVL-4B-RL 进行图纸问答和审核
- 在当前浏览器标签页中保留聊天记录，并支持手动清理

### MechVL 的功能与能力

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

## 系统架构

```text
浏览器（http://127.0.0.1:8000/home）
  |
  +-- SolidCog / FastAPI（Windows，端口 8000）
       +-- SQLite：图纸元数据和 OCR 结果
       +-- uploads/：原始图纸文件
       +-- DashScope qwen3-vl-plus：图纸 OCR
       +-- MechVL 服务（WSL2，端口 8100）：本地图纸问答
```

主程序与 MechVL 是两个独立服务，必须分别启动。Qwen3-VL-Plus 是云端 API，不需要下载；MechVL-4B-RL 会下载到 WSL2 的 Hugging Face 缓存。

## 运行要求

### 必需条件

- Windows 10 22H2 或 Windows 11
- 支持 WSL2 的 x64 处理器
- Python 3.12 x64、Git
- NVIDIA 独立显卡及支持 WSL2 CUDA 的最新 Windows 驱动
- 阿里云 DashScope API Key，并已开通 `qwen3-vl-plus`
- 能访问 PyPI、PyTorch 和 Hugging Face

### 硬件建议

| 项目 | 最低建议 | 推荐 |
| --- | --- | --- |
| NVIDIA 显存 | 8 GB，4-bit 推理 | 12 GB 或更高 |
| 系统内存 | 16 GB | 32 GB |
| 可用磁盘 | 25 GB | 40 GB |

RTX 4060 Laptop 8 GB 已在本项目的 4-bit NF4 配置下验证。8 GB 显存余量较小，推理期间不要同时运行其他占用 GPU 的模型或程序。没有 NVIDIA GPU 时，OCR 和图纸管理仍可运行，但当前 MechVL 服务不能启动。

## 一、安装基础软件

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

## 二、克隆仓库并安装主程序

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

## 三、配置 Qwen3-VL-Plus

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
```

不要把 `.env` 提交到 Git。中国内地 DashScope 使用上述北京地域地址；若 Key 属于其他地域，请按 DashScope 控制台提供的兼容模式地址修改 `QWEN_BASE_URL`。

## 四、安装 MechVL 服务

仍在仓库根目录的 PowerShell 中执行：

```powershell
wsl.exe -d Ubuntu -- bash -lc "sudo apt-get update && sudo apt-get install -y python3-venv"
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a "$PWD").Trim()
wsl.exe -d Ubuntu -- bash -lc "cd '$repoWsl/mechvl_server' && bash setup_wsl.sh"
wsl.exe -d Ubuntu -- bash -lc "cd '$repoWsl/mechvl_server' && bash download_model.sh"
```

`setup_wsl.sh` 会创建 `mechvl_server/.venv`，安装 CUDA 12.8 版 PyTorch、Transformers 和 4-bit 量化依赖。`download_model.sh` 会从 Hugging Face 下载 `XiaofengAlg/MechVL-4B-RL`。安装和下载可能持续数十分钟。

下载完成后检查模型缓存：

```powershell
wsl.exe -d Ubuntu -- bash -lc "du -sh ~/.cache/huggingface/hub/models--XiaofengAlg--MechVL-4B-RL"
```

若下载中断，重新执行 `download_model.sh` 即可续传。

## 五、启动两个服务

### 终端 A：启动 MechVL

在仓库根目录打开 PowerShell：

```powershell
.\start_mechvl_wsl.bat
```

首次启动需要把模型载入显存。等待 Uvicorn 启动日志后，在另一个 PowerShell 中检查：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8100/health
```

成功响应示例：

```json
{"status":"ready","model":"XiaofengAlg/MechVL-4B-RL","cuda":"NVIDIA GeForce RTX 4060 Laptop GPU","busy":false}
```

### 终端 B：启动 SolidCog

在仓库根目录再打开一个 PowerShell：

```powershell
.\start_server.bat
```

检查主服务：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8000/
```

然后打开：

- 工作台：<http://127.0.0.1:8000/home>
- API 文档：<http://127.0.0.1:8000/docs>

两个终端都需要保持打开。按 `Ctrl+C` 可停止对应服务。

## 六、验证完整流程

1. 打开工作台，上传一张清晰的 PDF 或 PNG 机械图纸。
2. 等待 Qwen3-VL-Plus OCR 完成。
3. 点击“查看 OCR”，确认标题栏、技术要求或完整文字已有内容。
4. 返回工作台，输入刚识别出的文字，确认能检索到该图纸。
5. 在“本地模型审核问答”中选择这张图纸并提出相关问题。
6. 等待 MechVL 返回回答；8 GB 显存设备单次推理可能需要数分钟。

完成以上六步即表示 DashScope OCR、SQLite、文件存储、全文检索和 MechVL 本地问答链路均正常。

## 日常启动

安装只需执行一次。以后在仓库根目录分别打开两个 PowerShell：

```powershell
.\start_mechvl_wsl.bat
```

```powershell
.\start_server.bat
```

先等待 MechVL `/health` 返回 `ready`，再使用图纸问答。仅使用上传、OCR、搜索功能时，可以不启动 MechVL。

## 测试

测试使用临时数据库和上传目录，不会修改正式数据：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 数据与隐私

- `database.db` 保存图纸元数据和 OCR 文本。
- `uploads/` 保存上传的原始图纸。
- `.env` 保存 API Key。
- 浏览器聊天记录只存于当前标签页的 `sessionStorage`，关闭标签页后清除。
- OCR 时，图纸页面会发送到 DashScope `qwen3-vl-plus`。
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
Get-NetTCPConnection -LocalPort 8000,8100 -ErrorAction SilentlyContinue
```

停止占用进程，或同步修改启动脚本与 `.env` 中对应端口。

## 项目结构

```text
solidcog/
├─ main.py                  # 三行 FastAPI 入口
├─ app/
│  ├─ application.py       # 应用创建与路由注册
│  ├─ config.py            # 环境变量和路径
│  ├─ database.py          # SQLite 初始化
│  ├─ repositories/        # 数据访问
│  ├─ routers/             # 页面、图纸和 AI HTTP 接口
│  └─ services/            # OCR、文件、Qwen、MechVL 业务逻辑
├─ mechvl_server/           # WSL2 本地模型服务
├─ templates/               # HTML/CSS/JavaScript 页面
├─ tests/                   # 标准库 unittest 测试
├─ requirements.txt        # Windows 主服务依赖
├─ start_server.bat        # SolidCog 启动入口
└─ start_mechvl_wsl.bat    # MechVL WSL2 启动入口
```

## 主要接口

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

## 许可证

SolidCog 自有代码采用 [Apache License 2.0](LICENSE) 发布，版权归
`liu-Daniel7` 所有。第三方组件的归属信息见 [NOTICE](NOTICE)。

SolidCog 使用 [MechVL-4B-RL](https://huggingface.co/XiaofengAlg/MechVL-4B-RL)
提供机械图纸问答能力。该模型由其原作者发布，并采用 Apache-2.0；本仓库不包含、
不拥有且不重新授权其模型权重，安装脚本仅从原始 Hugging Face 仓库下载模型。

使用 MechVL-4B-RL 时请引用其论文：*MechVQA: Benchmarking and Enhancing
Multimodal LLMs on Comprehensive Mechanical Drawing Understanding*（2026）。
DashScope API、Qwen 模型和其他第三方依赖仍分别遵循其自身许可证与服务条款。
