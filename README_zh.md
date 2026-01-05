<div align="center">

  <img src="./docs/logo.jpg" width="800" style="max-width: 100%;" height="auto" alt="RepoReaper Logo">

  <h1>RepoReaper</h1>

  <h3>
    💀 Harvest Logic. Dissect Architecture. Chat with Code.
    <br>
    基于 AST 深度解析 · 双语适配的自治型代码审计 Agent
  </h3>

  <p>
    <a href="./README.md">English</a> • 
    <strong>简体中文</strong>
  </p>
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/tzzp1224/RepoReaper?style=flat-square&color=blue" alt="License">
  </a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python Version">
  <img src="https://img.shields.io/badge/Model-DeepSeek_V3-673AB7?style=flat-square&logo=openai&logoColor=white" alt="DeepSeek Powered">
  <img src="https://img.shields.io/badge/Agent-ReAct_Pattern-orange?style=flat-square" alt="Agent Architecture">

  <br>

  <img src="https://img.shields.io/badge/RAG-Hybrid_Search-009688?style=flat-square" alt="RAG">
  <img src="https://img.shields.io/badge/Parser-Python_AST-FFD700?style=flat-square&labelColor=black" alt="AST Parsing">
  <img src="https://img.shields.io/badge/VectorDB-Chroma-important?style=flat-square" alt="ChromaDB">
  <img src="https://img.shields.io/badge/Framework-FastAPI-005571?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">

  <br>
  <br>

  <img src="./docs/demo_preview.gif" width="800" style="max-width: 100%; box-shadow: 0 4px 8px rgba(0,0,0,0.1); border-radius: 8px;" alt="RepoReaper Demo">

  <br>
</div>

---

**一个智能化的、具备代理（Agentic）能力的自动化架构分析与语义代码搜索系统。**

本项目超越了传统的“代码对话”范式，实现了一个模拟高级技术专家认知过程的自治 Agent。系统不再对代码仓库进行静态索引，而是创造性地将 **大语言模型 (LLM)** 视为 CPU，将 **向量数据库 (Vector Store)** 视为高速 **L2 智能缓存**。Agent 能够动态遍历仓库结构，预取关键上下文进入“缓存”（RAG），并在检测到语义缺失时执行 Just-In-Time (JIT) 实时读取。

---

## 🚀 核心理念：主动收割，而非被动检索

我们将 RAG 重新定义为 LLM 的 **L2 智能缓存**，而非静态数据库：

1.  **全局制图 (Repo Mapping):** 启动时，Agent 使用 Python AST 解析整个仓库，提取所有类和方法的签名，构建一张“代码地图”。
2.  **智能预取 (Warm-up):** 基于架构重要性，自主筛选最关键的核心文件（如 `Core`, `Auth`, `API`）进行深度解析并预热缓存。
3.  **Just-In-Time 审计 (ReAct Loop):** 在问答中，如果发现缓存中缺少关键实现细节，Agent 不会强行回答，而是触发 `<tool_code>` 工具链，实时拉取 GitHub 上的缺失文件，实现“边看边答”。

---

## 🏗 技术架构与创新点

### 1. 🧬 基于 AST 的结构化感知 (AST-Aware Chunking)
拒绝粗暴的文本切割。我们利用 `ast` 模块实现了**逻辑完整的分块**：
* **类/方法级切分:** 确保函数体永远不会被截断。
* **上下文注入:** 当拆解超大类（Large Class）时，系统会自动将父类的 Docstring 和签名注入到每个子方法的 Context 中，让 LLM 既见树木，也见森林。

### 2. 🤖 "ReAct" 自治代理循环
Chat Service 实现了一套完整的推理闭环：
* **查询重写 (Query Rewrite):** 自动将用户的中文自然语言转换为精准的英文代码检索关键词（如 "鉴权" -> "auth middleware jwt"）。
* **自我修正:** 模型在发现信息不足时，会主动发起请求（Agent Action），拉取缺失的文件路径，并在单次交互内完成学习。

### 3. 🔍 混合检索机制 (Hybrid Search)
为了应对代码检索的复杂性，采用加权融合策略：
* **Dense Retrieval:** 使用 `BAAI/bge-m3` 向量捕捉语义（理解 "登录" 等同于 "Login"）。
* **Sparse Retrieval (BM25):** 精准捕捉变量名、错误码和特定函数签名。

### 4. 🌏 原生双语适配
专为多语言环境打造：
* **动态提示词:** 系统根据用户输入自动切换 System Prompt（英文专业风 / 中文务实风）。
* **前端交互:** UI 内置语言切换开关，从架构报告生成到后续问答，全链路适配中文开发者习惯。

---

## 🛠 技术栈

* **核心框架:** Python 3.10+, FastAPI, AsyncIO (高并发 I/O)
* **大模型基座:** OpenAI SDK (完美适配 DeepSeek-V3 / SiliconFlow)
* **向量存储:** ChromaDB (本地持久化)
* **代码解析:** Python `ast` 标准库
* **前端:** HTML5, SSE (流式传输), Mermaid.js (架构图渲染)
* **部署:** Docker, Gunicorn, Uvicorn

---

## ⚡ 性能优化

* **无状态架构:** `VectorStoreManager` 采用磁盘持久化设计，支持多 Worker 并发读取，内存占用低且无锁竞争。
* **网络鲁棒性:** 针对 GitHub API 的 403/429 速率限制及长文本生成的超时问题，内置了指数退避与自动重试机制。

---

## 🏁 快速开始

**前置要求:**
* Python 3.9+
* 有效的 GitHub Token
* 大模型 API Key（推荐使用 DeepSeek-V3 + SiliconFlow 免费版 bge-m3）。

1.  **克隆仓库**
    ```bash
    git clone [https://github.com/tzzp1224/RepoReaper.git](https://github.com/tzzp1224/RepoReaper.git)
    cd RepoReaper
    ```

2.  **安装依赖**
    建议使用虚拟环境以避免依赖冲突：
    ```bash
    # 创建并激活虚拟环境
    python -m venv venv
    source venv/bin/activate  # Windows 用户使用: venv\Scripts\activate
    
    # 安装依赖
    pip install -r requirements.txt
    ```

3.  **配置环境**
    在项目根目录下复制 `.env.example` 或新建 `.env` 文件：
    ```env
    # GitHub 访问令牌 (用于读取仓库)
    GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxx
    
    # LLM 服务商 Key (如 DeepSeek)
    DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxx
    
    # Embedding 服务商 Key (SiliconFlow 提供免费 bge-m3)
    SILICON_API_KEY=sk-xxxxxxxxxxxxxxx
    ```

4.  **启动服务**

    **方式 A：本地运行 (Gunicorn)**
    适用于开发或直接部署：
    ```bash
    gunicorn -c gunicorn_conf.py app.main:app
    ```

    **方式 B：Docker 容器化运行 🐳**
    无需配置本地 Python 环境，直接一键启动：
    ```bash
    # 1. 构建镜像
    docker build -t reporeaper .
    
    # 2. 启动容器 (挂载 .env 文件)
    docker run -d -p 8000:8000 --env-file .env --name reporeaper reporeaper
    ```

5.  **开始审计**
    浏览器访问 `http://localhost:8000`，输入任意 GitHub 仓库地址，观察 RepoReaper 如何“收割”代码架构。

---

## 📈 Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>