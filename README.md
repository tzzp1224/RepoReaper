<div align="center">

  <img src="./docs/logo.jpg" width="800" style="max-width: 100%;" height="auto" alt="RepoReaper Logo">

  <h1>RepoReaper</h1>

  <h3>💀 Harvest Logic. Dissect Architecture. Chat with Code.</h3>

  <p>
    <a href="./README.md">English</a> • 
    <a href="./README_zh.md">简体中文</a>
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
    <b>👇 Live Demo / 在线体验 👇</b>
  </p>
  <p align="center">
    <a href="https://realdexter-reporeaper.hf.space" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🤗%20Hugging%20Face-Global%20Demo-ffd21e?style=for-the-badge&logo=huggingface&logoColor=black" alt="Global Demo" height="45">
    </a>
    &nbsp;&nbsp;&nbsp;
    <a href="https://realdexter.com/" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🚀%20Seoul%20Server-CN%20Optimized-red?style=for-the-badge&logo=rocket&logoColor=white" alt="China Demo" height="45">
    </a>
  </p>

  <p align="center">
    <small>
      ⚠️ Public demos use shared API quotas. Deploy locally for the best experience.
    </small>
  </p>

  <br>

  <img src="./docs/demo_preview.gif" width="800" style="max-width: 100%; box-shadow: 0 4px 8px rgba(0,0,0,0.1); border-radius: 8px;" alt="RepoReaper Demo">

  <br>
</div>

---

An autonomous Agent that dissects any GitHub repository. It maps code architecture, warms up semantic cache, and answers questions with Just-In-Time context retrieval.

---

## ✨ Key Features

| Feature | Description |
|:--------|:------------|
| **Multi-Language AST Parsing** | Python AST + Regex patterns for Java, TypeScript, Go, Rust, etc. |
| **Hybrid Search** | Qdrant vectors + BM25 with RRF fusion |
| **JIT Context Loading** | Auto-fetches missing files during Q&A |
| **Query Rewrite** | Translates natural language to code keywords |
| **End-to-End Tracing** | Langfuse integration for observability |
| **Auto Evaluation** | LLM-as-Judge scoring pipeline |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Vue 3 Frontend (SSE Streaming + Mermaid Diagrams)          │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│  FastAPI Backend                                            │
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

## 🛠 Tech Stack

**Backend:** Python 3.10+ · FastAPI · AsyncIO · Qdrant · BM25  
**Frontend:** Vue 3 · Pinia · Mermaid.js · SSE  
**LLM:** DeepSeek V3 · SiliconFlow BGE-M3  
**Ops:** Docker · Gunicorn · Langfuse

---

## 🏁 Quick Start

**Prerequisites:** Python 3.10+ · GitHub Token · LLM API Keys

```bash
# Clone & Setup
git clone https://github.com/tzzp1224/RepoReaper.git && cd RepoReaper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure .env
cat > .env << EOF
GITHUB_TOKEN=ghp_xxx
DEEPSEEK_API_KEY=sk-xxx
SILICON_API_KEY=sk-xxx
EOF

# Run
python -m app.main
```

**Docker:**
```bash
docker build -t reporeaper . && docker run -d -p 8000:8000 --env-file .env reporeaper
```

Open `http://localhost:8000` and paste any GitHub repo URL.





## 📈 Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>