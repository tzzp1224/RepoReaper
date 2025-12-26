# 🧠 GitHub RAG Agent (Chat with Code)

> 基于 Google Gemini 和 RAG 技术构建的智能代码助手。它可以深入分析 GitHub 仓库，通过向量检索（Vector Search）回答关于代码架构、逻辑和功能的具体问题。

## 📖 项目简介

这是一个全栈 AI Agent 项目，旨在帮助开发者快速理解陌生的 GitHub 开源项目。它不仅仅是一个聊天机器人，更是一个具备**Agentic Workflow（代理工作流）**的智能系统。

**核心工作流：**

1. **感知**: 自动抓取 GitHub 仓库目录结构。
2. **规划**: AI 自主思考，判断哪些文件是核心代码（Critical Files）。
3. **索引**: 下载核心代码，使用 Embedding 模型转化为向量并存入 ChromaDB。
4. **交互**: 提供 Chat 界面，利用 RAG（检索增强生成）技术，基于真实代码片段回答用户问题。

## ✨ 核心功能

- **⚡️ 智能仓库分析**: 输入 GitHub URL，自动过滤非代码文件，分析项目结构。
- **🤖 AI 驱动的文件筛选**: 使用 Gemini 模型通过语义理解，自动挑选 Top 3-5 个最关键的文件进行深入阅读，避免 Token 浪费。
- **📚 RAG 知识库**: 集成 **ChromaDB**，支持将代码片段向量化存储，实现高精度上下文检索。
- **💬 代码问答 (Chat with Code)**: 支持对特定代码逻辑提问（如“鉴权逻辑在哪里？”），AI 会引用真实代码片段作答。
- **🌊 流式反馈 (SSE)**: 前端采用 Server-Sent Events，实时展示 Agent 的思考、下载、索引和生成报告的全过程。
- **🛡️ 健壮性设计**: 内置 API 限流重试机制（针对 Gemini 免费版限制）和 Windows 中文编码修复。

## 🛠 技术栈

- **LLM Model**: Google Gemini 2.0 / 3.0 Flash (通过 `google-genai` SDK v1beta 调用)
- **Embedding**: text-embedding-004
- **Backend**: Python, FastAPI, Uvicorn, SSE-Starlette
- **Vector Store**: ChromaDB (Ephemeral/Memory mode)
- **Tools**: PyGithub (GitHub API 交互)
- **Frontend**: Vanilla HTML/JS (轻量级客户端)

## 📂 项目结构

Plaintext

```
.
├── main.py                  # 🚀 项目入口 (FastAPI Server)，处理 HTTP 请求和 SSE 流
├── agent.py                 # CLI 版本的 Agent 逻辑 (独立运行脚本)
├── vector_store.py          # 💾 向量数据库管理 (ChromaDB 封装，含 Embedding 逻辑)
├── tools_github.py          # 🛠 GitHub 工具包 (获取目录树、下载文件内容)
├── test_client.html         # 💻 前端界面 (用于测试和交互)
├── test_gemini.py           # ✅ Gemini API 连通性测试脚本
├── check_available_models.py# 📋 列出可用 Gemini 模型工具
└── .env                     # 🔐 配置文件 (API Keys)
```

## 🚀 快速开始

### 1. 环境准备

确保你已安装 Python 3.9 或更高版本。

Bash

```
# 克隆仓库
git clone <your-repo-url>
cd <your-repo-name>
```

### 2. 安装依赖

根据代码中的 import，你需要安装以下 Python 库：

Bash

```
pip install fastapi uvicorn python-dotenv google-genai chromadb PyGithub sse-starlette
```

### 3. 配置环境变量

在项目根目录下创建一个 `.env` 文件，并填入你的 API Key：

Ini, TOML

```
# .env file

# 申请地址: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your_gemini_api_key_here

# 申请地址: https://github.com/settings/tokens (Classic Token 即可)
GITHUB_TOKEN=your_github_access_token_here
```

### 4. 运行服务

启动 FastAPI 后端服务：

Bash

```
python main.py
```

*服务默认运行在 `http://127.0.0.1:8000`*

### 5. 使用前端

直接在浏览器中打开 `test_client.html` 文件即可（无需部署，它是静态 HTML，直接双击打开或通过 VS Code Live Server 打开）。

1. 在左侧输入框填入 GitHub 仓库地址 (例如 `https://github.com/fastapi/fastapi`)。
2. 点击 **"开始分析"**。
3. 等待右侧日志显示 "✅ 分析完成"。
4. 在右侧聊天框输入问题开始对话。

## ⚙️ 关键配置说明

### 模型选择

在 `main.py` 和 `agent.py` 中，默认使用了预览版模型以获得更快的速度：

Python

```
MODEL_NAME = "gemini-3-flash-preview"
```

*注意：Flash 模型可能有 RPM (每分钟请求数) 限制，代码中已包含简单的限流处理，但如果在生产环境使用，建议切换至 `gemini-1.5-pro` 或购买付费配额。*

### 向量数据库重置

目前的 `VectorStore` 实现采用**内存模式**。这意味着：

- 每次重启 `main.py`，之前的索引会丢失。
- 每次开始新的分析 (`/analyze`)，代码会尝试清理旧的集合 (`repo_code`)，确保不会混淆不同仓库的代码。

## ⚠️ 常见问题 (Troubleshooting)

1. **UnicodeEncodeError (Windows)**:
   - 代码中已包含 `sys.stdout = io.TextIOWrapper(...)` 修复补丁，通常无需担心。如果遇到乱码，请检查终端编码设置。
2. **429 Resource Exhausted**:
   - 这是因为触发了 Google API 的速率限制。代码中的 `agent.py` 包含重试逻辑 (`call_gemini_with_retry`)。如果频繁遇到，请适当增加 `time.sleep` 的时长。
3. **GitHub API Rate Limit**:
   - 未配置 `GITHUB_TOKEN` 时每小时只能请求 60 次。请务必在 `.env` 中配置 Token 以提升至 5000 次/小时。

## 📝 待办事项 (To-Do)

- [ ] 支持按文件后缀过滤（目前硬编码在 `tools_github.py`）
- [ ] 优化 Chunking 策略（目前简单按字符截断，可升级为 RecursiveCharacterTextSplitter）
- [ ] 向量数据库持久化保存
- [ ] 支持更多 LLM 模型切换

## 🤝 贡献

欢迎提交 Issues 和 Pull Requests！