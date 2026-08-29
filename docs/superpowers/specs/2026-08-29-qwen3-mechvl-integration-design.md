# Qwen3-VL 与 MechVL 集成设计

## 目标

将 SolidCog 的模型链路调整为：

```text
上传 PDF/PNG -> DashScope qwen3-vl-plus OCR -> SQLite 入库
选择图纸问答 -> WSL2 MechVL-4B-RL 服务 -> 机械问答与审图
```

删除 DeepSeek 的配置、后端调用和前端入口。保留现有图纸、数据库和页面工作流。

## 架构

- `app/services/qwen.py`：调用 DashScope OpenAI-compatible API，负责 Qwen3-VL OCR。
- `app/services/mechvl.py`：调用 WSL2 中的 MechVL HTTP 服务。
- `app/services/ai.py`：保留模型无关的文本清理和工具编排。
- `mechvl_server/`：独立 WSL2 推理服务，提供 `GET /health` 和 `POST /analyze`。
- SolidCog 继续在 Windows 运行；MechVL 服务监听 `127.0.0.1:8100`，按需手动启动。

不在 SolidCog 启动时加载模型，不把模型权重提交到 Git，也不增加模型工厂或插件框架。

## Qwen3-VL OCR

- `OCR_BACKEND=qwen_vl` 表示云端千问后端；模型由 `QWEN_VL_MODEL=qwen3-vl-plus` 单独配置。
- API Key 只从 `.env` 的 `QWEN_API_KEY` 读取。
- PDF 按页渲染并逐页识别，不再只处理第一页。
- 默认最多识别前 10 页，由 `QWEN_OCR_MAX_PAGES` 配置。
- 每页结果整理后合并为现有 `title_block`、`tech_block`、`all_text`、`layout` 结构，数据库无需迁移。
- 临时图片使用随机临时文件并在请求结束后清理。

## MechVL 问答

- 前端智能助手只保留“MechVL 本地”模型。
- 选择图纸后，SolidCog 将用户问题、已入库 OCR 和图纸预览发送到 `MECHVL_BASE_URL`。
- PDF 默认渲染第一页作为视觉输入；图片限制长边，避免 8 GB 显存因高分辨率输入溢出。
- WSL2 服务加载 `XiaofengAlg/MechVL-4B-RL` 的 4-bit 版本，单请求串行推理。
- 服务提供健康检查，未启动、超时或显存不足时返回明确错误，不自动降级到云端模型。
- 无图纸的普通聊天不再调用外部模型，前端要求先选择图纸。

## WSL2 部署

项目提供：

- WSL2 环境依赖安装脚本。
- 模型下载说明或脚本。
- MechVL 服务启动脚本。
- 健康检查命令。

模型下载必须由用户手动执行。推理服务不随 SolidCog 自动启动，避免日常使用时持续占用约 5-7 GB 显存。

## 配置

`.env.example` 保留：

```env
QWEN_API_KEY=replace-with-your-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_VL_MODEL=qwen3-vl-plus
QWEN_OCR_MAX_PAGES=10
OCR_BACKEND=qwen_vl
MECHVL_BASE_URL=http://127.0.0.1:8100
MECHVL_TIMEOUT_SECONDS=180
```

删除 `DEEPSEEK_API_KEY` 及全部 DeepSeek 分支。

## 错误处理

- DashScope 配置缺失时返回明确配置错误，不显示 Key。
- 单页 OCR 失败时记录页码；没有任何成功页时上传失败且不写入空记录。
- MechVL 连接失败、超时和服务错误分别映射为可读的 HTTP 错误。
- 上传失败继续删除本次新保存、尚未入库的文件。
- WSL2 服务使用单请求锁；并发请求返回忙碌状态或排队，不并行占用显存。

## 测试与验收

- 使用标准库测试替换 DashScope 和 MechVL HTTP 调用，不真实下载模型或调用付费 API。
- 验证 Qwen3-VL 模型名、分页上限、逐页合并和失败清理。
- 验证 MechVL 请求结构、健康检查、超时和不可用错误。
- 验证前端不再显示 DeepSeek，代码中不存在 DeepSeek 配置或 URL。
- 验证现有 36 条数据库记录和上传文件不变。
- 手动配置 Key 后，用一张测试图纸验证真实 Qwen3-VL OCR。
- 手动启动 WSL2 服务后，用一张图纸验证 MechVL 问答。

## 非目标

- 自动下载数 GB 模型权重
- 自动启动或关闭 WSL2 服务
- MechVL 替代 OCR
- Qwen3-VL 本地部署
- 多用户并发推理
- 修改数据库结构或重新识别现有图纸
