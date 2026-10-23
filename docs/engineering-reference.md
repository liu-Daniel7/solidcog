# 工程设计与历史验证记录

[返回 README](../README.md)

本文保留 README 整理前的工程设计、论文依据与历史实测。所有“当前”“本轮”等表述均指原记录对应的版本，不代表最新版复测结果；部署入口和运行限制以 [README](../README.md) 为准。2026-10-23 的 macOS 依赖已锁定 `mineru-llama-cpp==0.1.5`；下文 `0.1.2` 仅为历史测试环境。WSL 的 128-token 参数不适用于 macOS（其服务配置为 2048）。

## 项目要解决的问题

机械工程图纸不是普通的连续文本。标题栏、技术要求、多视图投影、尺寸与公差、表面粗糙度、公式和表格同时存在，文字往往尺寸小、分布稀疏，并依赖所在区域和视图才能正确解释。单一 OCR 模型可以读取文字，却不一定理解视图、装配和规范；单一机械视觉语言模型可以回答工程问题，但不适合承担批量结构化抽取与全文检索。

SolidCog 因此将任务拆分为三个层次：

1. MinerU 或 Qwen3-VL 将 PDF 或图片转换为可检索的结构化内容。
2. SQLite 与本地文件系统管理图纸、OCR 结果和检索索引。
3. MechVL 结合图像与 OCR 上下文执行机械图纸问答和辅助审核。

## 核心工程贡献

MinerU 与 MechVL 提供上游模型能力；以下部分是 SolidCog 在模型之上完成的工程设计与实现。

### OCR 分区与统一数据契约

普通整页 OCR 只能返回一段文本，难以支持标题栏展示和技术要求检索。SolidCog 将结果统一为 `title_block`、`tech_block`、`all_text` 和 `layout` 四个分区：Qwen3-VL 通过固定 JSON 提示词输出，MinerU 的 Markdown、表格和 content list 则由适配器映射到相同结构。因此两个 OCR 后端可以复用数据库、全文检索、查看和导出链路，无需为模型分别维护业务代码。

### OCR 可靠性优化

- 支持 PDF、PNG、JPEG、TIFF（多页）、BMP 和 WebP；透明图片以白底合成，读取 EXIF 方向。
- Qwen 使用整页概览、高分辨率重叠分块和重点区域补读，分块覆盖到图纸的右侧和底部边缘。全局文字保留来源，避免误删不同位置的相同尺寸。
- macOS MinerU 默认以 300 DPI 渲染 PDF，使用重叠分块，并将布局中的图像区域再次按文字抽取，补读视图内尺寸和公差。可用 `MINERU_PDF_DPI`、`MINERU_OCR_TILE_SIZE`（默认 2200）调整分辨率和局部大小。
- Qwen 页数限制由 `QWEN_OCR_MAX_PAGES` 控制；超过限制或区域失败会返回提示，并保存在全局 OCR 中，避免将不完整结果当作完整结果。
- 单页失败时继续处理其他页面；全部失败时不写入空的“成功”记录。
- 清理 Markdown 代码围栏并兼容模型返回的非标准 JSON。
- MinerU 结合结构化文字、字段线索和坐标划分标题栏与技术要求，兼容带冒号、非 Markdown 标题及多页结果；缺少 Markdown 时从原始块恢复全文。
- 保存 MinerU 原始结构化结果，使适配错误可以回溯，而不是只保留最终纯文本。

分块增加识别调用和耗时，Qwen 云端调用也会增加费用。这些措施改善输入细节和结果完整性，并不保证模糊扫描、遮挡字符或任意图纸百分之百准确；仍需以有人工标注的机械图纸核对字符、尺寸、公差和分区准确率。DWG/DXF 等 CAD 文件需先导出 PDF 或栅格图像。

### MechVL 提示词与 OCR 上下文引导

SolidCog 不只把图片交给 MechVL，而是组合图纸预览、标题栏、技术要求、全局 OCR 与用户问题。服务端提示词要求模型严格依据图纸，证据不足时明确说明，并禁止编造尺寸、材料、公差或标准条款。OCR 上下文还能补偿预览图缩放后丢失的细小标注，答案清理层则移除影响工作台阅读的 Markdown 分隔符。

### MechVL 推理优化

- 将输入图纸最长边由 1536 px 限制为 1024 px，减少视觉 token 和显存计算量，同时保留 OCR 文字作为细小标注的上下文补偿。
- 将单次回答上限由 256 tokens 调整为 128 tokens，适配工程图纸检索和简短审核问答。
- 优先使用 FlashAttention 2，并在环境不兼容或初始化失败时自动回退到 PyTorch SDPA，兼顾注意力后端迭代与部署稳定性。
- 模型切换到 MechVL 后保持常驻，连续问答不再重复加载 4B 权重。

### 单卡调度与故障隔离

本地调度器让 MinerU 与 MechVL 在 8 GB 显存上互斥驻留，任务忙碌时拒绝切换，启动失败、超时或异常退出时回收目标进程。状态接口和转换器展示阶段、已用时间与预计剩余时间。Qwen 云端 OCR、图纸管理和检索不依赖本地 GPU 服务，即使调度器不可用仍可继续使用。

### 模块化重构

原应用的路由、数据库、OCR 和模型调用集中在单个 `main.py`。当前版本将其拆分为配置、路由、服务、仓储和独立模型服务；HTTP 层、业务逻辑、数据访问和模型适配可以分别修改与测试。替换 OCR 后端时无需同步改动主要路由和数据库层，故障也更容易定位到具体模块。

### 重构前后实测对比

以下对比记录模块化重构时的实测结果：基线为提交 `5cb4bc0` 的父版本，“重构后”指当次重构版本；测试使用同一台机器和同一 Python 环境。应用性能不包含 OCR 或 MechVL 推理，测试数量也不代表后续版本的当前总数。

| 指标 | 重构前 | 重构后（当时） | 结果 |
| --- | ---: | ---: | --- |
| 最大应用 Python 文件 | 2000 行 | 110 行 | 减少 **94.5%** |
| 业务模块 | 2 个 | 23 个 | 职责拆分为配置、路由、服务、仓储与模型服务 |
| 应用导入中位数（15 次） | 650.8 ms | 650.4 ms | 基本持平 |
| 应用导入 P95（15 次） | 772.4 ms | 670.3 ms | 本次样本降低 **13.2%** |
| `/` 响应中位数（500 次） | 1.778 ms | 1.792 ms | 慢 0.8%，属毫秒级波动 |
| `/home` 响应中位数（500 次） | 2.551 ms | 2.605 ms | 慢 2.1%，属毫秒级波动 |
| 自动化测试 | 0 项 | 19 项 | 覆盖路由、OCR、模型服务、调度和数据删除 |
| 自动验证的故障/安全场景 | 0 项 | 至少 7 类 | 包括空 OCR、额度错误、代理污染、服务不可用、输入限幅、模型互斥和忙碌拒绝切换 |

模块化重构没有显著改变普通路由速度，其主要收益是将最大文件规模降低 94.5%，并建立可独立测试、可替换和可隔离故障的代码边界。“稳定性提升”以 19 项测试和故障保护场景表示，不使用缺少长期运行数据支撑的百分比。

### 贡献对应的代码边界

| 工程工作 | 主要代码 | 可独立维护的边界 |
| --- | --- | --- |
| OCR 后端选择 | `app/services/ocr.py` | 统一分发 MinerU 与 Qwen，不影响路由和数据库 |
| Qwen 分区提示词与解析 | `app/services/qwen.py` | JSON 契约、代码围栏清理和额度错误转换 |
| MinerU 结果适配 | `app/services/mineru.py` | Markdown、标题栏表格、技术要求和原始结果保存 |
| MechVL 上下文编排 | `app/services/ai.py`、`mechvl_server/server.py` | OCR 上下文、图像预览、问题与防编造提示词 |
| 本地模型代理 | `app/services/model_scheduler.py` | 主应用不直接管理 GPU 进程 |
| 模型生命周期 | `model_scheduler/scheduler.py` | 互斥、忙碌锁、超时、进程组回收和历史计时 |
| 数据访问 | `app/repositories/drawings.py` | SQLite 查询与 HTTP/模型逻辑分离 |

OCR 数据流保持一个稳定的输出契约：

```text
PDF / PNG / JPEG / TIFF / BMP / WebP
  +-- Qwen3-VL -> 分区 JSON ---------+
  +-- MinerU -> Markdown/content list +-> 统一 OCR 结构
                                           +-- SQLite 入库
                                           +-- 全文检索
                                           +-- 查看与导出
                                           +-- MechVL 上下文
```

这种边界带来的维护性收益包括：

- 新增 OCR 后端只需实现统一结果结构，不需要复制上传、数据库和页面逻辑。
- 修改 MechVL 提示词不会改变图纸存储、检索或 OCR 代码。
- 调度器可以独立启动和测试，GPU 生命周期问题不会散落在 FastAPI 路由中。
- 仓储层可以使用临时数据库测试，避免测试过程修改正式图纸数据。
- 失败会停留在对应服务边界，并转换为可读错误，不再由一个大函数吞并所有异常。

基准测试每个版本均先预热，再重复执行；导入时间使用 15 个独立进程，路由延迟各采样 500 次。P95 改善仅代表本次样本，普通路由中位数的 0.8%–2.1% 差异不作为性能提升宣传。

结构统计仅包含 Git 管理的业务 Python 文件，不包含虚拟环境、生成文件或第三方模型代码。

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

Windows 历史基准环境为 Windows + WSL2、RTX 4060 Laptop 8 GB，MechVL 使用 NF4 4-bit 量化，MinerU 与 MechVL 通过调度器分时运行。测试固定使用单页工程图 `GAC-OP530C-l-2-1.pdf`，MechVL 固定问题为“这张图纸是什么零件？请用一句话简短回答。”，两次回答均正确识别为“气源三联件安装板（OP530B）”。

#### 端到端阶段耗时

| 阶段 | 本机实测 | 说明 |
| --- | ---: | --- |
| MinerU 冷启动与权重预载 | 49.1 s | 从调度器空闲到 MinerU 健康检查就绪 |
| MinerU 单页工程图 OCR | 10.63 s | 返回 completed，结构化响应约 31 KB |
| MinerU 切换至 MechVL | 297.4 s | 包含停止 MinerU、释放显存、加载和量化 MechVL |
| MechVL 首次问答 | 40.74 s | 模型刚加载后的第一次真实问答 |
| MechVL 第二次常驻问答 | 28.31 s | 不重新加载权重，答案与首问一致 |
| 完全空闲至完成首次问答 | 397.9 s | 冷启动、OCR、模型切换和首次问答之和 |

模型启动是完整流程的主要等待来源；进入 MechVL 常驻状态后，连续问题约 28 秒完成。模型就绪时 MechVL 占用约 5.84 GB 显存；MinerU 既有测试峰值约 7.24 GB，因此两者不同时驻留。

#### 输入分辨率优化对照

在同一已加载模型、同一图纸和同一问题下，仅改变输入图片最长边：

| MechVL 输入最长边 | 单次推理耗时 | 相对 1536 px |
| --- | ---: | ---: |
| 1536 px | 26.43 s | 基线 |
| 1024 px | 12.84 s | 耗时降低 **51.4%**，约 **2.06 倍加速** |

该对照隔离了模型加载和切换时间，表明降低图纸预览分辨率是当前最明确的单项推理优化。SolidCog 同时向 MechVL 提供标题栏、技术要求和全文 OCR 上下文，用于补偿图片缩放后可能损失的细小文字信息。

#### 整体交互速度变化

优化前的实际交互记录约为 360 s/问题；采用 1024 px 输入上限、128-token 输出上限、模型常驻和优化后的注意力后端后，本轮第二次常驻问答为 28.31 s：

| 对比口径 | 优化前 | 当前实测 | 结果 |
| --- | ---: | ---: | ---: |
| 用户连续问答等待时间 | 约 360 s | 28.31 s | 耗时降低 **92.1%**，约 **12.7 倍加速** |

这里的 12.7 倍是相对早期实际交互记录的整体结果，不能拆分归因给某一个优化项；其中分辨率调整有同条件 A/B 测试支持。FlashAttention 2 已作为当前注意力后端技术迭代启用，并保留 SDPA 自动回退，但未单独声明其性能贡献。

以上为单机单样本实测，不是跨设备性能承诺。页面复杂度、回答长度、模型缓存、磁盘速度、驱动、功耗模式和后台显存占用都会影响结果。该轮模型保护自动化验证共 19 项，覆盖模型互斥、忙碌拒绝切换、输入限幅、服务不可用和回退路径等场景。

#### macOS Apple Silicon 实测（2026-10-06）

测试环境为 macOS 26.5.2、Apple M2 8 核、24 GB 统一内存、Python 3.12.14、llama.cpp 0.5.0 Metal。MinerU 使用 `mineru-vl-utils 2.0.5` / `mineru-llama-cpp 0.1.2`；MechVL 使用本地 Q5 GGUF。完整软件回归通过 **48 项 Python 测试**和 **7 组浏览器视口及交互测试**。

应用性能使用隔离的临时数据库，预热后测量；导入测试为 15 个独立进程，每个只读路由执行 500 次本机 HTTP 请求。

| 指标 | macOS 实测 | 测试口径 |
| --- | ---: | --- |
| `import main` 中位数 / P95 | 226.5 / 232.9 ms | 15 个独立进程 |
| `/` 中位数 / P95 | 0.554 / 0.606 ms | 500 次请求 |
| `/home` 中位数 / P95 | 0.834 / 0.909 ms | 500 次请求 |
| MinerU 冷启动 | 3.58 s | 从调度器空闲至健康检查就绪 |
| MinerU 单页 OCR | 135.81 s | 1 页，返回 9,264 字符，无告警 |
| MinerU 切换至 MechVL | 6.62 s | 包含服务停止和目标服务就绪 |
| MechVL 首次 / 常驻问答 | 37.04 / 2.47 s | 相同问题；两次均识别为“面板8”零件 |
| 完全空闲至首次问答 | 185.83 s | 冷启动、OCR、模型切换和首次问答 |

Mac 模型链路使用本机已有单页图纸 `BJ-SG2-1-09A-12-图纸1.pdf`，问题与 Windows 测试相同。分辨率 A/B 使用同一图纸和问题、已就绪模型及相同提示参数，仅改变输入最长边；该 A/B 未附加 OCR 上下文。

| MechVL 输入最长边 | 单次推理耗时 | 相对 1536 px |
| --- | ---: | ---: |
| 1536 px | 12.03 s | 基线 |
| 1024 px | 5.10 s | 耗时降低 **57.6%**，约 **2.36 倍加速** |

此组 Mac 数据与 Windows 历史模型基准不能直接作速度排名：Windows 使用 `GAC-OP530C-l-2-1.pdf` 和 NF4 量化，Mac 使用不同图纸和 Q5 GGUF；Windows 结果也来自较早的历史测试。应用导入和路由数据同样受代码版本、Python 构建及测量环境影响。跨平台模型性能需要在两台机器上使用相同图纸、模型版本、量化及推理参数复测。

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
