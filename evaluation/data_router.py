# 文件路径: evaluation/data_router.py
"""
数据路由引擎 - 负责 SFT 数据管理和路由

根据评估结果将样本路由到不同的数据集
"""

import json
import os
from typing import Dict, List, Any

from evaluation.models import EvaluationResult, DataQualityTier
from evaluation.utils import smart_truncate, SFTLengthConfig


class DataRoutingEngine:
    """评估驱动的数据路由引擎"""
    
    # SFT 训练提示词
    SFT_INSTRUCTION = (
        "你是一个专业的GitHub代码仓库分析助手。根据提供的代码上下文，"
        "准确回答用户关于代码实现、架构设计、功能逻辑等问题。"
        "回答时应该：1) 直接引用相关代码 2) 解释代码的工作原理 3) 如有必要，提供代码示例。"
    )
    
    def __init__(self, output_dir: str = "evaluation/sft_data"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        self.positive_samples_file = os.path.join(output_dir, "positive_samples.jsonl")
        self.negative_samples_file = os.path.join(output_dir, "negative_samples.jsonl")
        self.dpo_pairs_file = os.path.join(output_dir, "dpo_pairs.jsonl")
        self.eval_results_file = os.path.join(output_dir, "eval_results.jsonl")
    
    def route_sample(self, eval_result: EvaluationResult) -> str:
        """路由单个样本，返回数据质量等级"""
        if eval_result.overall_score == 0.0:
            eval_result.compute_overall_score()
        
        self.route_data(eval_result)
        return eval_result.data_quality_tier.value
    
    def route_data(self, eval_result: EvaluationResult) -> None:
        """
        根据评估结果路由数据
        
        路由规则:
        - score > 0.9  → Gold   → positive_samples.jsonl
        - score > 0.6  → Silver → positive_samples.jsonl  
        - score > 0.4  → Bronze → negative_samples.jsonl
        - score <= 0.4 → Rejected (不应到达此处，在 auto_eval 中已过滤)
        
        注意: eval_results.jsonl 记录所有通过验证的样本，用于分析和审计
        """
        # 记录所有评估结果（完整审计日志）
        self._append_jsonl(self.eval_results_file, eval_result.to_dict())
        
        # 根据质量分级路由到不同的 SFT 数据文件
        if eval_result.overall_score > 0.9:
            # Gold: 高质量正样本
            sft_sample = self._build_sft_sample(eval_result)
            self._append_jsonl(self.positive_samples_file, sft_sample)
        
        elif eval_result.overall_score > 0.6:
            # Silver: 可用正样本
            sft_sample = self._build_sft_sample(eval_result)
            self._append_jsonl(self.positive_samples_file, sft_sample)
        
        elif eval_result.overall_score > 0.4:
            # Bronze: 负样本，可用于 DPO 或人工修正
            sft_sample = self._build_sft_sample(eval_result, negative=True)
            self._append_jsonl(self.negative_samples_file, sft_sample)
        
        # <= 0.4: 不写入任何 SFT 文件（已在 auto_eval 中被拒绝）
    
    def _build_sft_sample(self, eval_result: EvaluationResult, negative: bool = False) -> Dict:
        """
        构建 SFT 训练样本
        
        长度限制（基于 SFTLengthConfig）:
        - Context: 最大 2500 字符 (~800 tokens)
        - Answer: 最大 3000 字符 (~1000 tokens)
        - 总计: ~2000 tokens，适合 4096 max_length 训练
        """
        if eval_result.generation_metrics is None:
            return {}
        
        cfg = SFTLengthConfig
        
        # 1. 截断 Query
        query = eval_result.query
        if len(query) > cfg.MAX_QUERY_CHARS:
            query = query[:cfg.MAX_QUERY_CHARS] + "..."
        
        # 2. 智能截断 Context（保留开头 70% + 结尾 30%）
        context = eval_result.generation_metrics.retrieved_context
        context = smart_truncate(context, cfg.MAX_CONTEXT_CHARS, keep_ratio=0.7)
        
        # 3. 截断 Answer（保留开头，通常结论在开头）
        answer = eval_result.generation_metrics.generated_answer
        if len(answer) > cfg.MAX_ANSWER_CHARS:
            answer = answer[:cfg.MAX_ANSWER_CHARS] + "\n\n... [回答过长，已截断]"
        
        # 4. 构建 input 并检查总长度
        input_text = f"[用户问题]\n{query}\n\n[代码上下文]\n{context}"
        
        # 如果总长度仍超限，进一步压缩 context
        total_len = len(self.SFT_INSTRUCTION) + len(input_text) + len(answer)
        if total_len > cfg.MAX_TOTAL_CHARS:
            excess = total_len - cfg.MAX_TOTAL_CHARS
            new_context_len = max(500, len(context) - excess)  # 至少保留 500 字符
            context = smart_truncate(
                eval_result.generation_metrics.retrieved_context, 
                new_context_len, 
                keep_ratio=0.7
            )
            input_text = f"[用户问题]\n{query}\n\n[代码上下文]\n{context}"
        
        return {
            "instruction": self.SFT_INSTRUCTION,
            "input": input_text,
            "output": answer,
            "metadata": {
                "query": eval_result.query[:200],  # metadata 中也截断，节省空间
                "repo_url": eval_result.repo_url,
                "language": eval_result.language,
                "session_id": eval_result.session_id,
                "timestamp": eval_result.timestamp.isoformat(),
                "quality_tier": eval_result.data_quality_tier.value,
                "overall_score": eval_result.overall_score,
                "faithfulness": eval_result.generation_metrics.faithfulness,
                "answer_relevance": eval_result.generation_metrics.answer_relevance,
                "answer_completeness": eval_result.generation_metrics.answer_completeness,
                "code_correctness": eval_result.generation_metrics.code_correctness,
                "is_negative": negative,
                "sft_ready": eval_result.sft_ready,
                # 记录原始长度，便于分析
                "original_context_len": len(eval_result.generation_metrics.retrieved_context),
                "original_answer_len": len(eval_result.generation_metrics.generated_answer),
                "truncated": len(eval_result.generation_metrics.retrieved_context) > cfg.MAX_CONTEXT_CHARS
                          or len(eval_result.generation_metrics.generated_answer) > cfg.MAX_ANSWER_CHARS,
            }
        }
    
    def _append_jsonl(self, filepath: str, data: Dict) -> None:
        """追加数据到 JSONL 文件"""
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')
    
    def get_statistics(self) -> Dict[str, int]:
        """获取当前数据统计"""
        stats = {}
        for name, filepath in [
            ("positive", self.positive_samples_file),
            ("negative", self.negative_samples_file),
            ("dpo_pairs", self.dpo_pairs_file),
        ]:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    stats[name] = sum(1 for _ in f)
            else:
                stats[name] = 0
        return stats
    
    def get_distribution(self) -> Dict[str, int]:
        """获取评估结果的质量分布"""
        distribution = {"gold": 0, "silver": 0, "bronze": 0, "rejected": 0, "corrected": 0}
        
        if not os.path.exists(self.eval_results_file):
            return distribution
        
        try:
            with open(self.eval_results_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        result = json.loads(line)
                        tier = result.get("data_quality_tier", "bronze")
                        if tier in distribution:
                            distribution[tier] += 1
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"⚠️ Error reading eval results: {e}")
        
        return distribution
    
    def get_bad_samples(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取低质量样本用于人工审核"""
        bad_samples = []
        
        if not os.path.exists(self.eval_results_file):
            return bad_samples
        
        try:
            with open(self.eval_results_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        result = json.loads(line)
                        if result.get("overall_score", 0) < 0.5:
                            sample = {
                                "query": result.get("query", ""),
                                "score": result.get("overall_score", 0),
                                "issue": result.get("error_message", "Low quality"),
                                "quality_tier": result.get("data_quality_tier", "rejected"),
                                "timestamp": result.get("timestamp", "")
                            }
                            if result.get("generation"):
                                gen = result["generation"]
                                sample.update({
                                    "faithfulness": gen.get("faithfulness", 0),
                                    "answer_relevance": gen.get("answer_relevance", 0),
                                    "answer_completeness": gen.get("answer_completeness", 0),
                                })
                            bad_samples.append(sample)
                            if len(bad_samples) >= limit:
                                break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"⚠️ Error reading bad samples: {e}")
        
        return sorted(bad_samples, key=lambda x: x["score"])[:limit]
