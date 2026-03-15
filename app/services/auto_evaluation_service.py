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
import random
import time
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import asdict, dataclass, field, replace

from evaluation.evaluation_framework import (
    EvaluationEngine,
    EvaluationResult,
    DataRoutingEngine,
    DataQualityTier,
)
from evaluation.utils import is_chatty_query, has_code_indicators
from app.services.tracing_service import tracing_service
from app.core.config import AutoEvaluationConfig, auto_eval_config as default_auto_eval_config


@dataclass
class _EvalTask:
    """异步评估队列任务。"""
    query: str
    retrieved_context: str
    generated_answer: str
    session_id: str
    repo_url: str
    language: str
    trace_id: Optional[str] = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class AutoEvalRuntimeMetrics:
    """自动评估运行时指标（仅用于可观测）。"""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    enqueued: int = 0
    dropped_queue_full: int = 0
    processed: int = 0
    failed: int = 0
    visualize_only_observed: int = 0
    queue_wait_ms_total: float = 0.0
    queue_wait_ms_max: float = 0.0
    worker_starts: int = 0
    ragas_attempted: int = 0
    ragas_sampled: int = 0
    ragas_skipped_sampling: int = 0
    ragas_timeouts: int = 0
    ragas_failures: int = 0
    ragas_circuit_open_hits: int = 0
    last_error: str = ""


class AutoEvaluationService:
    """自动评估服务"""

    def __init__(
        self,
        eval_engine: EvaluationEngine,
        data_router: DataRoutingEngine,
        config: AutoEvaluationConfig = None,
    ):
        self.eval_engine = eval_engine
        self.data_router = data_router
        self.config = config or replace(default_auto_eval_config)
        self.needs_review_queue: list = []  # 需要人工审查的样本队列
        self._evaluated_keys: set = set()   # 防重复评估（session_id:query_hash）
        self._metrics = AutoEvalRuntimeMetrics()

        self._eval_queue: Optional[asyncio.Queue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._worker_stop: bool = False
        if self.config.queue_enabled:
            self._eval_queue = asyncio.Queue(maxsize=max(1, self.config.queue_maxsize))

        # Ragas 熔断状态
        self._ragas_consecutive_failures = 0
        self._ragas_circuit_open_until = 0.0

        # 被过滤数据的记录文件
        self.skipped_samples_file = "evaluation/sft_data/skipped_samples.jsonl"
        os.makedirs(os.path.dirname(self.skipped_samples_file), exist_ok=True)

    def _safe_add_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Tracing 必须 fail-open，不能影响评估流程。"""
        try:
            tracing_service.add_event(event_name, event_data)
        except Exception as e:
            print(f"  ⚠️ [AutoEval] tracing failed for {event_name}: {e}")

    def _safe_record_score(
        self,
        name: str,
        value: float | str,
        *,
        data_type: str = "NUMERIC",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Langfuse score 上报必须 fail-open。"""
        try:
            tracing_service.record_score(
                score_name=name,
                value=value,
                data_type=data_type,
                metadata=metadata,
            )
        except Exception as e:
            print(f"  ⚠️ [AutoEval] tracing score failed for {name}: {e}")

    def _record_skipped(
        self,
        reason: str,
        query: str,
        session_id: str,
        repo_url: str = "",
        context_len: int = 0,
        answer_len: int = 0,
    ) -> None:
        """记录被跳过的样本（供日后分析）"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "session_id": session_id,
            "query": query[:200] if query else "",
            "repo_url": repo_url,
            "context_length": context_len,
            "answer_length": answer_len,
        }
        try:
            with open(self.skipped_samples_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"  ⚠️ 记录跳过样本失败: {e}")

    def _validate_input(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str,
        repo_url: str,
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

    def _is_ragas_circuit_open(self) -> bool:
        """检查 Ragas 熔断是否开启。"""
        if self._ragas_circuit_open_until <= 0:
            return False
        if time.monotonic() >= self._ragas_circuit_open_until:
            self._ragas_circuit_open_until = 0.0
            self._ragas_consecutive_failures = 0
            return False
        return True

    def _on_ragas_success(self) -> None:
        self._ragas_consecutive_failures = 0
        self._ragas_circuit_open_until = 0.0

    def _on_ragas_failure(self, reason: str) -> None:
        self._metrics.ragas_failures += 1
        self._metrics.last_error = f"ragas:{reason}"
        if not self.config.ragas_circuit_breaker_enabled:
            return
        self._ragas_consecutive_failures += 1
        if self._ragas_consecutive_failures >= self.config.ragas_cb_fail_threshold:
            self._ragas_circuit_open_until = time.monotonic() + self.config.ragas_cb_reset_sec

    def _should_sample_ragas(self) -> bool:
        """是否对当前样本执行 Ragas（抽样）。"""
        sample_rate = max(0.0, min(1.0, self.config.ragas_sample_rate))
        return random.random() < sample_rate

    async def _ensure_worker_started(self) -> None:
        """确保 sidecar worker 已启动。"""
        if not self.config.queue_enabled or self._eval_queue is None:
            return
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_stop = False
        self._worker_task = asyncio.create_task(self._queue_worker(), name="auto_eval_worker")
        self._metrics.worker_starts += 1

    async def _queue_worker(self) -> None:
        """后台 worker：消费评估任务，不影响主链路。"""
        if self._eval_queue is None:
            return

        while not self._worker_stop:
            task = await self._eval_queue.get()
            if task is None:
                self._eval_queue.task_done()
                break

            try:
                wait_ms = (time.monotonic() - task.enqueued_at) * 1000
                self._metrics.queue_wait_ms_total += wait_ms
                self._metrics.queue_wait_ms_max = max(self._metrics.queue_wait_ms_max, wait_ms)
                with tracing_service.trace_scope(task.trace_id, session_id=task.session_id):
                    await self.auto_evaluate(
                        query=task.query,
                        retrieved_context=task.retrieved_context,
                        generated_answer=task.generated_answer,
                        session_id=task.session_id,
                        repo_url=task.repo_url,
                        language=task.language,
                    )
                self._metrics.processed += 1
            except Exception as e:
                self._metrics.failed += 1
                self._metrics.last_error = str(e)
                print(f"❌ Background eval worker failed: {e}")
            finally:
                self._eval_queue.task_done()

    async def shutdown(self) -> None:
        """关闭后台 worker。"""
        self._worker_stop = True
        if self._eval_queue and self._worker_task and not self._worker_task.done():
            try:
                self._eval_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            try:
                await asyncio.wait_for(self._worker_task, timeout=2.0)
            except Exception:
                self._worker_task.cancel()
        self._worker_task = None

    async def auto_evaluate(
        self,
        query: str,
        retrieved_context: str,
        generated_answer: str,
        session_id: str = "auto",
        repo_url: str = "",
        language: str = "en",
    ) -> Optional[str]:
        """
        自动评估单个查询-回答对
        
        Returns:
            质量等级 (gold/silver/bronze/rejected/needs_review) 或 None
        """
        if not self.config.enabled:
            return None

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
            self._safe_add_event(
                "auto_evaluation_started",
                {
                    "query": query[:100],
                    "session_id": session_id,
                    "visualize_only": self.config.visualize_only,
                },
            )

            custom_metrics = await self.eval_engine.evaluate_generation(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer,
            )
            custom_score = custom_metrics.overall_score()

            print(f"  ✓ Custom Score: {custom_score:.3f}")
            print(f"    - Faithfulness: {custom_metrics.faithfulness:.3f}")
            print(f"    - Answer Relevance: {custom_metrics.answer_relevance:.3f}")
            print(f"    - Completeness: {custom_metrics.answer_completeness:.3f}")

            # Step 2: Ragas Sanity Check (抽样 + 超时 + 熔断)
            ragas_score = None
            ragas_details = None

            if self.config.use_ragas:
                if self._is_ragas_circuit_open():
                    self._metrics.ragas_circuit_open_hits += 1
                elif not self._should_sample_ragas():
                    self._metrics.ragas_skipped_sampling += 1
                else:
                    self._metrics.ragas_attempted += 1
                    try:
                        ragas_score, ragas_details = await asyncio.wait_for(
                            self._ragas_eval(
                                query=query,
                                context=retrieved_context,
                                answer=generated_answer,
                            ),
                            timeout=self.config.ragas_timeout_sec,
                        )
                        if ragas_score is not None:
                            self._metrics.ragas_sampled += 1
                            self._on_ragas_success()
                            print(f"  ✓ Ragas Score: {ragas_score:.3f}")
                            if ragas_details:
                                print(f"    - {ragas_details}")
                        else:
                            self._on_ragas_failure("empty_score")
                    except asyncio.TimeoutError:
                        self._metrics.ragas_timeouts += 1
                        self._on_ragas_failure("timeout")
                        print("  ⚠️ Ragas 评估超时，跳过")
                    except Exception as e:
                        self._on_ragas_failure(str(e))
                        print(f"  ⚠️ Ragas 评估失败: {e}")

            # ============================================================
            # Step 3: 混合评估 + 异常检测
            # ============================================================
            final_score, quality_status = self._compute_final_score(
                custom_score=custom_score,
                ragas_score=ragas_score,
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
                notes=f"ragas_score={ragas_score:.3f}" if ragas_score is not None else "",
            )

            # 设置综合得分
            eval_result.apply_overall_score(final_score)

            if quality_status == "needs_review":
                eval_result.notes += " | needs_review=true"

            if self.config.visualize_only:
                if quality_status == "needs_review":
                    self._enqueue_review_sample(
                        eval_result=eval_result,
                        custom_score=custom_score,
                        ragas_score=ragas_score,
                        timestamp=start_time,
                    )
                self._metrics.visualize_only_observed += 1
            else:
                if quality_status == "needs_review":
                    self._enqueue_review_sample(
                        eval_result=eval_result,
                        custom_score=custom_score,
                        ragas_score=ragas_score,
                        timestamp=start_time,
                    )
                    print("  ⚠️ 需要人工审查 (needs_review)，等待人工审批后再落盘")
                elif eval_result.data_quality_tier != DataQualityTier.REJECTED:
                    print(
                        f"  ✓ 路由到 data_router "
                        f"(tier={eval_result.data_quality_tier.value}, score={final_score:.2f})"
                    )
                    self.data_router.route_sample(eval_result)
                else:
                    print(f"  ❌ 评分过低 (tier=rejected, score={final_score:.2f})，拒绝存储")

            score_metadata = {
                "session_id": session_id,
                "repo_url": repo_url,
                "query": query[:100],
                "quality_status": quality_status,
                "quality_tier": eval_result.data_quality_tier.value,
                "visualize_only": self.config.visualize_only,
            }
            self._safe_record_score(
                "auto_eval.final_score",
                round(float(final_score), 6),
                metadata=score_metadata,
            )
            self._safe_record_score(
                "auto_eval.custom_score",
                round(float(custom_score), 6),
                metadata=score_metadata,
            )
            if ragas_score is not None:
                self._safe_record_score(
                    "auto_eval.ragas_score",
                    round(float(ragas_score), 6),
                    metadata=score_metadata,
                )
            self._safe_record_score(
                "auto_eval.quality_tier",
                eval_result.data_quality_tier.value,
                data_type="CATEGORICAL",
                metadata=score_metadata,
            )

            self._safe_add_event(
                "auto_evaluation_completed",
                {
                    "query": query[:100],
                    "session_id": session_id,
                    "custom_score": custom_score,
                    "ragas_score": ragas_score,
                    "final_score": final_score,
                    "status": quality_status,
                    "quality_tier": eval_result.data_quality_tier.value,
                    "visualize_only": self.config.visualize_only,
                },
            )

            print(f"  ✅ 评估完成\n")

            return eval_result.data_quality_tier.value

        except Exception as e:
            self._metrics.failed += 1
            self._metrics.last_error = str(e)
            self._safe_add_event(
                "auto_evaluation_failed",
                {"query": query[:100], "session_id": session_id, "error": str(e)},
            )
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
        language: str = "en",
    ) -> None:
        """
        异步版本 - 不阻塞主流程
        
        在后台执行评估，不等待结果
        """
        if not self.config.enabled:
            return

        if not self.config.async_evaluation:
            await self.auto_evaluate(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer,
                session_id=session_id,
                repo_url=repo_url,
                language=language,
            )
            return

        if not self.config.queue_enabled or self._eval_queue is None:
            await self.auto_evaluate(
                query=query,
                retrieved_context=retrieved_context,
                generated_answer=generated_answer,
                session_id=session_id,
                repo_url=repo_url,
                language=language,
            )
            return

        await self._ensure_worker_started()
        payload = _EvalTask(
            query=query,
            retrieved_context=retrieved_context,
            generated_answer=generated_answer,
            session_id=session_id,
            repo_url=repo_url,
            language=language,
            trace_id=tracing_service.get_current_trace_id(),
        )

        if self.config.drop_when_queue_full:
            try:
                self._eval_queue.put_nowait(payload)
                self._metrics.enqueued += 1
            except asyncio.QueueFull:
                self._metrics.dropped_queue_full += 1
                self._safe_add_event(
                    "auto_evaluation_dropped",
                    {
                        "query": query[:100],
                        "session_id": session_id,
                        "reason": "queue_full",
                        "queue_maxsize": self._eval_queue.maxsize,
                    },
                )
        else:
            await self._eval_queue.put(payload)
            self._metrics.enqueued += 1

    def _compute_final_score(
        self,
        custom_score: float,
        ragas_score: Optional[float],
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
            self.config.custom_weight * custom_score
            + self.config.ragas_weight * ragas_score
        )

        # 两者都高分 → 高置信度
        if custom_score > 0.75 and ragas_score > 0.75:
            status = "high_confidence"
        else:
            status = "normal"

        return final_score, status

    def _enqueue_review_sample(
        self,
        eval_result: EvaluationResult,
        custom_score: float,
        ragas_score: Optional[float],
        timestamp: datetime,
    ) -> None:
        """将待人工审核样本放入队列（审批前不落盘）。"""
        self.needs_review_queue.append(
            {
                "eval_result": eval_result,
                "custom_score": custom_score,
                "ragas_score": ragas_score,
                "diff": abs(custom_score - (ragas_score if ragas_score is not None else custom_score)),
                "timestamp": timestamp.isoformat(),
                "routed": False,
            }
        )

    async def _ragas_eval(
        self,
        query: str,
        context: str,
        answer: str,
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
                "answer": [answer],
            }

            # 执行评估
            result = evaluate(
                dataset=dataset_dict,
                metrics=[faithfulness, answer_relevancy],
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

    def get_metrics(self) -> Dict[str, Any]:
        """获取可观测指标快照。"""
        payload = asdict(self._metrics)
        payload.update(
            {
                "queue_enabled": self.config.queue_enabled,
                "queue_size": self._eval_queue.qsize() if self._eval_queue else 0,
                "queue_maxsize": self._eval_queue.maxsize if self._eval_queue else 0,
                "worker_running": bool(self._worker_task and not self._worker_task.done()),
                "visualize_only": self.config.visualize_only,
                "ragas_circuit_open": self._is_ragas_circuit_open(),
                "ragas_consecutive_failures": self._ragas_consecutive_failures,
            }
        )
        return payload

    def get_runtime_status(self) -> Dict[str, Any]:
        """获取运行时状态（用于仪表盘展示）。"""
        return {
            "queue": {
                "enabled": self.config.queue_enabled,
                "size": self._eval_queue.qsize() if self._eval_queue else 0,
                "maxsize": self._eval_queue.maxsize if self._eval_queue else 0,
                "drop_when_full": self.config.drop_when_queue_full,
                "worker_running": bool(self._worker_task and not self._worker_task.done()),
            },
            "ragas": {
                "enabled": self.config.use_ragas,
                "sample_rate": self.config.ragas_sample_rate,
                "timeout_sec": self.config.ragas_timeout_sec,
                "circuit_open": self._is_ragas_circuit_open(),
                "cb_enabled": self.config.ragas_circuit_breaker_enabled,
            },
            "visualize_only": self.config.visualize_only,
        }

    def get_review_queue(self) -> list:
        """获取需要审查的样本列表"""
        return self.needs_review_queue

    def clear_review_queue(self) -> None:
        """清空审查队列"""
        self.needs_review_queue.clear()

    def approve_sample(self, index: int) -> None:
        """人工批准某个样本"""
        if 0 <= index < len(self.needs_review_queue):
            item = self.needs_review_queue.pop(index)
            # visualize_only 模式不允许写入训练数据
            if (not self.config.visualize_only) and (not item.get("routed", False)):
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
    config: AutoEvaluationConfig = None,
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


# 向后兼容：外部继续从该模块导入 EvaluationConfig
EvaluationConfig = AutoEvaluationConfig
