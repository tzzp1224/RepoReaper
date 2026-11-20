<div align="center">
  <img src="./docs/logo.jpg" width="800" style="max-width: 100%;" alt="RepoReaper Logo" />

  <h1>RepoReaper</h1>
  <p><b>Harvest Logic. Dissect Architecture. Chat with Code.</b></p>
  <p><b>NUS CS5260 Project (Spring 2026)</b></p>

  <p>
    <a href="./README.md">English</a> •
    <a href="./README_zh.md">简体中文</a>
  </p>

  <p>
    <img src="https://img.shields.io/github/license/tzzp1224/RepoReaper?style=flat-square&color=blue" alt="License" />
    <img src="https://img.shields.io/badge/Python-3.10--3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/Backend-FastAPI-005571?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/Frontend-Vue_3-4FC08D?style=flat-square&logo=vue.js&logoColor=white" alt="Vue 3" />
    <img src="https://img.shields.io/badge/Search-Qdrant+BM25-009688?style=flat-square" alt="Hybrid Search" />
  </p>

  <p>
    <a href="https://realdexter-reporeaper.hf.space" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🤗%20Hugging%20Face-Global%20Demo-ffd21e?style=for-the-badge&logo=huggingface&logoColor=black" alt="Global Demo" height="42" />
    </a>
    <a href="https://repo.realdexter.com/" target="_blank" rel="noopener noreferrer">
      <img src="https://img.shields.io/badge/🚀%20Seoul%20Server-CN%20Optimized-red?style=for-the-badge&logo=rocket&logoColor=white" alt="Seoul Demo" height="42" />
    </a>
  </p>

  <img src="./docs/new_demo.gif" width="800" style="max-width: 100%; border-radius: 8px;" alt="RepoReaper Demo" />
</div>

RepoReaper is an evidence-grounded repository intelligence agent for engineers, reviewers, and researchers who need to understand unfamiliar codebases quickly. It turns a GitHub repository into a reusable investigation workspace, so follow-up questions and verification stay anchored to the same context. Instead of a one-shot summary, you get a persistent workflow for analysis, chat, reproducibility checks, and paper-to-code validation.

## Why RepoReaper
- **Evidence-grounded by default**: answers and judgments are tied to retrievable repository evidence, not free-form summaries.
- **Reusable workspace, not one-shot output**: each repository maps to a persistent session with reusable context, reports, and artifacts.
- **Retrieval built for hard questions**: vector search + BM25 + RRF + query rewrite + JIT file loading recover missing evidence when first-pass context is thin.
- **Verification and inspectability built in**: Reproducibility Score, Paper Align, and white-box tracing make conclusions reviewable.

## What You Get
- A repository-scoped analysis workspace with reusable session state, indexed context, and generated artifacts.
- Repo chat for architecture exploration and implementation-level Q&A inside the same repository session.
- **Issues Notebook** and **Commit Roadmap** for two complementary views: community pressure and delivery direction.
- **Reproducibility Score** with structured risks, evidence references, and localized summary output.
- **Paper Align** outputs with claim-level verdicts: `aligned`, `partial`, `missing`, and `insufficient_evidence`.
- **Suggested Questions** with three anchored follow-ups: architecture, implementation, and reproduction path.
- Streaming output across analysis, insights, chat, and alignment so users can inspect progress before completion.

## Practical Use Cases
| Scenario | Outcome |
| :-- | :-- |
| Onboarding to a new repository | Analyze once, then reuse bilingual reports and indexed context for later deep dives. |
| Investigating implementation details | Chat gathers original source spans from one commit and links citations to their exact lines. |
| Reviewing project trajectory | Issues Notebook and Commit Roadmap reveal maintenance pressure and delivery direction in parallel. |
| Preparing a reproducibility handoff | Reproducibility Score provides structured risks, evidence references, and localized summary output. |
| Verifying paper claims against code | Paper Align checks claims against code evidence and surfaces missing or partial support with stream diagnostics. |
| Planning next questions | Suggested Questions proposes three anchored follow-ups for architecture, implementation, and reproduction. |

## Key Features

- **📄 Paper-to-Code Alignment**: Upload a research paper (PDF) and automatically verify if the repository implementation matches the claims and algorithms described in the paper.
- **🎯 Reproducibility Scoring**: Automatically evaluates the repository's documentation, environment setup, and code structure to generate a comprehensive reproducibility score.
- **💬 Interactive Codebase Chat**: Ask specific questions about the codebase using a context-aware Retrieval-Augmented Generation (RAG) system based on Qdrant.
- **📊 Issue & Commit Insights**: Summarize repository activity, track feature evolution, and analyze issue resolutions.
- **🗺️ Architecture & Roadmap**: Automatically generate Mermaid.js diagrams for code architecture and development roadmaps.
- **🧠 Multi-LLM Support**: Seamlessly switch between OpenAI, Gemini, Anthropic (Claude), and DeepSeek models.
- **👁️ Observability & Tracing**: Built-in LLM tracing and evaluation observability to monitor token usage and prompt chains (supported via dedicated Docker Compose profiles).

## How RepoReaper Works
- **Commit-pinned analysis**: refreshing the repository reuses unchanged chunks and their vectors. The new index and report become visible together. Refreshing an unchanged commit reuses its existing report.
- **Evidence retrieval**: Chat combines exact paths, symbols, and hybrid search. It reads bounded original source spans on demand and streams a single answer. It stores recent turns separately for each browser session, repository, and commit.
- **Streaming-first execution**: analysis, insights, chat, and alignment emit incremental output so users can inspect progress before full completion.
- **Explicit cache hierarchy**: issues/roadmap/questions artifacts and split reproducibility caches (`core` + localized) reduce repeat latency while preserving refresh control.
- **Concurrency-safe writes**: repository-level locks with `memory` / `file` / `redis` backends prevent conflicting writes on the same session.
- **Provider-pluggable inference layer**: model provider is configurable (`openai`, `deepseek`, `anthropic`, `gemini`) without changing workflow semantics.

## Observability and Quality Loop
- **Traceable request path**: tracing is attached to core chat sessions, so investigation steps remain inspectable instead of opaque.
- **Non-blocking evaluation**: auto-evaluation runs in async sidecar mode, separating answer latency from scoring latency.
- **Reviewable quality control**: suspicious evaluation samples enter a review queue with stable approve/reject actions.
- **Operational visibility**: runtime metrics and evaluation stats expose queue state, score distribution, and failure surfaces.
- **Fail-open behavior**: observability and evaluation failures do not block the primary analysis and chat workflow.

## Paper Align in Review Work
- **Claim extraction**: paper text is decomposed into verifiable technical claims.
- **Evidence retrieval per claim**: each claim is expanded into retrieval-friendly queries and matched against repository snippets.
- **Explicit judgment states**: outputs separate `aligned`, `partial`, `missing`, and `insufficient_evidence`, with evidence excerpts.
- **Source verification**: on a commit-pinned index, candidate files are read before judgment, with original line ranges and commit details in the evidence.
- **Streaming diagnostics**: claim progress, retrieval traces, fallback actions, and final confidence are emitted as inspectable events.

### Optional Jev routing

Set `TYPESAFE_API_KEY` and `JEV_MODE=shadow` to log Jev's preferred read batch alongside the local choice. `JEV_MODE=active` lets sufficiently confident selections reorder the candidate batches. The default is `off`. Jev receives the question and candidate path metadata, while repository access and file reads stay in RepoReaper. [TypeSafe Choice API](https://docs.typesafe.ai/primitives/choice)

The app reuses Jev connections per worker. Batch ranking gives shadow calls 1.5 seconds and active calls 450 ms.

Set `REPO_EXPLORATION_MODE=jev` and `JEV_MODE=active` to use Jev as the tool router. Jev chooses among semantic search, literal grep, and an incremental source read whenever those actions are available. It can also finish once enough source has been verified. Action and argument questions share the candidate files, line windows, and literals in one API request. RepoReaper builds the options from the question, pinned tree, and earlier tool results, and executes the selected action. The router has up to four decisions and a nine-second total budget, with up to 1.5 seconds per decision. If it cannot establish a usable route, Chat falls back to rule-based retrieval. With `JEV_MODE=shadow`, the router records the first proposed action in the background while Chat uses rule-based retrieval.

### Repository tools and index refresh

The default retrieval path prioritizes implementation files, then reads original source spans. Set `REPO_EXPLORATION_MODE=auto` to let Chat use `find_paths`, `grep`, `search_semantic`, and `read_spans` when initial evidence is incomplete. Tools read from the turn's pinned commit. Git grep uses literal matching, and the GitHub API fallback checks only a small set of candidate files. The explorer has two planning rounds, five tool calls, and a ten-second wall-clock limit.

`POST /api/repo/check` returns the active commit, publication time, and refresh status when the webhook is enabled. Set `REPO_WEBHOOK_SECRET` and `REPO_WEBHOOK_REPOSITORIES` to accept signed `push` deliveries at `POST /api/github/webhook`. The repository list is comma-separated. Only default-branch pushes to allowlisted, already indexed repositories are queued. The worker merges pending refreshes and reads the current remote HEAD. [GitHub webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)

Run `python -m app.snapshot_gc owner/repo` to preview collection of retired vectors and add `--apply` to delete eligible points. By default it retains the two most recent retired versions and vectors retired in the past 24 hours. The app and collection command must share the context directory and Qdrant instance. On POSIX hosts a shared file lock protects in-flight readers and writers.

## Quick Start
### Local (fastest path)
Prerequisites: Python `3.10-3.12`, one LLM API key (required), `GITHUB_TOKEN` (recommended), embedding key (recommended for retrieval quality).

```bash
git clone https://github.com/tzzp1224/RepoReaper.git
cd RepoReaper

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Required: LLM_PROVIDER + matching API key
# Supported providers: openai | deepseek | anthropic | gemini
# Recommended: GITHUB_TOKEN and SILICON_API_KEY
# Optional: LANGFUSE_ENABLED=true with Langfuse keys

python3 -m app.main
```

Open [http://localhost:8000](http://localhost:8000).

### Docker Compose (App + Qdrant)
```bash
cp .env.example .env
docker compose up -d --build
```

### Optional Observability Stack (Langfuse)
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

Compatibility note:
- Recommended runtime: Python `3.10-3.12`.
- Python `3.14` currently has known caveats in this repo (Langfuse SDK compatibility and some legacy asyncio test patterns).

## Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>
