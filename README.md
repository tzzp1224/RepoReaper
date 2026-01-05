<div align="center">

  <img src="./docs/logo.jpg" width="220" height="auto" alt="RepoReaper Logo">

  <h1>RepoReaper</h1>

  <h3>
    💀 Harvest Logic. Dissect Architecture. Chat with Code.
    <br>
    基于 AST 深度解析 · 双语适配的自治型代码审计 Agent
  </h3>

  <p>
    <a href="./README.md">English</a> • 
    <a href="./README_zh.md">简体中文</a>
  </p>
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/yourname/reporeaper?style=flat-square&color=blue" alt="License">
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

  <img src="./docs/demo_preview.gif" width="800" alt="RepoReaper Demo">

  <br>
</div>

---



**An intelligent, agentic system for automated architectural analysis and semantic code search.**

This project transcends traditional "Chat with Code" paradigms by implementing an autonomous Agent that mimics the cognitive process of a Senior Tech Lead. Instead of statically indexing a repository, the system treats the Large Language Model (LLM) as the CPU and the Vector Store as a high-speed **Context Cache**. The agent dynamically traverses the repository structure, pre-fetching critical contexts into the "cache" (RAG) and performing Just-In-Time (JIT) reads when semantic gaps are detected.

---

## 🚀 Core Philosophy: RAG as an Intelligent Cache

In traditional code assistants, RAG (Retrieval-Augmented Generation) is often a static lookup table. In this architecture, we redefine RAG as a **Dynamic L2 Cache** for the LLM:

1.  **Cold Start (Repo Map):** The agent first parses the Abstract Syntax Tree (AST) of the entire repository to build a lightweight symbol map (Classes/Functions). This serves as the "index" to the file system.
2.  **Prefetching (Analysis Phase):** During the initial analysis, the agent autonomously selects the most critical 10-20 files based on architectural relevance, parses them, and "warms up" the vector store (the cache).
3.  **Cache Miss Handling (ReAct Loop):** During user Q&A, if the retrieval mechanism (BM25 + Vector) returns insufficient context, the Agent triggers a **Just-In-Time (JIT)** file read. It autonomously tools the GitHub API to fetch missing files, updates the cache in real-time, and re-generates the answer.

---

## 🏗 System Architecture & Innovations

### 1. AST-Aware Semantic Chunking
Standard text chunking destroys code logic. We utilize Python's `ast` module to implement **Structure-Aware Chunking**.
* **Logical Boundaries:** Code is split by Class and Method definitions, ensuring that a function is never severed in the middle.
* **Context Injection:** Large classes are decomposed into methods, but the parent class's signature and docstrings are injected into every child chunk. This ensures the LLM understands the "why" (class purpose) even when looking at the "how" (method implementation).

### 2. Asynchronous Concurrency Pipeline
Built on top of `asyncio` and `httpx`, the system is designed for high-throughput I/O operations.
* **Non-Blocking Ingestion:** Repository parsing, AST extraction, and vector embedding occur concurrently.
* **Worker Scalability:** The application runs behind Gunicorn with Uvicorn workers, utilizing a stateless design pattern where the Vector Store Manager synchronizes context via persistent disk storage and shared ChromaDB instances. This allows multiple workers to serve requests without race conditions.

### 3. The "Just-In-Time" ReAct Agent
The Chat Service implements a sophisticated **Reasoning + Acting (ReAct)** loop:
* **Query Rewrite:** User queries (often vague or in different languages) are first rewritten by an LLM into precise, English-language technical keywords for optimal BM25/Vector retrieval.
* **Self-Correction:** If the retrieved context is insufficient, the model does not hallucinate. Instead, it issues a `<tool_code>` command to fetch specific file paths from the repository. The system intercepts this command, pulls the fresh data, indexes it, and feeds it back to the model in a single inference cycle.

### 4. Hybrid Search Mechanism
To balance semantic understanding with exact keyword matching, the retrieval engine employs a weighted hybrid approach:
* **Dense Retrieval (Vector):** Uses `BAAI/bge-m3` embeddings to find conceptually similar code (e.g., matching "authentication" to "login logic").
* **Sparse Retrieval (BM25):** Captures exact variable names, error codes, and specific function signatures that vector embeddings might miss.
* **Reciprocal Rank Fusion (RRF):** Results are fused and re-ranked to ensure the highest fidelity context is provided to the LLM.

### 5. Native Bilingual Support
The architecture is completely language-agnostic but optimized for dual-language environments (English/Chinese).
* **Dynamic Prompt Engineering:** The system detects the user's input language and hot-swaps the System Prompts to ensure the output format, tone, and technical terminology align with the user's locale.
* **UI Integration:** The frontend includes a dedicated language toggle that influences the entire generation pipeline, from the initial architectural report to the final Q&A.

---

## 🛠 Technical Stack

* **Core:** Python 3.10+, FastAPI, AsyncIO
* **LLM Integration:** OpenAI SDK (compatible with DeepSeek/SiliconFlow)
* **Vector Database:** ChromaDB (Persistent Storage)
* **Search Algorithms:** BM25Okapi, Rank-BM25
* **Parsing:** Python `ast` (Abstract Syntax Trees)
* **Frontend:** HTML5, Server-Sent Events (SSE) for real-time streaming, Mermaid.js for architecture diagrams.
* **Deployment:** Docker, Gunicorn, Uvicorn.

---

## ⚡ Performance Optimization

* **Session Management:** Uses browser `sessionStorage` coupled with server-side persistent contexts, allowing users to refresh pages without losing the "warm" cache state.
* **Network Resilience:** Implements robust error handling for GitHub API rate limits (403/429) and network timeouts during long-context generation.
* **Memory Efficiency:** The `VectorStoreManager` is designed to be stateless in memory but stateful on disk, preventing memory leaks in long-running container environments.

---

## 🏁 Quick Start

**Prerequisites:** Python 3.9+ and a GitHub Token.

1.  **Clone the Repository**
    ```bash
    git clone [repo_url]
    cd [repo_name]
    ```

2.  **Environment Setup**
    Configure your `.env` file with `GITHUB_TOKEN` and `SILICON_API_KEY` (or other LLM provider keys).

3.  **Run with Gunicorn (Production Mode)**
    ```bash
    gunicorn -c gunicorn_conf.py app.main:app
    ```

4.  **Access the Dashboard**
    Navigate to `http://localhost:8000`. Enter a GitHub repository URL to trigger the autonomous analysis agent.





\## 📈 Star History <a href="https://star-history.com/#tzzp1224/RepoReaper&Date"> <picture>   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" /> </picture> </a>