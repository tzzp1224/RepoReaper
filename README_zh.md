<div align="center">
  <img src="./docs/logo.jpg" width="800" style="max-width: 100%;" alt="RepoReaper Logo" />

  <h1>RepoReaper</h1>
  <p><b>Harvest Logic. Dissect Architecture. Chat with Code.</b></p>

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

  <video src="./docs/demo.mp4" width="800" style="max-width: 100%; border-radius: 8px;" controls loop muted playsinline>
    Your browser does not support the video tag.
  </video>
</div>

RepoReaper 是一个面向 GitHub 仓库的证据化智能分析 Agent，服务于需要快速吃透陌生代码库的工程师、评审者和研究者。它会把仓库沉淀为可复用的调查工作台，让后续追问、评审和验证始终基于同一仓库上下文。它不是一次性总结工具，而是可持续复用的分析、问答、可复现评估与论文对齐工作流。

## 为什么选择 RepoReaper
- **先证据，后结论**：回答和判断都绑定可检索的仓库证据，而不是只给抽象总结。
- **可复用工作台，而非一次性结果**：每个仓库对应持久会话，分析上下文、报告和 artifacts 可持续复用。
- **为复杂问题设计的检索链路**：向量检索 + BM25 + RRF + 查询改写 + JIT 补文件，在首轮证据不足时继续补召。
- **验证与可检查性内建**：Reproducibility Score、Paper Align 与白盒 tracing 让结论可追溯、可复核。

## 你会得到什么
- 一个以仓库为单位的分析工作台，包含可复用会话状态、索引上下文和分析产物。
- 在同一会话中完成架构理解与实现细节追问的 Repo Chat。
- **Issues Notebook** 与 **Commit Roadmap** 两条并行视图，分别观察社区压力与交付方向。
- **Reproducibility Score**：结构化风险、证据引用与本地化摘要输出。
- **Paper Align**：按 claim 输出 `aligned`、`partial`、`missing`、`insufficient_evidence` 判定。
- **Suggested Questions**：返回三类锚定追问，覆盖架构、实现与复现路径。
- 分析、洞察、问答、对齐全链路流式回传，无需等待全量完成再开始判断。

## 实际使用场景
| 场景 | 结果 |
| :-- | :-- |
| 新仓库接手与快速入门 | 分析一次后，持续复用中英报告与索引上下文。 |
| 深入实现细节排查 | Chat 先改写问题并检索关键片段，证据不足时触发 JIT 补文件。 |
| 项目节奏与维护状态审查 | Issues Notebook 与 Commit Roadmap 并行呈现维护压力和交付方向。 |
| 可复现性交接评估 | Reproducibility Score 给出结构化风险、证据引用和本地化摘要。 |
| 论文 claim 与代码实现核验 | Paper Align 对 claim 逐条比对代码证据，并流式暴露 partial/missing。 |
| 规划下一步问题 | Suggested Questions 给出架构、实现、复现三类锚定追问。 |

## RepoReaper 如何工作
- **会话化状态模型**：每个仓库映射到稳定会话，分析上下文、报告和 artifacts 可跨访问复用。
- **分层检索链路**：向量检索与 BM25 通过 RRF 融合，再结合查询改写与 JIT 补文件，提升复杂问题的证据召回。
- **流式执行为默认**：分析、洞察、问答、对齐按阶段回传，用户可边看边判断。
- **显式缓存层次**：issues/roadmap/questions 等产物可缓存复用；可复现评分采用 `core` 与 localized 分层缓存。
- **并发写入可控**：仓库级锁支持 `memory` / `file` / `redis` 后端，避免同会话写冲突。
- **模型层可插拔**：可在 `openai`、`deepseek`、`anthropic`、`gemini` 间切换，不改变工作流语义。

## 可观测与质量闭环
- **请求路径可追踪**：核心 chat 会话绑定 tracing，分析过程可检查而非黑箱。
- **评估不阻塞主链路**：auto-eval 以 sidecar 异步执行，回答延迟与评分延迟解耦。
- **质量判断可复核**：异常评估样本进入 review queue，支持稳定的 approve/reject 操作。
- **运行态指标可见**：可观测指标与评估统计反映队列状态、分数分布和失败面。
- **失败默认 fail-open**：观测或评估异常不会阻断主分析与问答流程。

## Paper Align 在评审中的用法
- **先拆 claim**：将论文文本拆解为可核验的技术 claims。
- **逐 claim 证据检索**：每个 claim 扩展为检索友好查询，并与仓库片段匹配。
- **判定结果有边界**：明确区分 `aligned`、`partial`、`missing`、`insufficient_evidence`，并给出证据摘录。
- **困难样本可回退**：证据不足时触发候选文件 JIT 拉取与再判定，而不是直接终止。
- **诊断过程可流式观察**：claim 进度、检索轨迹、回退动作与最终置信度都可被检查。

## 快速开始
### 本地运行（最短路径）
前置：Python `3.10-3.12`、至少一个 LLM API Key（必需）、`GITHUB_TOKEN`（推荐）、Embedding Key（推荐）。

```bash
git clone https://github.com/tzzp1224/RepoReaper.git
cd RepoReaper

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 必填：LLM_PROVIDER + 对应 API Key
# 支持：openai | deepseek | anthropic | gemini
# 推荐：GITHUB_TOKEN 与 SILICON_API_KEY
# 可选：LANGFUSE_ENABLED=true + Langfuse keys

python3 -m app.main
```

打开 [http://localhost:8000](http://localhost:8000)。

### Docker Compose（App + Qdrant）
```bash
cp .env.example .env
docker compose up -d --build
```

### 可选观测栈（Langfuse）
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

兼容性提示：
- 推荐 Python `3.10-3.12`。
- Python `3.14` 当前有已知兼容风险（Langfuse SDK 与部分 legacy asyncio 测试模式）。

## Star History

<a href="https://star-history.com/#tzzp1224/RepoReaper&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=tzzp1224/RepoReaper&type=Date" />
 </picture>
</a>
