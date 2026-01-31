# evaluation/__init__.py
"""
Evaluation 模块

提供完整的评估框架，包括：
- 数据模型 (models.py)
- 评估引擎 (evaluation_framework.py)
- 数据路由 (data_router.py)
- 工具函数 (utils.py)
- 数据分析 (analyze_eval_results.py)
- 数据清洗 (clean_and_export_sft_data.py)

使用示例:
    from evaluation import EvaluationEngine, DataRoutingEngine, EvaluationResult
    from evaluation.models import GenerationMetrics
"""

# 核心导出
from evaluation.models import (
    EvaluationLayer,
    DataQualityTier,
    QueryRewriteMetrics,
    RetrievalMetrics,
    GenerationMetrics,
    AgenticMetrics,
    EvaluationResult,
)

from evaluation.data_router import DataRoutingEngine
from evaluation.evaluation_framework import EvaluationEngine

# 工具函数
from evaluation.utils import (
    is_chatty_query,
    has_code_indicators,
    read_jsonl,
    append_jsonl,
    safe_truncate,
    smart_truncate,
    SFTLengthConfig,
)

__all__ = [
    # 枚举
    "EvaluationLayer",
    "DataQualityTier",
    # 数据模型
    "QueryRewriteMetrics",
    "RetrievalMetrics", 
    "GenerationMetrics",
    "AgenticMetrics",
    "EvaluationResult",
    # 引擎
    "EvaluationEngine",
    "DataRoutingEngine",
    # 工具函数
    "is_chatty_query",
    "has_code_indicators",
    "read_jsonl",
    "append_jsonl",
    "safe_truncate",
    "smart_truncate",
    "SFTLengthConfig",
]
