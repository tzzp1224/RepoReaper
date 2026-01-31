# 文件路径: app/services/auto_evaluation_service.py
"""
自动评估服务 - Phase 1
在后台异步进行评估，不阻塞用户请求

工作流程:
  1. 用户调用 /chat 或 /analyze
  2. 获得立即响应
  3. 后台异步执行评估
  4. 评估结果存储到 evaluation/sft_data/
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from evaluation.evaluation_framework import (
    EvaluationEngine, 
    EvaluationResult, 
    DataRoutingEngine,
    DataQualityTier
)
from evaluation.utils import is_chatty_query, has_code_indicators
from app.services.tracing_service import tracing_service


@dataclass
class EvaluationConfig:
    """
    自动评估配置
    
    数据路由阈值说明（与 data_router.py 一致）:
    - score > 0.9  → Gold   → positive_samples.jsonl
    - score > 0.6  → Silver → positive_samples.jsonl  
    - score > 0.4  → Bronze → negative_samples.jsonl
    - score <= 0.4 → Rejected → 不存储
    """
    enabled: bool = True                    # 是否启用自动评估
    use_ragas: bool = False                 # 是否使用 Ragas 进行 sanity check
    custom_weight: float = 0.7              # custom_eval 的权重
    ragas_weight: float = 0.3               # ragas_eval 的权重
    diff_threshold: float = 0.2             # 差异阈值（超过则标记 needs_review）
    min_quality_score: float = 0.4          # 最低质量分数（<=0.4 才拒绝）
    async_evaluation: bool = True           # 是否异步执行（推荐 True）
    min_query_length: int = 10              # 最小 query 长度
    min_answer_length: int = 100            # 最小 answer 长度
    require_repo_url: bool = True           # 是否要求有仓库 URL
    require_code_in_context: bool = True    # 是否要求上下文包含代码


class AutoEvaluationService:
    """自动评估服务"""
    
    def __init__(
        self,
        eval_engine: EvaluationEngine,
        data_router: DataRoutingEngine,
        config: EvaluationConfig = None
    ):
        self.eval_engine = eval_engine
        self.data_router = data_router
        self.config = config or EvaluationConfig()
        self.needs_review_queue: list = []  # 需要人工审查的样本队列
        self._evaluated_keys: set = set()   # 防重复评估（session_id:query_hash）
        
        # 被过滤数据的记录文件
        self.skipped_samples_file = "evaluation/sft_data/skipped_samples.jsonl"
        os.makedirs(os.path.dirname(self.skipped_samples_file), exist_ok=True)
    
    def _record_skipped(self, reason: str, query: str, session_id: str, 
                        repo_url: str = "", context_len: int = 0, answer_len: int = 0) -> None:
        """记录被跳过的样本（供日后分析）"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "session_id": session_id,
            "query": query[:200] if query else "",
            "repo_url": repo_url,
            "context_length": context_len,
            "answer_length": answer_len
        }
        try:
            with open(self.skipped_samples_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"  ⚠️ 记录跳过样本失败: {e}")
    
    def _validate_input(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str,
        repo_url: str
    ) -> tuple[bool, Optional[str]]:
        """
        验证输入是否满足评估条件
        
        Returns:
            (is_valid, skip_reason) - 如果有效返回 (True, None)，否则返回 (False, reason)
        """
        context_len = len(retrieved_context) if retrieved_context else 0
        answer_len = len(generated_answer) if generated_answer else 0
        
        # Query 验证
        if not query or not query.strip():
            self._record_skipped("query_empty", query or "", session_id, repo_url, context_len, answer_len)
            return False, "query 为空"
        
        if len(query.strip()) < self.config.min_query_length:
            self._record_skipped("query_too_short", query, session_id, repo_url, context_len, answer_len)
            return False, f"query 太短 ({len(query)} < {self.config.min_query_length})"
        
        if is_chatty_query(query):
            self._record_skipped("chatty_query", query, session_id, repo_url, context_len, answer_len)
            return False, f"闲聊/无效 query: {query[:30]}"
        
        # Repo URL 验证
        if self.config.require_repo_url and not repo_url:
            self._record_skipped("missing_repo_url", query, session_id, repo_url, context_len, answer_len)
            return False, "缺少 repo_url"
        
        # Answer 验证
        if not generated_answer or len(generated_answer.strip()) < self.config.min_answer_length:
            self._record_skipped("answer_too_short", query, session_id, repo_url, context_len, answer_len)
            return False, f"回答太短 ({answer_len} < {self.config.min_answer_length})"
        
        # Context 验证
        if self.config.require_code_in_context and not has_code_indicators(retrieved_context):
            self._record_skipped("no_code_in_context", query, session_id, repo_url, context_len, answer_len)
            return False, "上下文中未检测到代码"
        
        return True, None
    
    def _check_duplicate(self, query: str, session_id: str) -> bool:
        """检查是否重复评估，返回 True 表示是重复的"""
        import hashlib
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        eval_key = f"{session_id}:{query_hash}"
        
        if eval_key in self._evaluated_keys:
            return True
        
        self._evaluated_keys.add(eval_key)
        
        # 限制缓存大小，防止内存泄漏
        if len(self._evaluated_keys) > 1000:
            self._evaluated_keys = set(list(self._evaluated_keys)[-500:])
        
        return False
    
    async def auto_evaluate(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str = "auto",
        repo_url: str = "",
        language: str = "en"
    ) -> Optional[str]:
        """
        自动评估单个查询-回答对
        
        Returns:
            质量等级 (gold/silver/bronze/rejected/needs_review) 或 None
        """
        # 输入验证
        is_valid, skip_reason = self._validate_input(
            query, retrieved_context, generated_answer, session_id, repo_url
        )
        if not is_valid:
            print(f"  ⚠️ [AutoEval] 跳过: {skip_reason}")
            return None
        
        # 防重复评估
        if self._check_duplicate(query, session_id):
            print(f"  ⏭️ [AutoEval] 跳过重复评估: {query[:30]}...")
            return None
        
        start_time = datetime.now()
        
        try:
            # Step 1: 自定义评估
            print(f"📊 [AutoEval] 开始评估: {query[:50]}...")
            
            custom_metrics = await self.eval_engine.evaluate_generation(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer
            )
            custom_score = custom_metrics.overall_score()
            
            print(f"  ✓ Custom Score: {custom_score:.3f}")
            print(f"    - Faithfulness: {custom_metrics.faithfulness:.3f}")
            print(f"    - Answer Relevance: {custom_metrics.answer_relevance:.3f}")
            print(f"    - Completeness: {custom_metrics.answer_completeness:.3f}")
            
            # Step 2: Ragas Sanity Check (如果启用)
            ragas_score = None
            ragas_details = None
            
            if self.config.use_ragas:
                try:
                    ragas_score, ragas_details = await self._ragas_eval(
                        query=query,
                        context=retrieved_context,
                        answer=generated_answer
                    )
                    print(f"  ✓ Ragas Score: {ragas_score:.3f}")
                    if ragas_details:
                        print(f"    - {ragas_details}")
                except Exception as e:
                    print(f"  ⚠️ Ragas 评估失败: {e}")
                    # Ragas 失败不应该中断主流程
            
            # ============================================================
            # Step 3: 混合评估 + 异常检测
            # ============================================================
            final_score, quality_status = self._compute_final_score(
                custom_score=custom_score,
                ragas_score=ragas_score
            )
            
            print(f"  ✓ Final Score: {final_score:.3f} | Status: {quality_status}")
            
            # ============================================================
            # Step 4: 构建评估结果并存储
            # ============================================================
            eval_result = EvaluationResult(
                session_id=session_id,
                query=query,
                repo_url=repo_url,
                timestamp=start_time,
                language=language,
                generation_metrics=custom_metrics,
                notes=f"ragas_score={ragas_score:.3f}" if ragas_score else ""
            )
            
            # 设置综合得分
            eval_result.overall_score = final_score
            
            # 根据状态和得分确定质量等级
            print(f"  [DEBUG] quality_status={quality_status}, final_score={final_score:.3f}, threshold={self.config.min_quality_score}")
            
            if quality_status == "needs_review":
                eval_result.data_quality_tier = DataQualityTier.BRONZE
                eval_result.notes += " | needs_review=true"
                # 加入审查队列
                self.needs_review_queue.append({
                    "eval_result": eval_result,
                    "custom_score": custom_score,
                    "ragas_score": ragas_score,
                    "diff": abs(custom_score - (ragas_score or custom_score)),
                    "timestamp": start_time.isoformat()
                })
                print(f"  ⚠️ 需要人工审查 (needs_review)，暂存队列")
                # 同时也路由到数据存储，便于后续分析
                self.data_router.route_sample(eval_result)
            elif final_score > self.config.min_quality_score:
                # score > 0.4: 路由到 positive (>0.6) 或 negative (0.4-0.6)
                print(f"  ✓ 路由到 data_router (score {final_score:.2f} > {self.config.min_quality_score})")
                self.data_router.route_sample(eval_result)
            else:
                # score <= 0.4: 质量太差，直接拒绝
                eval_result.data_quality_tier = DataQualityTier.REJECTED
                print(f"  ❌ 评分过低 ({final_score:.2f} <= {self.config.min_quality_score})，拒绝存储")
            
            # 记录到 tracing
            tracing_service.add_event("auto_evaluation_completed", {
                "query": query[:100],
                "custom_score": custom_score,
                "ragas_score": ragas_score,
                "final_score": final_score,
                "status": quality_status,
                "quality_tier": eval_result.data_quality_tier.value
            })
            
            print(f"  ✅ 评估完成\n")
            
            return eval_result.data_quality_tier.value
        
        except Exception as e:
            print(f"  ❌ 自动评估异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def auto_evaluate_async(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str = "auto",
        repo_url: str = "",
        language: str = "en"
    ) -> None:
        """
        异步版本 - 不阻塞主流程
        
        在后台执行评估，不等待结果
        """
        if not self.config.async_evaluation:
            # 同步模式（不推荐在生产环境）
            await self.auto_evaluate(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer,
                session_id=session_id,
                repo_url=repo_url,
                language=language
            )
        else:
            # 异步模式（推荐）- 在后台执行
            asyncio.create_task(
                self._eval_task(
                    query=query,
                    retrieved_context=retrieved_context,
                    generated_answer=generated_answer,
                    session_id=session_id,
                    repo_url=repo_url,
                    language=language
                )
            )
    
    async def _eval_task(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str,
        repo_url: str,
        language: str
    ) -> None:
        """后台评估任务包装"""
        try:
            await asyncio.sleep(0.1)  # 让用户请求先返回
            await self.auto_evaluate(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer,
                session_id=session_id,
                repo_url=repo_url,
                language=language
            )
        except Exception as e:
            print(f"❌ Background eval task failed: {e}")
    
    def _compute_final_score(
        self,
        custom_score: float,
        ragas_score: Optional[float]
    ) -> tuple[float, str]:
        """
        计算最终得分和状态
        
        Returns:
            (final_score, status)
            status: "normal" / "needs_review" / "high_confidence"
        """
        
        if ragas_score is None:
            # 没有 Ragas 分数，直接用 custom 分数
            return custom_score, "normal"
        
        # 计算差异
        diff = abs(custom_score - ragas_score)
        
        # 判断异常
        if diff > self.config.diff_threshold:
            # 差异过大，标记为需要审查
            return custom_score, "needs_review"
        
        # 混合评分
        final_score = (
            self.config.custom_weight * custom_score +
            self.config.ragas_weight * ragas_score
        )
        
        # 两者都高分 → 高置信度
        if custom_score > 0.75 and ragas_score > 0.75:
            status = "high_confidence"
        else:
            status = "normal"
        
        return final_score, status
    
    async def _ragas_eval(
        self,
        query: str,
        context: str,
        answer: str
    ) -> tuple[Optional[float], Optional[str]]:
        """
        使用 Ragas 进行 sanity check
        
        Returns:
            (score, details)
        """
        try:
            from ragas.metrics import faithfulness, answer_relevancy
            from ragas import evaluate
            
            # 构造 Ragas 数据集
            dataset_dict = {
                "question": [query],
                "contexts": [[context]],
                "answer": [answer]
            }
            
            # 执行评估
            result = evaluate(
                dataset=dataset_dict,
                metrics=[faithfulness, answer_relevancy]
            )
            
            # 提取分数
            faithfulness_score = result["faithfulness"][0] if "faithfulness" in result else 0.5
            relevancy_score = result["answer_relevancy"][0] if "answer_relevancy" in result else 0.5
            
            # 平均得分
            ragas_score = (faithfulness_score + relevancy_score) / 2
            
            details = f"Ragas: faithfulness={faithfulness_score:.3f}, relevancy={relevancy_score:.3f}"
            
            return ragas_score, details
        
        except ImportError:
            print("⚠️ Ragas 未安装，跳过 sanity check")
            return None, None
        except Exception as e:
            print(f"⚠️ Ragas 评估异常: {e}")
            return None, None
    
    def get_review_queue(self) -> list:
        """获取需要审查的样本列表"""
        return self.needs_review_queue
    
    def clear_review_queue(self) -> None:
        """清空审查队列"""
        self.needs_review_queue.clear()
    
    def approve_sample(self, index: int) -> None:
        """人工批准某个样本"""
        if 0 <= index < len(self.needs_review_queue):
            item = self.needs_review_queue[index]
            # 直接存储到评估结果
            self.data_router.route_sample(item["eval_result"])
            print(f"✅ 样本 {index} 已批准")
    
    def reject_sample(self, index: int) -> None:
        """人工拒绝某个样本"""
        if 0 <= index < len(self.needs_review_queue):
            print(f"❌ 样本 {index} 已拒绝")
            self.needs_review_queue.pop(index)


# 全局实例
auto_eval_service: Optional[AutoEvaluationService] = None


def init_auto_evaluation_service(
    eval_engine: EvaluationEngine,
    data_router: DataRoutingEngine,
    config: EvaluationConfig = None
) -> AutoEvaluationService:
    """初始化自动评估服务"""
    global auto_eval_service
    auto_eval_service = AutoEvaluationService(
        eval_engine=eval_engine,
        data_router=data_router,
        config=config
    )
    return auto_eval_service


def get_auto_evaluation_service() -> Optional[AutoEvaluationService]:
    """获取自动评估服务实例"""
    return auto_eval_service
