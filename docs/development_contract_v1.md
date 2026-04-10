# RepoReaper 开发主协议（V1.1）

> 生效日期：2026-04-10  
> 本文档是当前阶段（时间受限）唯一开发基线。后续开发、联调、验收均以本文档为准。  
> 说明：本文档定义的是“未来开发契约”，不代表当前仓库已经全部实现。

## 1. 目标与边界

### 1.1 本阶段只做 3 个目标
1. **可复现性评分**
2. **提取 Issues + Commits**，说明当前存在问题与最近新增 feat
3. **论文-代码对齐**

### 1.2 本阶段明确不做
1. 成本守卫（token/cost hard guard）
2. CI 验收门禁扩展

说明：成本与运行监控由 Langfuse 负责，本阶段不新增成本控制功能。

---

## 2. 四人分工（固定 Owner 制）

| 成员 | 主目标 | Owner 范围（只改这些） | 交付物 | 不负责 |
|---|---|---|---|---|
| 成员 A（后端总线） | 串起 3 条主线 | `app/main.py` + 新增 schema/路由编排文件 | 3 个 API 路由、统一响应结构、统一错误返回 | 不写评分算法、不写前端 |
| 成员 B（Issues/Commits） | 抽取问题与近期新增 feat | `app/utils/github_client.py`、`app/services/github_service.py` | `IssueCommitInsight` 结构化结果 | 不做评分、不做 UI |
| 成员 C（评分 + 对齐） | 可复现评分与论文代码对齐 | 新增评分/对齐服务模块 | `ReproScoreResult`、`PaperAlignResult` | 不改 GitHub 抽取层 |
| 成员 D（前端展示） | 报告页结果可视化 | `frontend-vue/src/*` | 评分卡、Issues/Commits 洞察、论文对齐展示 | 不改后端业务逻辑 |

---

## 3. 接口契约（V1.1）

## 3.0 通用规则（新增接口必须遵守）

1. 新增接口统一挂在 `/api/*` 下。  
2. 新增接口统一使用 `POST + application/json`（避免 `GET` 携带 body 的兼容问题）。  
3. 所有时间字段使用 ISO 8601 UTC（例如 `2026-04-01T10:00:00Z`）。  
4. 兼容入参：`repo_url` 为标准字段，同时服务端必须兼容 `url`（作为别名）直到 V2。  
5. `status` 仅允许：`success` / `error`。  

### 统一成功响应

```json
{
  "status": "success",
  "data": {},
  "error": null
}
```

### 统一错误响应

```json
{
  "status": "error",
  "data": null,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "repo_url is required",
    "details": {}
  }
}
```

---

## 3.1 `POST /api/repo/insight/issues-commits`

- **Owner**：成员 B（成员 A 负责挂到统一路由）
- **用途**：提取仓库问题信号与近期新增 feat

### Request

```json
{
  "repo_url": "https://github.com/owner/repo",
  "since_days": 90,
  "limit": 100
}
```

字段约束：
1. `repo_url`：必填（服务端同时兼容 `url`）  
2. `since_days`：可选，默认 `90`，范围 `1-365`  
3. `limit`：可选，默认 `100`，范围 `1-500`  

### Response

```json
{
  "status": "success",
  "data": {
    "issue_risks": [
      {
        "id": 123,
        "title": "cannot reproduce on cuda 12",
        "url": "https://github.com/owner/repo/issues/123",
        "labels": ["bug", "reproducibility"],
        "risk_type": "repro_env",
        "severity": "high"
      }
    ],
    "recent_feats": [
      {
        "sha": "abc1234",
        "message": "feat: add new training pipeline",
        "author": "dev1",
        "date": "2026-04-01T10:00:00Z",
        "category": "feature"
      }
    ],
    "stats": {
      "issues_total_scanned": 100,
      "commits_total_scanned": 100,
      "risk_issue_count": 12,
      "recent_feat_count": 8
    }
  },
  "error": null
}
```

---

## 3.2 `POST /api/repro/score`

- **Owner**：成员 C（成员 A 负责挂到统一路由）
- **用途**：输出仓库可复现性评分与风险解释

### Request

```json
{
  "session_id": "repo_xxx",
  "repo_url": "https://github.com/owner/repo"
}
```

字段约束：
1. `session_id`、`repo_url` 至少提供一个。  
2. 如仅提供 `repo_url`，服务端可生成对应 `session_id`。  

### Response

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
        "title": "environment mismatch reports",
        "reason": "multiple open issues about dependency conflicts",
        "evidence_refs": ["issue#123", "issue#141"]
      }
    ],
    "evidence_refs": [
      "README.md",
      "requirements.txt",
      "issue#123",
      "commit:abc1234"
    ],
    "summary": "Repository is partially reproducible with moderate setup risk."
  },
  "error": null
}
```

评分语义：
1. `overall_score_raw`：`0-1`（内部统一计算尺度）  
2. `overall_score`：`0-100`（对外展示尺度，`round(overall_score_raw * 100)`）  
3. `level`：`high (>=80)` / `medium (60-79)` / `low (<60)`  
4. `quality_tier`：`gold (>=0.9)` / `silver (>=0.7)` / `bronze (>=0.5)` / `rejected (<0.5)`  

---

## 3.3 `POST /api/paper/align`

- **Owner**：成员 C（成员 A 负责挂到统一路由）
- **用途**：对齐论文 claim 与代码实现证据

### Request

```json
{
  "session_id": "repo_xxx",
  "paper_text": "method claims ...",
  "top_k": 5
}
```

字段约束：
1. `paper_text`：必填  
2. `top_k`：可选，默认 `5`，范围 `1-20`  
3. `session_id`：推荐提供；若缺失可由 `repo_url` 推导（如请求中补充 `repo_url`）  

### Response

```json
{
  "status": "success",
  "data": {
    "alignment_items": [
      {
        "claim": "The paper uses a two-stage retrieval pipeline.",
        "status": "aligned",
        "matched_files": ["app/services/vector_service.py"],
        "matched_symbols": ["search_hybrid", "_rrf_fusion"],
        "evidence_excerpt": "..."
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

---

## 3.4 与现有接口共存约束（防止影响当前逻辑）

本阶段新增 3 个接口时，**不得破坏以下已在用链路** 的请求与响应：

1. `POST /api/repo/check`
2. `GET /analyze`（SSE）
3. `POST /chat`（流式响应）
4. `POST /evaluate`
5. `/auto-eval/*`

说明：本阶段是“新增能力”，不是“替换旧链路”。

---

## 4. 模块交付顺序（固定）

1. **B 先完成** `issues/commits insight` 数据结构与抽取逻辑  
2. **C 接入** insight + 现有 analyze 结果，完成可复现评分  
3. **C 完成** 论文-代码对齐  
4. **A 串联** 3 个接口并统一响应  
5. **D 接入前端** 三块结果展示并完成联调

---

## 5. 协作规则（防发散）

1. 所有跨模块数据只允许走本文定义的 V1.1 接口与字段。  
2. 每人默认只改自己 Owner 范围；跨范围改动必须先同步。  
3. 每个 PR 必须包含：
   - 1 个请求示例
   - 1 个响应示例
   - 影响说明（是否改字段）  
4. 每日最小联调链路：  
   `analyze -> insight -> score -> align`
5. 每次联调需额外回归旧链路：  
   `repo/check -> analyze -> chat`

---

## 6. 变更管理（本文档即基线）

1. 如需新增目标或改接口字段，必须先改本文档再改代码。  
2. 接口字段变更需标注 `V1.1 -> V1.x`，并写明兼容策略。  
3. 未写入本文档的需求，不进入本阶段开发范围。  
4. 在进入 V2 之前，`repo_url/url` 双字段兼容不得删除。  
