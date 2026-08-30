# SolidCog 本地模型调度与 OCR 后端设计

## 目标

为 SolidCog 接入本地 MinerU2.5 OCR，并提供统一的本地模型调度器，使 MinerU 与 MechVL 在 8 GB 显存设备上互斥运行。用户可以自由切换本地 GPU 模式；选择 MinerU 上传或发起 MechVL 问答时，系统也能自动切换。现有 Qwen3-VL 云端 OCR 保留为可选后端。

## 已确认的产品行为

- OCR 后端按上传批次选择：`MinerU 本地` 或 `Qwen3-VL 云端`。
- 本地 GPU 模式可手动选择：`空闲`、`MinerU OCR`、`MechVL 审核`。
- 需要另一模型的操作会自动切换，无需二次确认。
- 操作完成后当前模型保持驻留，直到下一次切换或用户选择空闲。
- MinerU 与 MechVL 永远不能同时驻留显存。
- 切换时显示已用时间和预计剩余时间。
- 模型正在执行任务时不允许强制切换。

## 架构

采用独立 WSL 模型调度服务。调度器常驻但不占用 GPU，拥有其启动的 MinerU 与 MechVL 子进程，并通过单一互斥锁串行化切换和推理任务。

```text
浏览器
  |
  v
SolidCog / FastAPI (Windows, :8000)
  |
  +-- Qwen3-VL 云端 OCR
  |
  +-- WSL 模型调度器 (:8090)
        +-- MinerU API (:8200)
        +-- MechVL API (:8100)
```

调度器与两个模型使用独立 Python 环境。现有 MechVL 环境保持不变；MinerU 使用已安装的 `~/.local/share/solidcog/mineru/.venv`。

## 调度状态机

调度器公开以下稳定状态：

- `idle`
- `switching`
- `mineru_ready`
- `mechvl_ready`
- `error`

目标模式为 `idle`、`mineru` 或 `mechvl`。状态响应包含：

```json
{
  "state": "switching",
  "current_mode": "mineru",
  "target_mode": "mechvl",
  "stage": "starting_mechvl",
  "started_at": "2026-08-31T01:00:00+08:00",
  "elapsed_seconds": 37,
  "estimated_total_seconds": 52,
  "busy": false,
  "error": null
}
```

切换阶段包括停止旧模型、等待端口关闭、等待显存释放、启动目标模型和等待健康检查。切换由单一锁保护。重复请求同一已就绪模式直接成功；冲突请求返回当前切换状态。

调度器只终止自己创建的独立进程组，不按模糊进程名终止用户程序。启动失败时清理失败子进程并进入 `error`，但调度器继续运行以接受恢复切换。

## 调度器接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 调度器自身健康检查 |
| `GET` | `/status` | 返回当前状态、阶段和计时信息 |
| `POST` | `/switch/mineru` | 切换到 MinerU |
| `POST` | `/switch/mechvl` | 切换到 MechVL |
| `POST` | `/switch/idle` | 停止所有 GPU 模型 |
| `POST` | `/mineru/parse` | 确保 MinerU 就绪并解析文件 |

切换请求启动后台切换并立即返回状态。SolidCog 和浏览器通过状态接口轮询完成情况。解析接口在调度器内标记 `busy`，阻止切换，完成后保持 MinerU 驻留。MechVL 分析请求同样通过调度器代理并标记忙碌，避免问答期间切换。

## 切换计时器

前端显示：

```text
正在加载 MechVL
已用 00:37 · 预计剩余 00:15
```

调度器按目标模型保存最近成功切换的总耗时和各阶段耗时，使用有限窗口滚动平均估算。首次没有历史记录时仅显示正向计时和“首次加载，暂无法估算”。预计剩余时间最低为零；超过预计时间后显示“即将完成”，不显示负数。完成后短暂显示实际总耗时。

计时基于调度器的 `started_at`，刷新页面不会重置。统计保存在调度器私有 JSON 文件中，采用临时文件加原子替换写入，不进入 SolidCog 业务数据库。

## OCR 后端与数据流

上传表单增加 `ocr_backend`：

- `mineru`
- `qwen`，作为未提供字段时的兼容默认值

Qwen 路径保留现有逻辑，不改变本地 GPU 模式。MinerU 路径调用调度器；必要时自动切换到 MinerU，然后解析上传文件。

```text
PDF/PNG
  +-- qwen -> 现有 Qwen3-VL 服务 -> 统一 OCR 结果
  +-- mineru -> 本地调度器 -> MinerU -> 适配器 -> 统一 OCR 结果
```

统一结果契约保持当前数据库写入方式：

```json
{
  "title_block": "...",
  "tech_block": "...",
  "all_text": "...",
  "layout": "horizontal",
  "backend": "mineru_vlm",
  "model": "MinerU2.5-Pro-2605-1.2B",
  "pages_processed": 1,
  "page_errors": []
}
```

MinerU 适配规则：

- `all_text` 从 Markdown 提取，移除图片引用并保留表格文本。
- `tech_block` 优先提取“技术要求”标题后的编号段落。
- `title_block` 优先使用页面下部表格和标题栏内容；不能可靠细分时保留相关表格全文。
- `layout` 根据原始页面宽高确定。
- 原始 Markdown、content list 和必要的调试产物保存在独立结果目录，数据库只保存统一文本字段。

## MechVL 数据流

图纸问答不再假设用户已手动启动 MechVL。SolidCog 将请求交给调度器：

1. 若 MinerU 正忙，返回冲突状态，不中断 OCR。
2. 若当前不是 MechVL，自动切换并等待就绪。
3. 调度器代理现有 MechVL `/analyze` 请求并标记忙碌。
4. 回答完成后 MechVL 保持驻留。

现有 OCR 上下文与图纸预览生成方式保持不变。

## 前端转换器

工作台提供两个不同的分段控件：

```text
OCR 后端  [MinerU 本地 | Qwen3-VL 云端]
本地 GPU  [空闲 | MinerU OCR | MechVL 审核]
```

OCR 后端选择随当前上传批次提交。本地 GPU 控件反映调度器真实状态，可自由手动切换。

切换期间：

- 禁用重复切换和依赖目标模型的操作。
- 显示阶段、正向计时和预计剩余时间。
- 每秒更新本地计时显示，每三秒同步服务端真实状态。
- 页面隐藏时降低状态轮询频率。
- 成功后短暂显示本次实际耗时。
- 失败时恢复控件并显示可执行的中文错误。

选择 MinerU 上传时自动准备 MinerU；发起问答时自动准备 MechVL。Qwen OCR 不触发本地切换。调度器不可用时，Qwen OCR、图纸浏览、搜索和导出仍可用。

## 启动与恢复

新增调度器启动脚本。SolidCog 日常启动脚本确保轻量调度器已运行，但默认状态为 `idle`，不加载 GPU 模型。调度器已运行时重复启动不创建第二实例。

SolidCog 或浏览器重启后通过 `/status` 恢复真实模型状态。调度器重启时检查自己记录的子进程身份；无法确认归属的进程不主动终止，并报告明确错误供人工处理。

## 错误处理

- 模型忙：返回 HTTP 409 和当前任务类型。
- 调度器不可达：本地功能返回 HTTP 503，Qwen 路径继续工作。
- 模型启动超时：停止本次启动的进程组，状态进入 `error`。
- 模型异常退出：健康检查更新为 `error`，保留最近日志路径。
- MinerU 解析失败：保存诊断信息，不写入伪造 OCR 文本。
- MechVL 推理超时或 OOM：沿用现有中文错误，并确保 busy 标记释放。
- 显存未释放：不启动目标模型，等待至超时后报错，避免两个模型重叠。

## 安全与进程边界

- 调度器仅接受本机连接。
- 文件解析只允许映射到 SolidCog 配置的上传目录。
- 所有路径在使用前解析并验证父目录，防止路径穿越。
- 进程启动参数使用数组传递，不拼接不可信 shell 文本。
- PID 之外同时记录进程启动标识，防止 PID 复用误杀。

## 测试与验收

自动化测试覆盖：

- 状态机和重复切换幂等性。
- MinerU/MechVL 互斥及忙碌时拒绝切换。
- 启动失败、超时、异常退出和恢复。
- 双计时首次状态、历史估算及超时不为负数。
- OCR 后端分发与未传字段的 Qwen 兼容默认值。
- MinerU Markdown 到统一字段的适配。
- 调度器不可用时 Qwen 与普通管理功能仍可用。
- 前端模型状态、按钮禁用和错误显示。

实机验收使用项目真实机械图纸：

1. `idle -> mineru`，上传并确认 OCR 入库、查看和搜索正常。
2. `mineru -> mechvl`，确认 MinerU 进程退出后 MechVL 才加载并能回答问题。
3. `mechvl -> mineru`，再次解析图纸并确认切换计时器正确。
4. `mineru -> idle`，确认 GPU 模型进程完全退出。
5. 全程采样显存，验证任意时刻最多一个模型驻留且没有 OOM。
6. 验证 Qwen OCR 不改变当前本地模式。

## 文档交付

README 将补充：

- 调度器、MinerU 和 MechVL 的目录与端口。
- 首次安装及日常启动步骤。
- OCR 后端选择和自动切换行为。
- 手动状态、切换、停止与健康检查命令。
- 日志位置、超时、OOM、孤儿进程和 WSL 恢复方法。
- 8 GB 显存设备的互斥运行限制和实测性能。
