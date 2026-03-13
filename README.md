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
    <a href="https://repo.realdexter.com/" target="_blank" rel="noopener noreferrer">
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

**Prerequisites:** Python 3.10+ · (Optional) Node 18+ for rebuilding frontend · GitHub Token (recommended) · LLM API Key (required)

```bash
# Clone & Setup
git clone https://github.com/tzzp1224/RepoReaper.git && cd RepoReaper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure .env (copy from example and fill in your keys)
cp .env.example .env
# Required: set LLM_PROVIDER and the matching *_API_KEY
# Recommended: GITHUB_TOKEN and SILICON_API_KEY (embeddings)

# Langfuse configuration：
# LANGFUSE_ENABLED=true
# LANGFUSE_HOST=http://langfuse-web:3000  #if you run RepoReaper in docker
# LANGFUSE_HOST=http://localhost:3000  #if you run RepoReaper locally
# LANGFUSE_PUBLIC_KEY=<your public key>
# LANGFUSE_SECRET_KEY=<your secret key>

# (Optional) Build frontend (repo already contains frontend-dist)
cd frontend-vue
npm install
npm run build
cd ..

# Run
python -m app.main
```

Open `http://localhost:8000` and paste any GitHub repo URL.

**Docker (single container, local Qdrant):**
```bash
cp .env.example .env
docker build -t reporeaper .
docker run -d -p 8000:8000 --env-file .env reporeaper
```

**Docker Compose (recommended, with Qdrant Server):**
```bash
cp .env.example .env
# Set QDRANT_MODE=server and QDRANT_URL=http://qdrant:6333 in .env
docker compose up -d --build
```
**Using Langfuse:**
```bash
docker compose -f docker-compose.observability.yml up -d --build
```





## 📊 Evaluation & Tracing Status

| Component | Status | Notes |
|:----------|:------:|:------|
| **Self-built Eval Engine** | ✅ Working | 4-layer metrics (QueryRewrite / Retrieval / Generation / Agentic), LLM-as-Judge |
| **Auto Evaluation** | ✅ Working | Triggers after every `/chat`, async, writes to `evaluation/sft_data/` |
| **Data Routing (SFT)** | ✅ Working | Auto-grades Gold/Silver/Bronze/Rejected → JSONL files |
| **Eval API Endpoints** | ✅ Working | `/evaluate`, `/evaluation/stats`, `/dashboard/*`, `/auto-eval/*` (7 endpoints) |
| **Offline Retrieval Eval** | ✅ Working | `test_retrieval.py` — Hit Rate, Recall@K, Precision@K, MRR |
| **Langfuse Tracing** | ⚠️ Partial | Framework + 14 call sites wired in agent/chat services; falls back to local JSON logs (`logs/traces/`) when Langfuse unavailable |
| **Ragas Integration** | ❌ Placeholder | `use_ragas=False` by default; `_ragas_eval()` API call doesn't match latest Ragas SDK |
| **Langfuse ↔ Eval** | ❌ Not connected | Eval results only write JSONL, not reported to Langfuse Scores API |

> **Overall completion: ~65%** — the self-built eval loop is production-ready; Ragas and Langfuse integrations are scaffolded but not functional.

---

## ⚠️ Known Issues

1. **Python 3.14 + Langfuse import error**  
   `pydantic.V1.errors.ConfigError: unable to infer type for attribute "description"` — Langfuse 3.x internally uses `pydantic.v1` compat layer which breaks on Python 3.14.  
   **Workaround:** set `LANGFUSE_ENABLED=false` in `.env`, or use Python 3.10–3.12.

2. **Langfuse Server not included in `docker-compose.yml`**  
   Even if the import works, you need a running Langfuse instance. Add it yourself or use [app.langfuse.com](https://app.langfuse.com).

3. **Trace spans are not linked**  
   `tracing_service` records spans/events but doesn't pass `trace_id` to Langfuse API calls — the Langfuse UI will show isolated events instead of a connected trace tree.

4. **Ragas `_ragas_eval()` uses outdated API**  
   Passes a plain dict to `ragas.evaluate()`, but latest Ragas requires a `Dataset` object. The `ragas_eval_dataset.json` export exists but no script consumes it.

5. **Golden dataset has no reference answers**  
   All 26 test cases have `expected_answer: ""` — generation quality cannot be compared against ground truth.

6. **Heuristic fallback is coarse**  
   When no LLM client is available, `faithfulness` uses keyword overlap + 0.2 baseline; `completeness` is purely length-based.

---

## 🗺 Roadmap

- [ ] **Fix Langfuse compat** — pin `langfuse`/`pydantic` versions or gate import behind Python version check
- [ ] **Add Langfuse to `docker-compose.yml`** — one-command local observability
- [ ] **Wire trace_id through spans** — enable full trace tree in Langfuse UI
- [ ] **Integrate Ragas properly** — update `_ragas_eval()` to use `ragas.evaluate(Dataset(...))`, add a standalone eval script
- [ ] **Enrich golden dataset** — add `expected_answer` for generation benchmarking, expand to 50+ cases
- [ ] **Eval dashboard frontend** — Vue component to visualize quality distribution and bad cases
- [ ] **CI regression baseline** — run `test_retrieval.py` in GitHub Actions, fail on metric regression
- [ ] **Export to Langfuse Datasets** — push eval results to Langfuse Scores/Datasets API for unified observability

---

## 📈 Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>