# 文件路径: evaluation/models.py
"""
评估数据模型定义

将所有数据类和枚举集中管理，保持代码职责清晰
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from enum import Enum
from datetime import datetime

GOLD_MIN_SCORE = 0.9
SILVER_MIN_SCORE = 0.7
BRONZE_MIN_SCORE = 0.5


class EvaluationLayer(Enum):
    """评估层次分类"""
    QUERY_REWRITE = "query_rewrite"
    RETRIEVAL = "retrieval"
    GENERATION = "generation"
    AGENTIC = "agentic"


class DataQualityTier(Enum):
    """数据质量分级 (用于SFT数据路由)"""
    GOLD = "gold"          # 完美样本
    SILVER = "silver"      # 优质样本
    BRONZE = "bronze"      # 可用样本
    REJECTED = "rejected"  # 拒绝样本

    @classmethod
    def from_score(cls, score: float) -> "DataQualityTier":
        """
        根据综合分数统一判定质量等级。
        这是全局唯一的 score -> tier 映射入口，避免阈值漂移。
        """
        if score >= GOLD_MIN_SCORE:
            return cls.GOLD
        if score >= SILVER_MIN_SCORE:
            return cls.SILVER
        if score >= BRONZE_MIN_SCORE:
            return cls.BRONZE
        return cls.REJECTED

    @classmethod
    def min_score_for(cls, tier: "DataQualityTier") -> float:
        """
        返回指定质量等级的分数下界。
        用于对齐离线清洗阈值与在线路由阈值。
        """
        mapping = {
            cls.GOLD: GOLD_MIN_SCORE,
            cls.SILVER: SILVER_MIN_SCORE,
            cls.BRONZE: BRONZE_MIN_SCORE,
            cls.REJECTED: 0.0,
        }
        return mapping[tier]


# ============================================================================
# 各层评估指标
# ============================================================================

@dataclass
class QueryRewriteMetrics:
    """查询重写评估指标"""
    original_query: str
    rewritten_query: str
    language_detected: str
    keyword_coverage: float       # 0-1
    semantic_preservation: float  # 0-1
    diversity_score: float        # 0-1
    
    def overall_score(self) -> float:
        return (
            self.keyword_coverage * 0.4 +
            self.semantic_preservation * 0.4 +
            self.diversity_score * 0.2
        )


@dataclass
class RetrievalMetrics:
    """检索层评估指标"""
    query: str
    top_k: int
    
    # 核心指标
    hit_rate: float
    recall_at_k: float
    precision_at_k: float
    mrr: float  # Mean Reciprocal Rank
    
    # 高级指标
    context_relevance: float
    chunk_integrity: float
    retrieval_latency_ms: float
    
    # 混合检索
    vector_score_avg: float
    bm25_score_avg: float
    
    retrieved_files: List[str] = field(default_factory=list)
    ground_truth_files: List[str] = field(default_factory=list)
    
    def overall_score(self) -> float:
        return (
            self.recall_at_k * 0.3 +
            self.precision_at_k * 0.3 +
            self.context_relevance * 0.25 +
            self.chunk_integrity * 0.15
        )


@dataclass
class GenerationMetrics:
    """生成层评估指标"""
    query: str
    retrieved_context: str
    generated_answer: str
    
    # 核心指标
    faithfulness: float
    answer_relevance: float
    answer_completeness: float
    code_correctness: float
    
    # 可选
    ground_truth_answer: str = ""
    hallucination_count: int = 0
    unsupported_claims: List[str] = field(default_factory=list)
    generated_code_samples: List[str] = field(default_factory=list)
    generation_latency_ms: float = 0
    token_usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    
    def overall_score(self) -> float:
        base_score = (
            self.faithfulness * 0.35 +
            self.answer_relevance * 0.35 +
            self.answer_completeness * 0.2 +
            self.code_correctness * 0.1
        )
        penalty = self.hallucination_count * 0.1
        return max(0, base_score - penalty)


@dataclass
class AgenticMetrics:
    """Agent行为评估指标"""
    query: str
    tool_selection_accuracy: float
    tool_parameter_correctness: float
    
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    steps_taken: int = 0
    unnecessary_steps: int = 0
    backtrack_count: int = 0
    success: bool = True
    early_termination: bool = False
    end_to_end_latency_ms: float = 0
    
    def efficiency_score(self) -> float:
        if self.steps_taken == 0:
            return 0
        redundancy_ratio = self.unnecessary_steps / self.steps_taken
        return max(0, min(1, 1 - redundancy_ratio - self.backtrack_count * 0.1))
    
    def overall_score(self) -> float:
        return (
            self.tool_selection_accuracy * 0.4 +
            self.tool_parameter_correctness * 0.3 +
            self.efficiency_score() * 0.2 +
            (1.0 if self.success else 0.0) * 0.1
        )


# ============================================================================
# 综合评估结果
# ============================================================================

@dataclass
class EvaluationResult:
    """单次评估完整结果"""
    session_id: str
    query: str
    repo_url: str
    timestamp: datetime
    language: str = "en"
    
    # 各层评估结果
    query_rewrite_metrics: Optional[QueryRewriteMetrics] = None
    retrieval_metrics: Optional[RetrievalMetrics] = None
    generation_metrics: Optional[GenerationMetrics] = None
    agentic_metrics: Optional[AgenticMetrics] = None
    
    # 综合评分
    overall_score: float = 0.0
    data_quality_tier: DataQualityTier = DataQualityTier.BRONZE
    
    # SFT标注
    sft_ready: bool = False
    
    # 元数据
    error_message: Optional[str] = None
    notes: str = ""

    def apply_overall_score(self, score: float) -> float:
        """
        应用综合分数并同步衍生字段（tier / sft_ready）。
        """
        self.overall_score = max(0.0, min(1.0, score))
        self.data_quality_tier = DataQualityTier.from_score(self.overall_score)
        self.sft_ready = self.data_quality_tier in {DataQualityTier.GOLD, DataQualityTier.SILVER}
        return self.overall_score
    
    def compute_overall_score(self) -> float:
        """计算加权综合得分"""
        scores, weights = [], []
        
        if self.query_rewrite_metrics:
            scores.append(self.query_rewrite_metrics.overall_score())
            weights.append(0.15)
        
        if self.retrieval_metrics:
            scores.append(self.retrieval_metrics.overall_score())
            weights.append(0.35)
        
        if self.generation_metrics:
            scores.append(self.generation_metrics.overall_score())
            weights.append(0.4)
        
        if self.agentic_metrics:
            scores.append(self.agentic_metrics.overall_score())
            weights.append(0.1)
        
        if not scores:
            return 0.0
        
        total_weight = sum(weights)
        computed_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        return self.apply_overall_score(computed_score)
    
    def to_dict(self) -> Dict:
        """转换为字典供存储"""
        result = {
            "session_id": self.session_id,
            "query": self.query,
            "repo_url": self.repo_url,
            "timestamp": self.timestamp.isoformat(),
            "language": self.language,
            "overall_score": self.overall_score,
            "data_quality_tier": self.data_quality_tier.value,
            "sft_ready": self.sft_ready,
            "error_message": self.error_message,
            "notes": self.notes,
        }
        
        if self.query_rewrite_metrics:
            query_rewrite = asdict(self.query_rewrite_metrics)
            query_rewrite["overall_score"] = self.query_rewrite_metrics.overall_score()
            result["query_rewrite"] = query_rewrite
        if self.retrieval_metrics:
            retrieval = asdict(self.retrieval_metrics)
            retrieval["overall_score"] = self.retrieval_metrics.overall_score()
            result["retrieval"] = retrieval
        if self.generation_metrics:
            generation = asdict(self.generation_metrics)
            generation["overall_score"] = self.generation_metrics.overall_score()
            result["generation"] = generation
        if self.agentic_metrics:
            agentic = asdict(self.agentic_metrics)
            agentic["overall_score"] = self.agentic_metrics.overall_score()
            result["agentic"] = agentic
        
        return result
