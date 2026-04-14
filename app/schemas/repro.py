# -*- coding: utf-8 -*-
"""
可复现评分 & 论文-代码对齐 数据模型

与 docs/development_contract_v1.md §3.2 / §3.3 字段一一对应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ============================================================
# §3.2  POST /api/repro/score
# ============================================================

@dataclass
class ScoreRisk:
    title: str
    reason: str
    evidence_refs: List[str] = field(default_factory=list)


@dataclass
class DimensionScores:
    code_structure: float = 0.0
    docs_quality: float = 0.0
    env_readiness: float = 0.0
    community_stability: float = 0.0


@dataclass
class ReproScoreResult:
    overall_score: int                       # 0-100
    overall_score_raw: float                 # 0-1
    level: str                               # high / medium / low
    quality_tier: str                        # gold / silver / bronze / rejected
    dimension_scores: DimensionScores
    dimension_scores_raw: DimensionScores
    risks: List[ScoreRisk] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    summary: str = ""
    language: str = "en"
    cache_hit: bool = False

    @staticmethod
    def compute_level(raw: float) -> str:
        if raw >= 0.80:
            return "high"
        if raw >= 0.60:
            return "medium"
        return "low"

    @staticmethod
    def compute_tier(raw: float) -> str:
        if raw >= 0.90:
            return "gold"
        if raw >= 0.70:
            return "silver"
        if raw >= 0.50:
            return "bronze"
        return "rejected"

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "overall_score_raw": round(self.overall_score_raw, 4),
            "level": self.level,
            "quality_tier": self.quality_tier,
            "dimension_scores": {
                "code_structure": round(self.dimension_scores.code_structure * 100),
                "docs_quality": round(self.dimension_scores.docs_quality * 100),
                "env_readiness": round(self.dimension_scores.env_readiness * 100),
                "community_stability": round(self.dimension_scores.community_stability * 100),
            },
            "dimension_scores_raw": {
                "code_structure": round(self.dimension_scores_raw.code_structure, 4),
                "docs_quality": round(self.dimension_scores_raw.docs_quality, 4),
                "env_readiness": round(self.dimension_scores_raw.env_readiness, 4),
                "community_stability": round(self.dimension_scores_raw.community_stability, 4),
            },
            "risks": [
                {"title": r.title, "reason": r.reason, "evidence_refs": r.evidence_refs}
                for r in self.risks
            ],
            "evidence_refs": self.evidence_refs,
            "summary": self.summary,
            "language": self.language,
            "cache_hit": self.cache_hit,
        }


# ============================================================
# §3.3  POST /api/paper/align
# ============================================================

@dataclass
class AlignmentItem:
    claim: str
    status: str                              # aligned / partial / missing / insufficient_evidence
    matched_files: List[str] = field(default_factory=list)
    matched_symbols: List[str] = field(default_factory=list)
    evidence_excerpt: str = ""
    evidence_spans: List[dict] = field(default_factory=list)
    debug_info: Optional[dict] = None


@dataclass
class MissingClaim:
    claim: str
    reason: str = ""
    status: str = "missing"
    debug_info: Optional[dict] = None


@dataclass
class PaperAlignResult:
    alignment_items: List[AlignmentItem] = field(default_factory=list)
    missing_claims: List[MissingClaim] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "alignment_items": [
                {
                    "claim": a.claim,
                    "status": a.status,
                    "matched_files": a.matched_files,
                    "matched_symbols": a.matched_symbols,
                    "evidence_excerpt": a.evidence_excerpt,
                    "evidence_spans": a.evidence_spans,
                    "debug_info": a.debug_info,
                }
                for a in self.alignment_items
            ],
            "missing_claims": [
                {"claim": m.claim, "reason": m.reason, "status": m.status, "debug_info": m.debug_info}
                for m in self.missing_claims
            ],
            "confidence": round(self.confidence, 4),
        }
