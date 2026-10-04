# macOS MinerU 版本核对与升级（2026-10-04）

## 本机使用的组件

SolidCog 的 macOS OCR 服务直接使用 `MinerUClient` 和 llama.cpp/Metal，
没有通过完整 `mineru` CLI 或官方 HTTP 服务调用解析。因此三个版本号
分别代表模型权重、模型客户端和完整文档解析产品，不能相互替代。

| 组件 | 更新前 | 官方最新稳定版 | 本次处理 |
| --- | --- | --- | --- |
| 实际 OCR 客户端 `mineru-vl-utils` | 1.2.1 | 2.0.5（2026-09-19） | 本机升级并锁定 2.0.5 |
| Metal 推理引擎 `mineru-llama-cpp` | 0.1.2 | 0.1.2（2026-09-14） | 已是最新版，锁定版本 |
| 默认视觉模型 | 配置为 MinerU2.5-Pro-2605-1.2B | 官方 4.0.10 默认仍为该模型 | 使用现有权重 |
| 完整 `mineru` 主程序 | 未安装、未被应用调用 | 4.0.10（2026-09-29） | 不安装应用未调用的额外管线 |

## 差异与兼容性

`mineru-vl-utils` 2.x 改进了不同推理后端的模型加载、批量推理、进度和
取消处理。对本应用使用的 llama.cpp 后端，新版批量进度按完成请求更新，
并保持输出和输入的对应顺序。现用 `two_step_extract`、
`extract_with_layout`、归一化 bbox 和 `ExtractResult` 接口兼容；
默认识别提示词及核心两阶段抽取保持一致。升级本身不代表识别模型
经过重新训练，也不能据此声称机械图纸准确率提升。

完整 MinerU 4.x 增加独立小模型配置、文档库、四个解析质量档位、
多种原生文档类型和新的 V1 作业 API。它的调用方式、配置和输出协议
有较大变化，官方不再提供旧 `/file_parse`。本应用的该端点属于
自有 macOS 包装服务，继续使用最新模型客户端即可保留已验证的图纸
高分辨率分块方案。

本机 `models/mineru/model.safetensors` 的 SHA-256 与官方
`opendatalab/MinerU2.5-Pro-2605-1.2B` 仓库文件一致，核对 revision 为
`bff20d4ae2bf202df9f45284b4d43681555a97ed`。这验证原始权重来源，
不等同于对自行转换的每个 GGUF 文件做了官方字节一致性验证。

## 验证

- 更新实际 `.venv`，检查安装元数据；`pip check` 无依赖冲突。
- 自动化回归：`.venv/bin/python -m unittest discover -s tests -q`，47 项通过。
- 用新安装的运行库重新加载本地 GGUF/Metal 并识别真实 A3 机械图纸；
  核对标题栏图号、材料、比例、技术要求末条，以及细小尺寸。
- 真实运行返回 1 页、0 个区域失败；7 项核对全部通过，包含
  `634.9`、`R2460`、`597.5`、`R2421` 和 `0.7`。一次完整运行（含加载）
  用时 237.03 秒，仅用于确认兼容性，不作为新旧版本速度或准确率基准。
  本地运行记录位于 `tmp/mineru-version-audit/smoke-summary.json`。
- 健康接口、解析响应和原始结果保留 `runtime_versions`，便于区分
  模型名称与当前运行库版本。

## 官方核对来源

- [mineru-vl-utils 2.0.5（PyPI）](https://pypi.org/project/mineru-vl-utils/2.0.5/)
- [mineru-llama-cpp 0.1.2（PyPI）](https://pypi.org/project/mineru-llama-cpp/0.1.2/)
- [mineru 4.0.10（PyPI）](https://pypi.org/project/mineru/4.0.10/)
- [MinerU 4.0 迁移指南](https://opendatalab.github.io/MinerU/reference/migration_4/)
- [官方 4.0.10 发布](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.10-released)
- [官方 4.0.10 模型注册表](https://github.com/opendatalab/MinerU/blob/mineru-4.0.10-released/mineru/model/registry.py)
- [MinerU2.5-Pro-2605-1.2B 模型](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B)
