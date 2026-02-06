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

**前置要求:** Python 3.10+ ·（可选）Node 18+ 用于重新构建前端 · GitHub Token（推荐）· LLM API Key（必需）

```bash
# 克隆 & 安装
git clone https://github.com/tzzp1224/RepoReaper.git && cd RepoReaper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 配置 .env（建议从示例复制）
cp .env.example .env
# 必需：设置 LLM_PROVIDER 以及对应的 *_API_KEY
# 推荐：GITHUB_TOKEN 和 SILICON_API_KEY（Embedding）

# （可选）构建前端（仓库已包含 frontend-dist）
cd frontend-vue
npm install
npm run build
cd ..

# 启动
python -m app.main
```

访问 `http://localhost:8000`，输入任意 GitHub 仓库地址开始审计。

**Docker（单容器，本地 Qdrant）：**
```bash
cp .env.example .env
docker build -t reporeaper .
docker run -d -p 8000:8000 --env-file .env reporeaper
```

**Docker Compose（推荐，包含 Qdrant Server）：**
```bash
cp .env.example .env
# 在 .env 中设置 QDRANT_MODE=server 与 QDRANT_URL=http://qdrant:6333
docker compose up -d --build
```

---

## � 评估与追踪现状

| 组件 | 状态 | 说明 |
|:----|:----:|:----|
| **自研评估引擎** | ✅ 可用 | 四层指标（QueryRewrite / Retrieval / Generation / Agentic），LLM-as-Judge 判分 |
| **在线自动评估** | ✅ 可用 | 每次 `/chat` 结束后异步触发，结果写入 `evaluation/sft_data/` |
| **数据路由 (SFT)** | ✅ 可用 | 按评分自动分流 Gold/Silver/Bronze/Rejected → JSONL 文件 |
| **评估 API** | ✅ 可用 | `/evaluate`、`/evaluation/stats`、`/dashboard/*`、`/auto-eval/*` 共 7 个端点 |
| **离线检索评估** | ✅ 可用 | `test_retrieval.py` — Hit Rate、Recall@K、Precision@K、MRR |
| **Langfuse 追踪** | ⚠️ 部分完成 | 框架 + 14 处埋点已就位（agent/chat service）；不可用时自动降级为本地日志 `logs/traces/` |
| **Ragas 集成** | ❌ 占位 | 默认 `use_ragas=False`；`_ragas_eval()` 调用方式与最新 Ragas SDK 不兼容 |
| **Langfuse ↔ 评估** | ❌ 未打通 | 评估结果仅写 JSONL，未上报 Langfuse Scores API |

> **综合完成度约 65%**：自研评估链路已闭环可用；Ragas 与 Langfuse 集成均为半成品。

---

## ⚠️ 已知问题

1. **Python 3.14 + Langfuse 导入报错**  
   `pydantic.V1.errors.ConfigError: unable to infer type for attribute "description"` — Langfuse 3.x 内部依赖 `pydantic.v1` 兼容层，在 Python 3.14 下不兼容。  
   **临时方案：** 在 `.env` 中设置 `LANGFUSE_ENABLED=false`，或使用 Python 3.10–3.12。

2. **`docker-compose.yml` 未包含 Langfuse 服务**  
   即使导入成功，仍需运行中的 Langfuse 实例。请自行添加或使用 [app.langfuse.com](https://app.langfuse.com)。

3. **Trace 链路未关联**  
   `tracing_service` 记录了 span/event，但调用 Langfuse API 时未传 `trace_id`，Langfuse UI 中只能看到孤立事件而非完整链路树。

4. **Ragas `_ragas_eval()` API 过时**  
   当前向 `ragas.evaluate()` 传递 dict，最新 Ragas 要求 `Dataset` 对象。已导出 `ragas_eval_dataset.json` 但无脚本消费它。

5. **黄金数据集缺少标准答案**  
   26 条测试用例的 `expected_answer` 均为空，无法做生成质量的 ground truth 对比。

6. **启发式降级较粗糙**  
   无 LLM client 时，`faithfulness` 用关键词重叠 + 0.2 基础分；`completeness` 纯粹按字数判断。

---

## 🗺 路线图

- [ ] **修复 Langfuse 兼容性** — 固定 `langfuse`/`pydantic` 版本或按 Python 版本门控导入
- [ ] **`docker-compose.yml` 加入 Langfuse** — 一键启动本地可观测平台
- [ ] **串联 trace_id** — 让 Langfuse UI 展示完整链路树
- [ ] **正式接入 Ragas** — 更新 `_ragas_eval()` 使用 `ragas.evaluate(Dataset(...))`，新增独立评估脚本
- [ ] **丰富黄金数据集** — 补充 `expected_answer`，扩展至 50+ 条用例
- [ ] **评估仪表盘前端** — Vue 组件可视化质量分布与 Bad Case
- [ ] **CI 回归基线** — 在 GitHub Actions 中运行 `test_retrieval.py`，指标回退时失败
- [ ] **对接 Langfuse Datasets** — 将评估结果推送到 Langfuse Scores/Datasets API，统一可观测

---

## �📈 Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>
