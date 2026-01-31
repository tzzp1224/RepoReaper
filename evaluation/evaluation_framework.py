# 文件路径: evaluation/evaluation_framework.py
"""
GitHub Agent 完整评估框架
四层评估架构 + 数据路由引擎

Author: Dexter
Date: 2025-01-27

注意: 数据模型已拆分到 models.py，数据路由已拆分到 data_router.py
      此文件保留核心评估引擎逻辑，并重新导出所有符号保持向后兼容
"""

import json
import os
import re
from typing import List, Dict, Any
from datetime import datetime

# 重新导出所有模型（保持向后兼容）
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


# ============================================================================
# 评估引擎核心逻辑
# ============================================================================

class EvaluationEngine:
    """评估引擎 - 负责多层面打分"""
    
    def __init__(
        self, 
        llm_client=None, 
        golden_dataset_path: str = "evaluation/golden_dataset.json",
        model_name: str = None
    ):
        self.llm_client = llm_client
        self.model_name = model_name or "gpt-4o-mini"  # 默认使用轻量模型
        self.golden_dataset = self._load_golden_dataset(golden_dataset_path)
    
    def _load_golden_dataset(self, path: str) -> List[Dict]:
        """加载黄金数据集"""
        if not os.path.exists(path):
            print(f"⚠️ Golden dataset not found at {path}")
            return []
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    async def evaluate_query_rewrite(
        self,
        original_query: str,
        rewritten_query: str,
        language_detected: str
    ) -> QueryRewriteMetrics:
        """
        评估查询重写质量
        
        指标:
        - keyword_coverage: 重写后的关键词是否覆盖了原Query的核心概念?
        - semantic_preservation: 语义是否保留?
        - diversity_score: 关键词多样性
        """
        
        # 简化版: 使用关键词匹配
        original_tokens = set(original_query.lower().split())
        rewritten_tokens = set(rewritten_query.lower().split())
        
        # 关键词覆盖度: 原Query的关键词有多少在重写中保留
        if original_tokens:
            coverage = len(original_tokens & rewritten_tokens) / len(original_tokens)
        else:
            coverage = 0.0
        
        # 多样性: 重写后的关键词数量越多、越不重复，分数越高
        unique_ratio = len(rewritten_tokens) / max(len(original_tokens), 1)
        diversity = min(1.0, unique_ratio)
        
        # 语义保留度 (简化版本: 假设如果覆盖度高就认为语义保留良好)
        semantic_preservation = min(1.0, coverage + 0.2)  # 基础分+覆盖度加分
        
        return QueryRewriteMetrics(
            original_query=original_query,
            rewritten_query=rewritten_query,
            language_detected=language_detected,
            keyword_coverage=coverage,
            semantic_preservation=semantic_preservation,
            diversity_score=diversity
        )
    
    async def evaluate_retrieval(
        self,
        query: str,
        retrieved_files: List[str],
        ground_truth_files: List[str],
        top_k: int = 5,
        retrieval_latency_ms: float = 0,
        vector_scores: List[float] = None,
        bm25_scores: List[float] = None
    ) -> RetrievalMetrics:
        """
        评估检索层质量
        
        指标:
        - hit_rate: 是否找到了任何正确的文件?
        - recall_at_k: 前K个中有多少是正确的?
        - precision_at_k: 返回的文件中有多少是正确的?
        - mrr: 第一个正确结果的排名倒数
        """
        
        retrieved_set = set(retrieved_files[:top_k])
        ground_truth_set = set(ground_truth_files)
        
        # Hit rate: 是否有交集
        hit_rate = 1.0 if retrieved_set & ground_truth_set else 0.0
        
        # Recall@K: 找到的正确结果数 / 正确结果总数
        correct_count = len(retrieved_set & ground_truth_set)
        recall = correct_count / len(ground_truth_set) if ground_truth_set else 0.0
        
        # Precision@K: 找到的正确结果数 / 返回的结果总数
        precision = correct_count / len(retrieved_set) if retrieved_set else 0.0
        
        # MRR: 第一个正确结果的倒数排名
        mrr = 0.0
        for i, file in enumerate(retrieved_files[:top_k], 1):
            if file in ground_truth_set:
                mrr = 1.0 / i
                break
        
        # Context Relevance: 简化版 - 假设Precision反映了相关性
        context_relevance = precision
        
        # Chunk Integrity: 简化版 - 假设没有太多文件就认为完整度高
        chunk_integrity = min(1.0, 1.0 / len(retrieved_set)) if retrieved_set else 0.0
        
        vector_avg = sum(vector_scores) / len(vector_scores) if vector_scores else 0.0
        bm25_avg = sum(bm25_scores) / len(bm25_scores) if bm25_scores else 0.0
        
        return RetrievalMetrics(
            query=query,
            top_k=top_k,
            hit_rate=hit_rate,
            recall_at_k=recall,
            precision_at_k=precision,
            mrr=mrr,
            context_relevance=context_relevance,
            chunk_integrity=chunk_integrity,
            retrieval_latency_ms=retrieval_latency_ms,
            vector_score_avg=vector_avg,
            bm25_score_avg=bm25_avg,
            retrieved_files=retrieved_files,
            ground_truth_files=ground_truth_files
        )
    
    async def evaluate_generation(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        ground_truth_answer: str = "",
        generation_latency_ms: float = 0,
        token_usage: Dict[str, int] = None
    ) -> GenerationMetrics:
        """
        评估生成层质量
        
        指标:
        - faithfulness: 回答是否严格基于Context?
        - answer_relevance: 回答是否回答了问题?
        - answer_completeness: 回答是否足够完整?
        - code_correctness: 生成的代码是否正确?
        """
        
        # 1. Faithfulness: 使用LLM-as-Judge进行幻觉检测
        faithfulness = await self._judge_faithfulness(
            retrieved_context,
            generated_answer
        )
        
        # 2. Answer Relevance: 回答和问题的相似度
        answer_relevance = await self._judge_answer_relevance(
            query,
            generated_answer
        )
        
        # 3. Answer Completeness: 简化版 - 通过长度和结构判断
        completeness = self._judge_completeness(
            generated_answer,
            ground_truth_answer
        )
        
        # 4. Code Correctness: 使用AST检查代码块
        code_samples = self._extract_code_blocks(generated_answer)
        code_correctness = self._check_code_correctness(code_samples)
        
        metrics = GenerationMetrics(
            query=query,
            retrieved_context=retrieved_context,
            generated_answer=generated_answer,
            ground_truth_answer=ground_truth_answer,
            faithfulness=faithfulness,
            answer_relevance=answer_relevance,
            answer_completeness=completeness,
            code_correctness=code_correctness,
            generated_code_samples=code_samples,
            generation_latency_ms=generation_latency_ms,
            token_usage=token_usage or {"input": 0, "output": 0}
        )
        
        return metrics
    
    async def _judge_faithfulness(self, context: str, answer: str) -> float:
        """
        LLM-as-Judge: 判断回答是否由Context支撑
        返回 0-1 的分数
        
        注意：Faithfulness 判断的是"回答中的信息是否能从 Context 中找到依据"
        而不是"回答是否完全复制 Context 内容"
        """
        if not self.llm_client:
            # 简化版: 如果没有LLM客户端，使用启发式方法
            # 统计Answer中的关键词有多少出现在Context中
            context_lower = context.lower()
            answer_words = set(answer.lower().split())
            # 过滤掉常见停用词
            stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 
                         'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                         'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                         'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
                         'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'that',
                         'which', 'who', 'whom', 'this', 'these', 'those', 'it', 'its'}
            meaningful_words = answer_words - stop_words
            if not meaningful_words:
                return 0.7  # 没有有意义的词，给默认分
            # 计算答案中有多少有意义的词出现在Context中
            found_count = sum(1 for word in meaningful_words if word in context_lower)
            overlap = found_count / len(meaningful_words)
            return min(1.0, overlap + 0.2)  # 给一定的基础分
        
        # 智能截取 Context：提取与 Answer 相关的部分
        # 如果 Context 太长，优先包含 Answer 中提到的关键词附近的内容
        max_context_len = 6000  # 增加到 6000 字符
        if len(context) > max_context_len:
            # 尝试找到 Answer 中提到的关键文件/函数名
            import re
            # 提取 Answer 中可能的文件路径或函数名
            patterns = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*', answer[:500])
            important_terms = [p for p in patterns if len(p) > 3][:5]  # 取前5个重要词
            
            # 优先截取包含这些词的部分
            context_parts = []
            remaining = max_context_len
            for term in important_terms:
                idx = context.find(term)
                if idx != -1 and remaining > 0:
                    start = max(0, idx - 300)
                    end = min(len(context), idx + 700)
                    snippet = context[start:end]
                    if snippet not in ''.join(context_parts):
                        context_parts.append(snippet)
                        remaining -= len(snippet)
            
            # 如果没找到相关部分，还是用前 6000 字符
            if context_parts:
                truncated_context = "\n...\n".join(context_parts)
            else:
                truncated_context = context[:max_context_len]
        else:
            truncated_context = context
        
        # 改进的 Prompt：更明确定义 Faithfulness
        prompt = f"""Evaluate the FAITHFULNESS of the answer to the given context.

FAITHFULNESS means: The claims and information in the answer can be verified from or are consistent with the context. 
- Score HIGH (0.7-1.0) if the answer correctly identifies or explains concepts that ARE in the context
- Score MEDIUM (0.4-0.7) if the answer is partially supported but makes some unsupported claims  
- Score LOW (0.0-0.4) if the answer contradicts the context or makes completely unsupported claims

NOTE: If the answer says "X is not in the context" and X is indeed not shown, that's a FAITHFUL statement (score 0.7+)
NOTE: If the answer correctly identifies WHERE something is defined based on imports/references in context, that's FAITHFUL

[Context]
{truncated_context}

[Answer]
{answer[:1500]}

SCORE (0.0-1.0):"""
        
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10
            )
            score_str = response.choices[0].message.content.strip()
            # 提取数字（处理可能的额外文本）
            import re
            match = re.search(r'(\d+\.?\d*)', score_str)
            if match:
                score = float(match.group(1))
            else:
                score = float(score_str)
            return min(1.0, max(0.0, score))
        except Exception as e:
            print(f"⚠️ Faithfulness judgment failed: {e}")
            return 0.5
    
    async def _judge_answer_relevance(self, query: str, answer: str) -> float:
        """判断回答与问题的相关性"""
        if not self.llm_client:
            # 简化版: 使用关键词重叠度
            query_words = set(query.lower().split())
            answer_words = set(answer.lower().split())
            overlap = len(query_words & answer_words) / max(len(query_words), 1)
            return min(1.0, overlap + 0.3)  # 基础分0.3+重叠度
        
        prompt = f"""
        Does the answer address the query?
        
        [Query]
        {query}
        
        [Answer]
        {answer[:1000]}
        
        Score (0.0-1.0):
        """
        
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10
            )
            score = float(response.choices[0].message.content.strip())
            return min(1.0, max(0.0, score))
        except:
            return 0.5
    
    def _judge_completeness(self, generated_answer: str, ground_truth: str = "") -> float:
        """判断回答的完整性"""
        # 简化版: 根据长度和结构
        if len(generated_answer) < 50:
            return 0.3
        elif len(generated_answer) < 200:
            return 0.6
        else:
            return 0.9
    
    def _extract_code_blocks(self, text: str) -> List[str]:
        """从文本中提取代码块"""
        import re
        code_pattern = r'```[\w]*\n(.*?)\n```'
        matches = re.findall(code_pattern, text, re.DOTALL)
        return matches
    
    def _check_code_correctness(self, code_samples: List[str]) -> float:
        """检查代码是否有语法错误"""
        if not code_samples:
            return 1.0  # 没有代码就认为正确
        
        import ast
        correct_count = 0
        for code in code_samples:
            try:
                ast.parse(code)
                correct_count += 1
            except SyntaxError:
                pass
        
        return correct_count / len(code_samples)
    
    async def evaluate_agentic(
        self,
        query: str,
        tool_calls: List[Dict[str, Any]],
        success: bool,
        steps_taken: int = 0,
        end_to_end_latency_ms: float = 0
    ) -> AgenticMetrics:
        """
        评估Agent的决策和行为
        """
        
        # Tool Selection Accuracy: 工具选择是否正确?
        tool_selection_accuracy = 1.0 if success else 0.5
        
        # Tool Parameter Correctness: 参数是否正确传递?
        tool_param_correctness = 1.0 if all(
            tc.get("success", False) for tc in tool_calls
        ) else 0.5
        
        # 计算冗余步骤
        unnecessary_steps = 0
        backtrack_count = 0
        
        # 简化版: 如果有重复的工具调用则视为冗余
        tool_call_signatures = [tc.get("name", "") for tc in tool_calls]
        for i, sig in enumerate(tool_call_signatures):
            if i > 0 and sig == tool_call_signatures[i-1]:
                unnecessary_steps += 1
        
        return AgenticMetrics(
            query=query,
            tool_calls=tool_calls,
            tool_selection_accuracy=tool_selection_accuracy,
            tool_parameter_correctness=tool_param_correctness,
            steps_taken=steps_taken,
            unnecessary_steps=unnecessary_steps,
            backtrack_count=backtrack_count,
            success=success,
            end_to_end_latency_ms=end_to_end_latency_ms
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取评估统计信息
        
        Returns:
            包含 total_evaluations, average_score, quality_distribution, top_issues 的字典
        """
        # 从 eval_results.jsonl 读取评估结果
        eval_results_path = "evaluation/sft_data/eval_results.jsonl"
        
        stats = {
            "total_evaluations": 0,
            "average_score": 0.0,
            "quality_distribution": {
                "gold": 0,
                "silver": 0,
                "bronze": 0,
                "rejected": 0
            },
            "top_issues": []
        }
        
        if not os.path.exists(eval_results_path):
            return stats
        
        # 读取和分析评估结果
        scores = []
        issues = {}
        
        try:
            with open(eval_results_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        result = json.loads(line)
                        stats["total_evaluations"] += 1
                        
                        # 收集得分
                        score = result.get("overall_score", 0)
                        scores.append(score)
                        
                        # 统计质量分布
                        tier = result.get("data_quality_tier", "bronze")
                        if tier in stats["quality_distribution"]:
                            stats["quality_distribution"][tier] += 1
                        
                        # 收集常见问题 (假设记录在 notes 或 error_message 中)
                        note = result.get("notes", "") or result.get("error_message", "")
                        if note:
                            issues[note] = issues.get(note, 0) + 1
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"⚠️ Error reading eval results: {e}")
        
        # 计算平均分
        if scores:
            stats["average_score"] = sum(scores) / len(scores)
        
        # 获取前5个常见问题
        if issues:
            stats["top_issues"] = [
                {"issue": issue, "count": count}
                for issue, count in sorted(issues.items(), key=lambda x: x[1], reverse=True)[:5]
            ]
        
        return stats


# ============================================================================
# __all__ 导出列表（保持向后兼容）
# ============================================================================

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
]
