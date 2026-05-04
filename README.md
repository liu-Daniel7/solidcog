# 工程图纸智能管理系统

## 系统简介

本系统是一款集成AI技术的工程图纸管理平台，支持PDF/PNG图纸上传、千问VL智能OCR识别、自动分析图纸结构和技术要求。具备拖放上传、双模型AI助手（千问VL/DeepSeek）、图纸搜索等功能，为工程设计人员提供高效、智能的图纸管理解决方案。

## 系统功能

- ✅ **图纸上传**：支持PDF/PNG格式，可批量上传
- ✅ **智能OCR**：使用千问VL进行图纸识别和分析
- ✅ **AI助手**：支持千问VL和DeepSeek双模型切换
- ✅ **拖放上传**：支持从文件浏览器和图纸列表拖动文件
- ✅ **图纸搜索**：基于OCR结果的全文搜索
- ✅ **结果展示**：分区显示标题栏、技术要求、全局OCR

## 快速开始

### 1. 安装依赖

运行安装脚本：

```bash
# Windows
install.bat

# Linux/MacOS
chmod +x install.sh
./install.sh
```

### 2. 配置API密钥

打开 `main.py` 文件，修改以下API密钥：

```python
# 千问API配置
QWEN_API_KEY = "你的千问API密钥"

# DeepSeek API配置
DEEPSEEK_API_KEY = "你的DeepSeek API密钥"
```

### 3. 启动系统

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 访问系统

打开浏览器访问：
- 主页：http://localhost:8000/主页
- API文档：http://localhost:8000/docs

## 技术栈

### 后端
- Python 3.9.13
- FastAPI 0.128.8
- Uvicorn 0.39.0
- SQLite
- PaddleOCR 2.6.1.3
- 千问VL API
- DeepSeek API

### 前端
- HTML5
- CSS3
- JavaScript
- Jinja2模板引擎

## 目录结构

```
drawing-system/
├── main.py              # 主程序（FastAPI应用）
├── requirements.txt     # Python依赖
├── install.bat          # Windows安装脚本
├── database.db          # SQLite数据库文件
├── templates/           # HTML模板目录
│   ├── index.html       # 主页模板
│   └── ocr_view.html    # OCR查看模板
├── uploads/            # 上传文件存储目录
└── __pycache__/        # Python缓存
```

## 使用指南

### 上传图纸
1. 在主页点击"选择文件"按钮，或直接拖放PDF/PNG文件到上传区域
2. 点击"上传图纸"按钮
3. 系统会自动进行OCR识别和分析

### 使用智能助手
1. 在主页的"智能助手"区域选择模型（千问VL或DeepSeek）
2. 输入问题并发送
3. 或直接拖放PDF文件到对话框进行分析

### 查看OCR结果
1. 在图纸列表中点击"查看OCR"链接
2. 查看标题栏、技术要求和全局OCR结果

### 搜索图纸
1. 在主页的搜索框中输入关键词
2. 点击"搜索"按钮
3. 查看搜索结果

## 常见问题

### 1. 上传图纸失败
- 检查文件格式是否为PDF或PNG
- 检查文件大小是否过大
- 检查网络连接是否正常

### 2. OCR识别失败
- 检查API密钥是否正确
- 检查网络连接是否正常
- 尝试使用不同的模型（千问VL或PaddleOCR）

### 3. 服务器启动失败
- 检查8000端口是否被占用
- 检查依赖是否安装完整
- 检查Python版本是否为3.9+

## 许可证

本项目使用MIT许可证，详见LICENSE文件。

## 贡献

欢迎提交Issue和Pull Request，共同改进系统功能。

## 联系方式
18600470339
如有问题，请联系系统管理员。