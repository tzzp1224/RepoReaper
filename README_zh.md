<div align="center">

  <img src="./docs/logo.jpg" width="800" style="max-width: 100%;" height="auto" alt="RepoReaper Logo">

  <h1>RepoReaper</h1>

  <h3>💀 Harvest Logic. Dissect Architecture. Chat with Code.</h3>

  <p>
    <a href="./README.md">English</a> • 
    <strong>简体中文</strong>
  </p>

  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/tzzp1224/RepoReaper?style=flat-square&color=blue" alt="License">
  </a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python Version">
  <img src="https://img.shields.io/badge/Model-DeepSeek_V3-673AB7?style=flat-square&logo=openai&logoColor=white" alt="DeepSeek Powered">
  <img src="https://img.shields.io/badge/Agent-ReAct-orange?style=flat-square" alt="Agent Architecture">

  <br>

  <img src="https://img.shields.io/badge/RAG-Hybrid_Search-009688?style=flat-square" alt="RAG">
  <img src="https://img.shields.io/badge/VectorDB-Qdrant-important?style=flat-square" alt="Qdrant">
  <img src="https://img.shields.io/badge/Framework-FastAPI-005571?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Frontend-Vue_3-4FC08D?style=flat-square&logo=vue.js&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">

  <br>
  <br>

  <p>
    <b>👇 在线体验 👇</b>
  </p>
  <p align="center">
    <a href="https://realdexter-reporeaper.hf.space" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🤗%20Hugging%20Face-Global%20Demo-ffd21e?style=for-the-badge&logo=huggingface&logoColor=black" alt="Global Demo" height="45">
    </a>
    &nbsp;&nbsp;&nbsp;
    <a href="https://realdexter.com/" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🚀%20Seoul%20Server-国内优化-red?style=for-the-badge&logo=rocket&logoColor=white" alt="China Demo" height="45">
    </a>
  </p>

  <p align="center">
    <small>
      ⚠️ 中国用户请使用 Seoul Server。如遇限流，建议本地部署。
    </small>
  </p>

  <br>

  <img src="./docs/demo_preview.gif" width="800" style="max-width: 100%; box-shadow: 0 4px 8px rgba(0,0,0,0.1); border-radius: 8px;" alt="RepoReaper Demo">

  <br>
</div>

---

自治型代码审计 Agent：解析任意 GitHub 仓库架构，构建语义缓存，支持即时上下文检索问答。

---

## ✨ 核心特性

| 特性 | 说明 |
|:----|:----|
| **多语言 AST 解析** | Python AST + 正则适配 Java / TS / Go / Rust 等 |
| **混合检索** | Qdrant 向量 + BM25 关键词，RRF 融合排序 |
| **JIT 动态加载** | 问答时自动拉取缺失文件 |
| **查询重写** | 自然语言 → 代码检索关键词 |
| **端到端追踪** | Langfuse 集成，全链路可观测 |
| **自动评估** | LLM-as-Judge 质量评分 |

---

## 🏗 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  Vue 3 前端 (SSE 流式 + Mermaid 架构图)                       │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│  FastAPI 后端                                               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │ Agent       │ │ Chat        │ │ Evaluation          │   │
│  │ Service     │ │ Service     │ │ Framework           │   │
│  └──────┬──────┘ └──────┬──────┘ └─────────────────────┘   │
│         │               │                                   │
│  ┌──────▼───────────────▼──────┐  ┌─────────────────────┐  │
│  │ Vector Service (Qdrant+BM25)│  │ Tracing (Langfuse)  │  │
│  └─────────────────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠 技术栈

**后端:** Python 3.10+ · FastAPI · AsyncIO · Qdrant · BM25  
**前端:** Vue 3 · Pinia · Mermaid.js · SSE  
**模型:** DeepSeek V3 · SiliconFlow BGE-M3  
**运维:** Docker · Gunicorn · Langfuse

---

## 🏁 快速开始

**前置要求:** Python 3.10+ · GitHub Token · LLM API Key

```bash
# 克隆 & 安装
git clone https://github.com/tzzp1224/RepoReaper.git && cd RepoReaper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 配置 .env
cat > .env << EOF
GITHUB_TOKEN=ghp_xxx
DEEPSEEK_API_KEY=sk-xxx
SILICON_API_KEY=sk-xxx
EOF

# 启动
python -m app.main
```

**Docker 部署:**
```bash
docker build -t reporeaper . && docker run -d -p 8000:8000 --env-file .env reporeaper
```

访问 `http://localhost:8000`，输入任意 GitHub 仓库地址开始审计。

---

## 📈 Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>
