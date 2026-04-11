# Part C —— 可复现评分 & 论文-代码对齐

> Owner：成员 C  
> 依赖：已完成 `/analyze` 的仓库 session  
> 契约版本：V1.1（`docs/development_contract_v1.md` §3.2 / §3.3）

---

## 1. 文件清单

| 文件 | 作用 |
|------|------|
| `app/schemas/repro.py` | 数据模型：`ReproScoreResult`、`PaperAlignResult` 等 |
| `app/services/repro_score_service.py` | 可复现评分核心逻辑 |
| `app/services/paper_align_service.py` | 论文-代码对齐核心逻辑 |
| `app/main.py`（增量） | 路由 `POST /api/repro/score`、`POST /api/paper/align` |
| `tests/conftest.py` | 测试 stub（qdrant_client 等离线 mock） |
| `tests/test_part_c.py` | 单元测试（20 条，全 mock，离线可跑） |

---

## 2. 接口说明

### 2.1 `POST /api/repro/score` — 可复现性评分

**请求**

```json
{
  "session_id": "repo_xxx",
  "repo_url": "https://github.com/owner/repo",
  "since_days": 90,
  "limit": 100
}
```

- `session_id` / `repo_url` 至少提供一个（两者都给以 `session_id` 优先）。
- `repo_url` 也接受 `url` 作为别名。
- `since_days`（可选，默认 90，1–365）、`limit`（可选，默认 100，1–500）：控制拉取 GitHub insight 的时间窗与条数，与 `POST /api/repo/insight/issues-commits` 语义一致；非法值回退默认。

**响应**

```json
{
  "status": "success",
  "data": {
    "overall_score": 72,
    "overall_score_raw": 0.72,
    "level": "medium",
    "quality_tier": "silver",
    "dimension_scores": {
      "code_structure": 80,
      "docs_quality": 68,
      "env_readiness": 70,
      "community_stability": 66
    },
    "dimension_scores_raw": {
      "code_structure": 0.8,
      "docs_quality": 0.68,
      "env_readiness": 0.7,
      "community_stability": 0.66
    },
    "risks": [
      {
        "title": "CUDA version conflict",
        "reason": "multiple open issues about dependency conflicts",
        "evidence_refs": ["issue#123", "Dockerfile"]
      }
    ],
    "evidence_refs": ["README.md", "requirements.txt", "Dockerfile"],
    "summary": "Repository is partially reproducible with moderate setup risk."
  },
  "error": null
}
```

**评分逻辑**

| 层级 | 说明 |
|------|------|
| **规则层** | 用正则扫描 `file_tree`，按信号权重累加四维度分数（0-1）。 |
| **LLM 层** | 发送 `file_tree` + 分析报告给 LLM，返回 `risks` 列表和 `summary`。 |
| **降级** | LLM 不可用时仍返回规则层打分，`summary` 标记为 unavailable。 |

四维度说明：

| 维度 | 主要检测信号 |
|------|-------------|
| `code_structure` | src/lib 目录、入口文件、测试目录、CI、构建脚本、manifest |
| `docs_quality` | README、LICENSE、docs 目录、CONTRIBUTING、examples |
| `env_readiness` | 依赖锁文件、Dockerfile、.env.example、安装脚本 |
| `community_stability` | issue/PR 模板、CONTRIBUTING、CI、SECURITY、CHANGELOG |

---

### 2.2 `POST /api/paper/align` — 论文-代码对齐

**请求**

```json
{
  "session_id": "repo_xxx",
  "paper_text": "We propose a two-stage retrieval pipeline...",
  "top_k": 5
}
```

- `paper_text`：必填。
- `session_id` / `repo_url`：至少提供一个。
- `top_k`：可选，默认 5，范围 1-20。

**响应**

```json
{
  "status": "success",
  "data": {
    "alignment_items": [
      {
        "claim": "The system uses a two-stage retrieval pipeline.",
        "status": "aligned",
        "matched_files": ["src/model.py"],
        "matched_symbols": ["TwoStageRetriever", "search_hybrid"],
        "evidence_excerpt": "class TwoStageRetriever … def search_hybrid"
      }
    ],
    "missing_claims": [
      {
        "claim": "Ablation on model X",
        "reason": "no direct implementation evidence found"
      }
    ],
    "confidence": 0.78
  },
  "error": null
}
```

**对齐逻辑**

```
paper_text ──▶ LLM 拆 claim（≤10 条）
                 │
                 ▼
         逐条 search_hybrid 检索代码片段
                 │
                 ▼
         LLM 判定 aligned / partial / missing
                 │
                 ▼
         汇总 → confidence = Σ(aligned×1 + partial×0.5) / total
```

---

## 3. 错误响应

所有新接口使用统一错误格式：

```json
{
  "status": "error",
  "data": null,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "session_id or repo_url is required",
    "details": {}
  }
}
```

常见 `code` 值：

| code | 场景 |
|------|------|
| `INVALID_ARGUMENT` | 缺少必填参数、session 未分析 |
| `INTERNAL` | 服务内部异常 |

---

## 4. 如何测试

### 4.1 离线单元测试（推荐，无需 API Key / 数据库）

```bash
# 在项目根目录
pip install pytest          # 如未安装
python -m pytest tests/test_part_c.py -v
```

**预期输出：20 passed**

测试覆盖：

| 测试类 | 覆盖内容 |
|--------|---------|
| `TestSchemaToDict` (4) | `to_dict()` 字段完整性、`compute_level/tier` 边界 |
| `TestRuleBasedScoring` (3) | 完整仓库高分、空仓库零分、最小仓库部分得分 |
| `TestComputeReproScore` (5) | 正常结果、契约字段、无上下文报错、repo_url 解析、参数缺失 |
| `TestComputePaperAlignment` (6) | 正常结果、契约字段、confidence 计算、空 paper、无上下文、top_k clamp |
| `TestLLMFallback` (2) | LLM 不可用时评分降级、对齐降级 |

### 4.2 集成测试（需要已分析的仓库 session）

1. 先正常 analyze 一个仓库（如 RepoReaper 自身）：
   - 访问 `http://localhost:8000`
   - 输入 `https://github.com/tzzp1224/RepoReaper` 并完成分析

2. 测试评分接口：

```bash
curl -X POST http://localhost:8000/api/repro/score \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/tzzp1224/RepoReaper"}'
```

3. 测试对齐接口：

```bash
curl -X POST http://localhost:8000/api/paper/align \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/tzzp1224/RepoReaper",
    "paper_text": "The system uses hybrid retrieval combining vector search and BM25 with RRF fusion. It supports multi-language AST parsing for Python, Java, and TypeScript.",
    "top_k": 5
  }'
```

---

## 5. Mock 数据说明

`tests/test_part_c.py` 内含完整的 mock 数据，可直接作为前端/联调的参考：

| 常量 | 模拟内容 |
|------|---------|
| `MOCK_FILE_TREE` | 一个典型 ML 仓库的文件树 |
| `MOCK_REPORT` | 分析报告文本 |
| `MOCK_SESSION_CONTEXT` | `load_context()` 的完整返回值 |
| `MOCK_SEARCH_RESULTS` | `search_hybrid()` 返回的代码片段列表 |
| `LLM_RISK_RESPONSE` | LLM 对评分请求的 JSON 回复 |
| `LLM_CLAIMS_RESPONSE` | LLM 拆 claim 的 JSON 回复 |
| `LLM_JUDGE_ALIGNED/PARTIAL/MISSING` | LLM 对齐判定的三种回复 |

前端可直接使用上述 mock 的 response 结构进行开发。

---

## 6. 与其他成员的对接点

| 对接方 | 内容 |
|--------|------|
| **成员 A** | 路由已预注册；A 可将 `_unified_success/_unified_error` 提取到公共模块 |
| **成员 B** | `community_stability` 维度可接入 B 的 `IssueCommitInsight` 增强打分 |
| **成员 D** | 前端按 §2 的 response 结构渲染评分卡和对齐结果 |

---

## 7. 测试结果

```
tests/test_part_c.py::TestSchemaToDict::test_repro_score_result_to_dict_has_all_keys    PASSED
tests/test_part_c.py::TestSchemaToDict::test_paper_align_result_to_dict_has_all_keys    PASSED
tests/test_part_c.py::TestSchemaToDict::test_compute_level_boundaries                   PASSED
tests/test_part_c.py::TestSchemaToDict::test_compute_tier_boundaries                    PASSED
tests/test_part_c.py::TestRuleBasedScoring::test_full_repo_scores_high                  PASSED
tests/test_part_c.py::TestRuleBasedScoring::test_empty_tree_scores_zero                 PASSED
tests/test_part_c.py::TestRuleBasedScoring::test_minimal_repo                           PASSED
tests/test_part_c.py::TestComputeReproScore::test_returns_valid_result                  PASSED
tests/test_part_c.py::TestComputeReproScore::test_to_dict_matches_contract              PASSED
tests/test_part_c.py::TestComputeReproScore::test_no_context_raises                     PASSED
tests/test_part_c.py::TestComputeReproScore::test_repo_url_resolves_session             PASSED
tests/test_part_c.py::TestComputeReproScore::test_missing_both_raises                   PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_returns_valid_result              PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_to_dict_matches_contract          PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_confidence_calculation            PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_empty_paper_text_raises           PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_no_context_raises                 PASSED
tests/test_part_c.py::TestComputePaperAlignment::test_top_k_clamp                       PASSED
tests/test_part_c.py::TestLLMFallback::test_score_without_llm                           PASSED
tests/test_part_c.py::TestLLMFallback::test_align_without_llm                           PASSED

======================== 20 passed in 0.25s =========================
```
