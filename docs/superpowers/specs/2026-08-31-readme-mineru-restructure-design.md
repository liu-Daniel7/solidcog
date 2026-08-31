# SolidCog README 展示型重构设计

## 目标

完整重构 SolidCog 根目录 README，使其优先服务挑战杯评委、导师、合作方和项目展示场景，同时保留开发者完成安装、运行、测试和二次开发所需的信息。

本次重构参考 `nature-skills` README 的信息组织方式，包括顶部标识、快捷导航、明确目录、能力索引、快速开始和分层文档，但不复制其作者运营、社区推广或与 SolidCog 无关的内容。

## 读者与阅读路径

第一阅读路径面向展示型读者：

1. 在首屏理解 SolidCog 解决的问题。
2. 快速看到核心能力与技术创新。
3. 理解 MinerU、MechVL 和模型调度器的分工。
4. 查看论文依据与本机性能证据。

第二阅读路径面向开发者：

1. 快速启动已有环境。
2. 完成 Windows、WSL2、模型和调度器安装。
3. 理解服务端口、接口和项目结构。
4. 执行测试并处理常见故障。

## README 信息架构

README 按以下顺序组织：

1. 项目标题、定位、徽章和快捷导航。
2. 项目背景与待解决问题。
3. 核心能力总览。
4. 技术创新。
5. MinerU 文档解析能力。
6. MechVL 机械图纸理解能力。
7. 性能与证据。
8. 系统架构和典型工作流。
9. 快速开始。
10. 完整安装与配置。
11. 模型调度器接入方式。
12. API、项目结构和测试。
13. 数据与隐私。
14. 常见问题。
15. 论文引用、第三方归属和许可证。

README 顶部使用文本标题和 Shields.io 徽章，不在本次工作中新增 Banner 图片。快捷导航直接链接到核心能力、技术创新、系统架构、快速开始、完整安装和论文引用。

## MinerU 能力说明

MinerU 章节以四类一手来源为依据：

- `MinerU: An Open-Source Solution for Precise Document Content Extraction`，用于说明原始流水线系统的布局检测、分区 OCR、公式、表格、阅读顺序与后处理。
- `MinerU2.5: A Decoupled Vision-Language Model for Efficient High-Resolution Document Parsing`，用于说明低分辨率全局布局分析与高分辨率局部识别组成的粗到细机制。
- `MinerU2.5-Pro: Pushing the Limits of Data-Centric Document Parsing at Scale`，用于说明当前部署的 1.2B Pro 模型通过数据工程和训练策略提升解析能力，而非扩大模型架构。
- `OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations`，用于说明文档解析评测涵盖的文本、公式、表格和阅读顺序维度。

能力描述覆盖：

- 全局布局与区域定位。
- 高分辨率文字和细小内容识别。
- 标题、正文、列表和多栏阅读顺序恢复。
- 公式识别与结构化表达。
- 表格结构识别与 HTML/Markdown 输出。
- 图片和其他页面元素的结构化保留。
- Markdown、content list 和中间布局结果输出。

明确以下边界：MinerU 是通用文档解析模型，不具备 MechVL 的机械制图推理和规范判定能力；OCR 结果仍受图像清晰度、字体、扫描质量、复杂标注和领域符号影响。

## 证据分层

所有性能和能力结论按来源分层：

| 证据类型 | 用途 | 表述要求 |
| --- | --- | --- |
| 论文方法 | 解释模型设计和能力范围 | 标明论文与链接 |
| 论文基准 | 展示作者报告的公开评测结果 | 使用“论文报告”，不称为项目准确率 |
| SolidCog 实测 | 展示当前硬件上的启动、耗时和显存 | 标明硬件、样本与环境，不外推到其他设备 |

MinerU2.5 在 olmOCR-bench 上的论文结果和 MechVL 在 MechVQA 上的论文结果放入“论文报告”区。RTX 4060 Laptop 8 GB 上的 MinerU 启动、单页解析、峰值显存和 MechVL 加载数据放入“本机实测”区。

## 技术创新表达

技术创新章节聚焦 SolidCog 已实现的工程能力：

- MinerU 负责结构化 OCR，MechVL 负责机械图纸理解和审核，职责清晰分离。
- 单卡环境中使用本地模型调度器保证两个 GPU 模型不同时驻留。
- 当前模型保持驻留，避免每个请求重复加载。
- 切换过程暴露阶段、已用时间和基于历史样本的预计剩余时间。
- OCR 可在 MinerU 本地模式和 Qwen3-VL 云端模式之间按上传批次选择。
- 模型执行任务时拒绝切换，避免中途终止 OCR 或问答。

不得把模型组合、端口代理或计时器表述成算法层面的原创研究成果。

## 内容保留与精简

现有 README 中以下内容完整保留并重新分组：

- Windows、WSL2、CUDA 和硬件要求。
- Qwen、MinerU、MechVL 与调度器的安装命令。
- 启动、验证和日常使用步骤。
- 模型调度器接口与处理流程。
- 数据、隐私、测试、故障排查、项目结构和主要接口。
- MechVL 论文能力介绍与第三方模型许可说明。

重复的启动说明、模型切换描述和端口说明合并为单一权威段落。长安装命令保留，不隐藏在外部文档中，以确保 README 可独立完成部署。

## 引用与许可证

论文部分提供 MinerU、MinerU2.5、MinerU2.5-Pro、OmniDocBench 和 MechVQA 的标题、arXiv 链接与 BibTeX。BibTeX 字段以论文官方元数据为准，不虚构期刊、会议或 DOI。

许可证部分继续说明：SolidCog 自有代码采用 Apache License 2.0；MinerU、MechVL、Qwen、DashScope 和其他第三方组件分别受其自身许可证或服务条款约束；仓库不重新授权第三方模型权重。

## 验收标准

- README 具有可用的目录和顶部快捷导航。
- 展示型读者能在安装章节之前理解问题、能力、创新、模型依据和实测结果。
- MinerU 能力介绍能够追溯到一手论文。
- 论文基准与 SolidCog 本机实测明确分开。
- 原有安装、启动、API、排错和许可证信息没有实质遗漏。
- 所有标题锚点、内部链接和外部论文链接有效。
- Markdown 表格、代码块和列表结构完整。
- `git diff --check` 通过。

