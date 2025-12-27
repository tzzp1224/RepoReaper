# GitHub RAG Agent

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green)
![Status](https://img.shields.io/badge/status-active-success)

[English Version](README.md)

## 项目概述

**GitHub RAG Agent** 是一个基于 **Google Gemini** 大语言模型与 **检索增强生成 (RAG)** 技术的自动化代码分析系统。该系统旨在解决开发者在面对陌生、复杂代码库时的理解痛点。

与传统的静态代码搜索工具不同，本系统实现了一个完整的**智能体工作流 (Agentic Workflow)**。它能够模拟高级工程师的阅读习惯：自主感知仓库结构，规划需要优先阅读的核心文件以优化 Token 消耗，将代码逻辑索引至向量数据库，并基于真实的上下文回答关于架构、逻辑与实现的复杂问题。

## 核心功能

* **智能规划 (Agentic Planning)**：系统通过 GitHub API 获取完整的文件树，并利用 LLM 规划器自动识别项目中前 3-5 个最具分析价值的核心文件（如入口文件、配置文件、核心逻辑层），从而在有限的上下文窗口内实现最大化的信息覆盖。
* **高精度检索 (RAG)**：集成 ChromaDB 向量数据库，利用 `text-embedding-004` 模型将代码片段转化为高维向量。在问答阶段，系统通过语义相似度检索最相关的代码块，确保回答基于真实代码证据而非模型幻觉。
* **实时流式响应 (Real-time Streaming)**：后端采用 Server-Sent Events (SSE) 协议，将智能体的感知、思考、下载、索引与生成报告的全过程实时推送至前端，提供透明且极具交互性的用户体验。
* **工程化架构**：项目采用领域驱动设计 (DDD) 理念，将单脚本重构为分层清晰的模块化架构，解耦了 API 接口、业务逻辑与基础设施层，便于后续扩展与维护。

## 系统架构

系统遵循微服务式的分层设计，确保了各组件的职责单一性。

[![](https://mermaid.ink/img/pako:eNp1Um1L21AU_iuX-2kD7Zr0zebDoFOnAwdiywZL-yE11zbQJCVNxrZGKGrVKbad2rqhq6uyrTDMHBtTfP0zvTftv9hNE0WHXkhO7snzPOec594inFZFBDmY0YR8FiRGkgqgazgnIUXnX6I0wNYeWT6yf_xMgcHBx-Z4IjH5KB4fNUFs8hn_VCjoNAJc_oqr-ymX7CQcaPdwHp98M0Ecaa-lacTHMlQT2GcNUlknO2t4peUR3HfBSLtNJCH5cowv510M_jWXhC7CWZ5YvwDjA93KEa427G0Ln9ft0w3SXDDBmKSPG2neDcBV6ZXmeuU1_KmduluK9QH7YLtXeo_Lv3ubFmksdU7_mmAyJygK0viJieeg-30BL6_fLxGgEpu7ZLmGax96S1X7zx4-q5vgBZrWVW3kCe-mSf2QrFn4ZOOWA0gR77EC757YrVLXuiCN8_-s8AZ0ak-NxhOO7yaYQrKqoymUV_nu5Y7dXr2CdU43aNUbvV811hcYldNIFCUlUzDBcFZTZYF_4EYKwItlfPARW83OxerDGwqeO30BUmnbtUVqPpIlReLHVDWTQ97O6ezuQW_10Dle6RyvUuvJfom6R2eJjfH0Ab0tyz7YJO0W_rzl6Thph-M5vt2kANO7tnCA3mZJhJyuGWgAykiTBWcLiw43CfUsklEScvRTRDOCkdMdV2cpLS8or1RVvmJqqpHJQm5GyBXozsiLgo5GJIEejXyd1ehESBtWDVqXY5lgXwRyRfgGciG_z8-woTAbjjJDkRAboX_fQo4JMb5IMBqlySDjZwPM7AB81y_L-JgoE40EomwwHAgPBf1Ds_8AwJVx7g?type=png)](https://mermaid.live/edit#pako:eNp1Um1L21AU_iuX-2kD7Zr0zebDoFOnAwdiywZL-yE11zbQJCVNxrZGKGrVKbad2rqhq6uyrTDMHBtTfP0zvTftv9hNE0WHXkhO7snzPOec594inFZFBDmY0YR8FiRGkgqgazgnIUXnX6I0wNYeWT6yf_xMgcHBx-Z4IjH5KB4fNUFs8hn_VCjoNAJc_oqr-ymX7CQcaPdwHp98M0Ecaa-lacTHMlQT2GcNUlknO2t4peUR3HfBSLtNJCH5cowv510M_jWXhC7CWZ5YvwDjA93KEa427G0Ln9ft0w3SXDDBmKSPG2neDcBV6ZXmeuU1_KmduluK9QH7YLtXeo_Lv3ubFmksdU7_mmAyJygK0viJieeg-30BL6_fLxGgEpu7ZLmGax96S1X7zx4-q5vgBZrWVW3kCe-mSf2QrFn4ZOOWA0gR77EC757YrVLXuiCN8_-s8AZ0ak-NxhOO7yaYQrKqoymUV_nu5Y7dXr2CdU43aNUbvV811hcYldNIFCUlUzDBcFZTZYF_4EYKwItlfPARW83OxerDGwqeO30BUmnbtUVqPpIlReLHVDWTQ97O6ezuQW_10Dle6RyvUuvJfom6R2eJjfH0Ab0tyz7YJO0W_rzl6Thph-M5vt2kANO7tnCA3mZJhJyuGWgAykiTBWcLiw43CfUsklEScvRTRDOCkdMdV2cpLS8or1RVvmJqqpHJQm5GyBXozsiLgo5GJIEejXyd1ehESBtWDVqXY5lgXwRyRfgGciG_z8-woTAbjjJDkRAboX_fQo4JMb5IMBqlySDjZwPM7AB81y_L-JgoE40EomwwHAgPBf1Ds_8AwJVx7g)

## 项目结构

本项目采用标准的 Python 工程目录结构：

Plaintext

```
github-rag-agent/
├── app/
│   ├── core/
│   │   └── config.py          # 集中式配置管理 (环境变量加载与校验)
│   ├── services/
│   │   ├── agent_service.py   # 智能体核心编排逻辑 (感知-规划-执行循环)
│   │   ├── github_service.py  # GitHub API 交互封装 (含文件过滤器)
│   │   └── vector_service.py  # 向量数据库管理 (ChromaDB 初始化与检索)
│   ├── utils/
│   │   └── llm_client.py      # Google GenAI 客户端单例封装
│   └── main.py                # 应用程序入口与路由定义
├── frontend/
│   └── index.html             # 轻量级客户端界面
├── .env                       # 环境变量配置文件
├── requirements.txt           # 项目依赖清单
└── README_CN.md               # 项目文档
```

## 安装与部署

### 前置要求

- **Python**: 版本 3.9 或更高。
- **Google Gemini API Key**: 用于模型推理与向量生成。
- **GitHub Access Token**: 用于解除 API 请求速率限制。

### 安装步骤

1. **克隆仓库**

   Bash

   ```
   git clone [https://github.com/your-username/github-rag-agent.git](https://github.com/your-username/github-rag-agent.git)
   cd github-rag-agent
   ```

2. **安装依赖** 建议使用虚拟环境进行安装。

   Bash

   ```
   pip install -r requirements.txt
   ```

3. **环境配置** 在项目根目录下创建 `.env` 文件，并填入以下配置：

   Ini, TOML

   ```
   GEMINI_API_KEY=your_gemini_api_key
   GITHUB_TOKEN=your_github_token
   ```

### 启动服务

1. **启动后端服务** 请务必使用模块化方式启动，以确保 Python 包路径解析正确：

   Bash

   ```
   python -m app.main
   ```

   服务默认监听 `http://127.0.0.1:8000`。

2. **访问前端界面** 在浏览器中打开 `frontend/index.html` 文件。 *注意：为获得最佳体验（避免跨域问题），建议通过本地服务器（如 VS Code Live Server）运行该 HTML 文件。*

## API 接口说明

系统暴露以下核心 RESTful 接口：

- `GET /analyze?url={repo_url}`
  - **描述**：启动针对指定 GitHub 仓库的分析任务。
  - **响应**：SSE 事件流，包含任务步骤与状态更新。
- `POST /chat`
  - **描述**：基于已建立的索引进行代码问答。
  - **请求体**：`{"query": "用户问题"}`
  - **响应**：JSON 对象，包含回答文本及参考的文件来源列表。
- `GET /`
  - **描述**：健康检查接口，返回当前服务状态及模型配置信息。

## 未来优化方向 (Roadmap)

### 1. 向量存储持久化

- **现状**：当前 ChromaDB 运行在易失性（内存）模式下，服务重启会导致索引数据丢失。
- **规划**：配置 ChromaDB 使用本地磁盘持久化存储，或集成云原生向量数据库（如 Pinecone, Milvus），实现一次索引、多次查询。

### 2. 高级分块策略 (Advanced Chunking)

- **现状**：采用基于字符长度的简单截断或分割策略。
- **规划**：引入基于抽象语法树 (AST) 的语义分块策略。确保向量切片尊重函数、类与方法的边界，从而显著提升检索的语义准确性。

### 3. 多用户会话隔离

- **现状**：使用全局单例向量存储，仅支持单用户单任务模式。
- **规划**：引入 Session ID 机制，为不同用户或不同仓库的分析任务创建独立的向量集合 (Collection)，实现多租户并发支持。

### 4. LLM 适配层抽象

- **现状**：代码与 Google Gemini SDK 强耦合。
- **规划**：引入适配器模式或集成 LangChain 框架，构建统一的 LLM 接口层，从而支持 OpenAI (GPT-4), Anthropic (Claude 3) 或本地开源模型 (DeepSeek/Llama 3)。

## 许可证

本项目遵循 MIT 开源许可证。详情请参阅 LICENSE 文件。