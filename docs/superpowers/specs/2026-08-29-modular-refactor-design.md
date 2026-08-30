# SolidCog 模块化重构设计

## 目标

将当前约 2000 行的 `main.py` 拆成职责清晰的轻量模块，删除早期副本、重复路由、临时脚本和无效测试，同时保持上传、OCR、搜索、查看、导出及 AI 问答的现有行为。

本次不引入新框架、任务队列、认证系统、数据库迁移框架或插件体系。重构完成后，`main.py` 只负责暴露 FastAPI 应用。

## 目录结构

```text
solidcog/
├─ main.py
├─ app/
│  ├─ __init__.py
│  ├─ application.py
│  ├─ config.py
│  ├─ database.py
│  ├─ schemas.py
│  ├─ repositories/
│  │  └─ drawings.py
│  ├─ services/
│  │  ├─ drawings.py
│  │  ├─ ocr.py
│  │  └─ ai.py
│  └─ routers/
│     ├─ pages.py
│     ├─ drawings.py
│     └─ ai.py
├─ templates/
├─ uploads/
├─ tests/
│  ├─ test_routes.py
│  └─ test_ocr_dispatch.py
└─ requirements.txt
```

`main.py` 的目标内容：

```python
from app.application import create_app

app = create_app()
```

## 模块职责

- `application.py`：创建 FastAPI 应用、挂载上传目录、注册路由。
- `config.py`：加载 `.env`，集中管理项目路径、上传限制、模型和 OCR 后端配置。
- `database.py`：创建 SQLite 连接并初始化现有 `drawings` 表。
- `schemas.py`：存放 HTTP 请求使用的 Pydantic 模型。
- `repositories/drawings.py`：集中管理 `drawings` 表的增删查搜 SQL。
- `services/drawings.py`：编排文件保存、OCR、入库、删除和导出。
- `services/ocr.py`：提供 `run_ocr(path)`，内部选择 Qwen VL、Paddle、Ascend CANN 或 MindX。
- `services/ai.py`：负责千问、DeepSeek、图纸上下文和分析工具调用。
- `routers/`：只处理 HTTP 输入输出，不直接执行 SQL 或调用第三方模型。

不创建单实现接口、工厂、Repository 基类或依赖注入容器。新增功能直接增加小型函数和对应 router。

## 数据流

上传流程保持同步：

1. Router 校验请求并调用 drawing service。
2. Drawing service 校验扩展名和 50 MB 限制，保存文件。
3. OCR service 识别文件并返回统一结果。
4. Drawing repository 写入现有 SQLite 表。
5. 任一步骤失败时，删除本次新保存且尚未成功入库的文件。
6. 根据 `Accept` 返回 JSON 或重定向 `/home`。

搜索、OCR 查看和 AI 图纸上下文都通过 drawing repository 获取数据，避免重复 SQL。配置路径统一基于项目根目录，确保从不同工作目录启动时行为一致。

## 保留接口

- `GET /`
- `GET /home`
- `GET /search`
- `POST /upload-drawing`
- `GET /drawings`
- `DELETE /drawings/{drawing_id}`
- `GET /delete-drawing/{drawing_id}`
- `GET /delete-all-drawings`
- `GET /ocr/{drawing_id}`
- `GET /view-ocr/{drawing_id}`
- `GET /export-ocr/{drawing_id}`
- `POST /chat`
- `POST /chat-with-drawing`
- `POST /analyze-drawing`
- `POST /analyze-drawing-simple`
- `POST /qwen-tool`

为保持现有页面行为，本轮暂时保留两个 GET 删除入口；后续前端接口整理时再改为 DELETE。

删除旧别名 `/status`、`/upload`、`/drawings-list`，并删除重复注册的 `/home` 和 `/view-ocr/{drawing_id}`。

## OCR 与 AI 扩展边界

`run_ocr(path)` 是唯一 OCR 入口，返回包含 `title_block`、`tech_block`、`all_text`、`layout` 和 `backend` 的字典。后端选择保留现有环境变量语义。

Paddle 的布局检测和区域裁切只保留一套实现。Qwen、Paddle、Ascend CANN 和 MindX 仍采用简单函数分派；只有当真实后端数量和差异明显增长时才引入注册机制。

AI service 保持现有模型和响应语义。选择图纸后的问答仍按当前行为使用 Qwen，本次不改变产品逻辑。

## 删除范围

删除整个早期仓库副本 `C:\Solidcog\drawing-system`。

主线删除：

- `alter_database.py`、`alter_database_v2.py`、`rebuild_database.py`
- `check_db.py`、`check_ocr_db.py`、`check_routes.py`、`fix_routes.py`
- `test_upload.py`、`test_all_routes.py`
- 临时 OCR 图片、调试截图、运行日志和提取中间文件
- 重复裁切代码、重复导入、旧路由和失效注释

保留：

- `database.db` 与 `uploads/` 中的现有运行数据
- `templates/`、部署脚本、`.env.example`
- 项目方案、技术文档和 `docs/` 内容

## 错误处理

- 信任边界继续校验文件类型、大小、记录 ID 和必需配置。
- Repository 负责关闭连接，但不吞掉数据库错误。
- Service 将可预期错误转换为清晰的业务异常。
- Router 将业务异常映射为稳定 HTTP 状态码。
- 第三方 API 错误不暴露密钥或完整内部响应。

## 测试与验收

只保留两组小型标准库 `unittest` 测试：

- `test_routes.py`：使用临时数据库验证应用导入、主页、搜索、列表、OCR 查看和基础增删查搜。
- `test_ocr_dispatch.py`：替换实际后端函数，验证 OCR 后端选择、统一结果和未知后端错误。

测试不得调用真实千问、DeepSeek、Paddle 模型，也不得读写现有 `database.db` 和 `uploads/`。

验收条件：

1. `main.py` 仅保留应用入口。
2. 应用可导入并启动。
3. 保留接口的无外部依赖路径通过测试。
4. 现有数据库结构、36 条记录和上传文件不受影响。
5. 重复路由和已批准的旧接口不再注册。
6. 代码中不存在两套相同的数据库查询或布局裁切主实现。

## 非目标

- 异步 OCR、后台队列或进度系统
- 用户登录、权限和 CSRF 改造
- PostgreSQL、ORM 或迁移框架
- 前端重设计
- 真实 Ascend/MindX 推理接入
- 改变现有 OCR、搜索或 AI 问答产品行为
