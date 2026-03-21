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
import hashlib
import json
import os
import random
import time
import types
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import asdict, dataclass, field, replace, fields
from collections import OrderedDict
from contextlib import contextmanager

from evaluation.evaluation_framework import (
    EvaluationEngine,
    EvaluationResult,
    DataRoutingEngine,
    DataQualityTier,
)
from evaluation.models import QueryRewriteMetrics, RetrievalMetrics, GenerationMetrics, AgenticMetrics
from evaluation.utils import is_chatty_query, has_code_indicators
from app.services.tracing_service import tracing_service
from app.core.config import AutoEvaluationConfig, auto_eval_config as default_auto_eval_config, settings


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
    inflight: int = 0
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
        self._evaluated_keys: OrderedDict[str, None] = OrderedDict()  # 防重复评估（session_id:query_hash）
        self._pending_eval_keys: set[str] = set()
        self._metrics = AutoEvalRuntimeMetrics()

        self._eval_queue: Optional[asyncio.Queue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._worker_stop: bool = False
        if self.config.queue_enabled:
            self._eval_queue = asyncio.Queue(maxsize=max(1, self.config.queue_maxsize))

        # Ragas 熔断状态
        self._ragas_consecutive_failures = 0
        self._ragas_circuit_open_until = 0.0

        # 运行时状态文件（Phase 5：持久化）
        self._state_dir = Path("evaluation/sft_data")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self.skipped_samples_file = str(self._state_dir / "skipped_samples.jsonl")
        self.review_queue_file = str(self._state_dir / "needs_review_queue.json")
        self.evaluated_keys_file = str(self._state_dir / "evaluated_keys.json")
        self.review_decisions_file = str(self._state_dir / "review_decisions.json")
        self._review_decisions: Dict[str, Dict[str, Any]] = {}
        self._load_persistent_state()

    def _write_json_atomic(self, filepath: str, payload: Any) -> None:
        """原子写入 JSON，避免文件损坏。"""
        temp_filepath = f"{filepath}.tmp"
        with open(temp_filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_filepath, filepath)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now()

    @staticmethod
    def _metric_from_payload(payload: Optional[Dict[str, Any]], metric_cls):
        """从字典安全恢复 dataclass 指标对象。"""
        if not isinstance(payload, dict):
            return None
        allowed_fields = {f.name for f in fields(metric_cls)}
        kwargs = {k: v for k, v in payload.items() if k in allowed_fields}
        try:
            return metric_cls(**kwargs)
        except Exception:
            return None

    def _evaluation_result_from_dict(self, payload: Dict[str, Any]) -> Optional[EvaluationResult]:
        """从持久化字典恢复 EvaluationResult。"""
        if not isinstance(payload, dict):
            return None

        result = EvaluationResult(
            session_id=payload.get("session_id", "unknown"),
            query=payload.get("query", ""),
            repo_url=payload.get("repo_url", ""),
            timestamp=self._parse_timestamp(payload.get("timestamp")),
            language=payload.get("language", "en"),
            query_rewrite_metrics=self._metric_from_payload(payload.get("query_rewrite"), QueryRewriteMetrics),
            retrieval_metrics=self._metric_from_payload(payload.get("retrieval"), RetrievalMetrics),
            generation_metrics=self._metric_from_payload(payload.get("generation"), GenerationMetrics),
            agentic_metrics=self._metric_from_payload(payload.get("agentic"), AgenticMetrics),
            error_message=payload.get("error_message"),
            notes=payload.get("notes", ""),
        )
        try:
            score = float(payload.get("overall_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        result.apply_overall_score(score)
        return result

    def _serialize_review_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        eval_result_dict = item.get("eval_result_dict")
        if not eval_result_dict:
            eval_result = item.get("eval_result")
            if isinstance(eval_result, EvaluationResult):
                eval_result_dict = eval_result.to_dict()
            else:
                eval_result_dict = {}

        return {
            "sample_id": item.get("sample_id"),
            "eval_result": eval_result_dict,
            "custom_score": item.get("custom_score"),
            "ragas_score": item.get("ragas_score"),
            "diff": item.get("diff"),
            "timestamp": item.get("timestamp"),
            "routed": bool(item.get("routed", False)),
        }

    def _deserialize_review_item(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        eval_payload = payload.get("eval_result")
        eval_result = self._evaluation_result_from_dict(eval_payload if isinstance(eval_payload, dict) else {})
        if not eval_result:
            return None

        sample_id = payload.get("sample_id")
        if not sample_id:
            sample_id = f"sample_{uuid.uuid4().hex[:16]}"

        return {
            "sample_id": sample_id,
            "eval_result": eval_result,
            "eval_result_dict": eval_result.to_dict(),
            "custom_score": payload.get("custom_score"),
            "ragas_score": payload.get("ragas_score"),
            "diff": payload.get("diff"),
            "timestamp": payload.get("timestamp") or datetime.now().isoformat(),
            "routed": bool(payload.get("routed", False)),
        }

    def _persist_review_queue(self) -> None:
        try:
            serialized = [self._serialize_review_item(item) for item in self.needs_review_queue]
            self._write_json_atomic(self.review_queue_file, serialized)
        except Exception as e:
            print(f"  ⚠️ 保存 needs_review 队列失败: {e}")

    def _persist_evaluated_keys(self) -> None:
        try:
            self._write_json_atomic(self.evaluated_keys_file, list(self._evaluated_keys.keys()))
        except Exception as e:
            print(f"  ⚠️ 保存去重缓存失败: {e}")

    def _persist_review_decisions(self) -> None:
        try:
            self._write_json_atomic(self.review_decisions_file, self._review_decisions)
        except Exception as e:
            print(f"  ⚠️ 保存审核决策失败: {e}")

    def _load_persistent_state(self) -> None:
        """加载审核队列、去重缓存、审核决策（失败不影响主流程）。"""
        # 加载去重缓存
        if os.path.exists(self.evaluated_keys_file):
            try:
                with open(self.evaluated_keys_file, "r", encoding="utf-8") as f:
                    keys = json.load(f)
                if isinstance(keys, list):
                    self._evaluated_keys = OrderedDict((str(k), None) for k in keys if isinstance(k, str))
                    if len(self._evaluated_keys) > 1000:
                        while len(self._evaluated_keys) > 500:
                            self._evaluated_keys.popitem(last=False)
            except Exception as e:
                print(f"  ⚠️ 加载去重缓存失败: {e}")

        # 加载审核决策（用于 approve/reject 幂等）
        if os.path.exists(self.review_decisions_file):
            try:
                with open(self.review_decisions_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    self._review_decisions = payload
            except Exception as e:
                print(f"  ⚠️ 加载审核决策失败: {e}")

        # 加载待审核队列
        if os.path.exists(self.review_queue_file):
            try:
                with open(self.review_queue_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, list):
                    loaded_items = []
                    for item in payload:
                        loaded = self._deserialize_review_item(item)
                        if loaded:
                            loaded_items.append(loaded)
                    self.needs_review_queue = loaded_items
            except Exception as e:
                print(f"  ⚠️ 加载 needs_review 队列失败: {e}")

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

    def _build_eval_key(self, query: str, session_id: str) -> str:
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"{session_id}:{query_hash}"

    def _commit_evaluated_key(self, eval_key: str) -> None:
        self._evaluated_keys[eval_key] = None
        if len(self._evaluated_keys) > 1000:
            while len(self._evaluated_keys) > 500:
                self._evaluated_keys.popitem(last=False)
        self._persist_evaluated_keys()

    def _reserve_eval_key(self, query: str, session_id: str) -> Optional[str]:
        """
        预占评估键（仅内存 pending），避免并发重复评估。
        终态成功后由 _commit_evaluated_key 持久化。
        """
        eval_key = self._build_eval_key(query, session_id)
        if eval_key in self._evaluated_keys or eval_key in self._pending_eval_keys:
            return None
        self._pending_eval_keys.add(eval_key)
        return eval_key

    def _release_eval_key(self, eval_key: Optional[str]) -> None:
        if eval_key:
            self._pending_eval_keys.discard(eval_key)

    def _check_duplicate(self, query: str, session_id: str) -> bool:
        """
        兼容旧路径：检查并立即写入去重键。
        仅用于测试与历史调用；在线评估主流程使用 reserve/commit 两阶段去重。
        """
        eval_key = self._build_eval_key(query, session_id)
        if eval_key in self._evaluated_keys:
            return True
        self._commit_evaluated_key(eval_key)
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

    @staticmethod
    def _normalize_error_reason(reason: Optional[str], fallback: str = "unknown") -> str:
        raw = (reason or "").strip()
        if not raw:
            raw = fallback
        normalized = raw.replace("\n", " ").replace("\r", " ")
        return normalized[:120]

    def _on_ragas_failure(self, reason: str) -> None:
        safe_reason = self._normalize_error_reason(reason, fallback="ragas_failed")
        self._metrics.ragas_failures += 1
        self._metrics.last_error = f"ragas:{safe_reason}"
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
                self._metrics.inflight += 1
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
                if self._metrics.inflight > 0:
                    self._metrics.inflight -= 1
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

        # 防重复评估（两阶段：pending -> terminal commit）
        eval_key = self._reserve_eval_key(query, session_id)
        if not eval_key:
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
                            self._on_ragas_failure(ragas_details or "empty_score")
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

            review_sample_id: Optional[str] = None
            if quality_status == "needs_review":
                eval_result.notes += " | needs_review=true"

            if self.config.visualize_only:
                if quality_status == "needs_review":
                    review_sample_id = self._enqueue_review_sample(
                        eval_result=eval_result,
                        custom_score=custom_score,
                        ragas_score=ragas_score,
                        timestamp=start_time,
                    )
                self._metrics.visualize_only_observed += 1
            else:
                if quality_status == "needs_review":
                    review_sample_id = self._enqueue_review_sample(
                        eval_result=eval_result,
                        custom_score=custom_score,
                        ragas_score=ragas_score,
                        timestamp=start_time,
                    )
                    print("  ⚠️ 需要人工审查 (needs_review)，等待人工审批后再落盘")
                else:
                    self.data_router.route_sample(eval_result)
                    if eval_result.data_quality_tier == DataQualityTier.REJECTED:
                        print(f"  ❌ 评分过低 (tier=rejected, score={final_score:.2f})，仅记录审计结果")
                    else:
                        print(
                            f"  ✓ 路由到 data_router "
                            f"(tier={eval_result.data_quality_tier.value}, score={final_score:.2f})"
                        )

            # 只有进入终态后才持久化 dedupe key，避免“已去重但无终态记录”。
            self._commit_evaluated_key(eval_key)

            score_metadata = {
                "session_id": session_id,
                "repo_url": repo_url,
                "query": query[:100],
                "quality_status": quality_status,
                "quality_tier": eval_result.data_quality_tier.value,
                "visualize_only": self.config.visualize_only,
            }
            if review_sample_id:
                score_metadata["review_sample_id"] = review_sample_id
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
                    "review_sample_id": review_sample_id,
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
        finally:
            self._release_eval_key(eval_key)

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
    ) -> str:
        """将待人工审核样本放入队列（审批前不落盘）。"""
        sample_id = f"sample_{uuid.uuid4().hex[:16]}"
        self.needs_review_queue.append(
            {
                "sample_id": sample_id,
                "eval_result": eval_result,
                "eval_result_dict": eval_result.to_dict(),
                "custom_score": custom_score,
                "ragas_score": ragas_score,
                "diff": abs(custom_score - (ragas_score if ragas_score is not None else custom_score)),
                "timestamp": timestamp.isoformat(),
                "routed": False,
            }
        )
        self._persist_review_queue()
        return sample_id

    @staticmethod
    def _extract_ragas_metric_value(result: Any, metric_names: tuple[str, ...]) -> Optional[float]:
        """从不同版本 ragas 返回结构中提取指标值。"""
        for metric_name in metric_names:
            try:
                value = result[metric_name]
                if isinstance(value, list):
                    if value:
                        return float(value[0])
                elif value is not None:
                    return float(value)
            except Exception:
                pass

        scores = getattr(result, "scores", None)
        if isinstance(scores, list) and scores:
            first = scores[0]
            if isinstance(first, dict):
                for metric_name in metric_names:
                    value = first.get(metric_name)
                    if value is not None:
                        return float(value)

        to_pandas = getattr(result, "to_pandas", None)
        if callable(to_pandas):
            try:
                frame = to_pandas()
                if len(frame) > 0:
                    row = frame.iloc[0]
                    for metric_name in metric_names:
                        if metric_name in row and row[metric_name] is not None:
                            return float(row[metric_name])
            except Exception:
                pass
        return None

    @staticmethod
    def _resolve_ragas_collection_metric(collection_module: Any) -> Optional[Any]:
        """
        解析 ragas.metrics.collections.* 导出的 metric 对象。
        新版某些发行版导出的 `metric` 仍是 module，需要回退旧导入路径。
        """
        candidate = getattr(collection_module, "metric", None)
        if candidate is None or isinstance(candidate, types.ModuleType):
            return None
        return candidate

    @contextmanager
    def _ragas_runtime_env(self):
        """
        为 Ragas 运行时补齐 OpenAI 兼容环境变量。
        - 已配置 OPENAI_API_KEY 时不覆盖。
        - 在 DeepSeek 场景下映射到 OpenAI 兼容变量，降低接线成本。
        """
        overrides: Dict[str, str] = {}
        if not os.getenv("OPENAI_API_KEY"):
            provider = settings.LLM_PROVIDER.lower()
            if provider == "deepseek" and settings.DEEPSEEK_API_KEY:
                overrides["OPENAI_API_KEY"] = settings.DEEPSEEK_API_KEY
                if settings.DEEPSEEK_BASE_URL:
                    overrides["OPENAI_BASE_URL"] = settings.DEEPSEEK_BASE_URL
            elif provider == "openai" and settings.OPENAI_API_KEY:
                overrides["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
                if settings.OPENAI_BASE_URL:
                    overrides["OPENAI_BASE_URL"] = settings.OPENAI_BASE_URL

        original: Dict[str, Optional[str]] = {}
        try:
            for key, value in overrides.items():
                original[key] = os.environ.get(key)
                os.environ[key] = value
            yield
        finally:
            for key in overrides:
                previous = original.get(key)
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

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
            from ragas import evaluate
            from datasets import Dataset

            faithfulness_metric = None
            answer_relevancy_metric = None
            try:
                from ragas.metrics.collections import faithfulness as ragas_faithfulness
                from ragas.metrics.collections import answer_relevancy as ragas_answer_relevancy
                faithfulness_metric = self._resolve_ragas_collection_metric(ragas_faithfulness)
                answer_relevancy_metric = self._resolve_ragas_collection_metric(ragas_answer_relevancy)
            except ImportError:
                pass

            # 兼容旧版 ragas 指标导入路径（通常为已初始化的 metric object）
            if faithfulness_metric is None or answer_relevancy_metric is None:
                from ragas.metrics import faithfulness as legacy_faithfulness
                from ragas.metrics import answer_relevancy as legacy_answer_relevancy
                if faithfulness_metric is None:
                    faithfulness_metric = legacy_faithfulness
                if answer_relevancy_metric is None:
                    answer_relevancy_metric = legacy_answer_relevancy

            # 构造 Ragas 数据集（Phase 4：Dataset API）
            dataset_dict = {
                "question": [query],
                "contexts": [[context]],
                "answer": [answer],
            }
            dataset = Dataset.from_dict(dataset_dict)

            def _run_eval(metrics: list[Any]):
                try:
                    return evaluate(
                        dataset=dataset,
                        metrics=metrics,
                        show_progress=False,
                        raise_exceptions=False,
                    )
                except TypeError:
                    return evaluate(dataset=dataset, metrics=metrics)

            with self._ragas_runtime_env():
                result = await asyncio.to_thread(
                    _run_eval,
                    [faithfulness_metric, answer_relevancy_metric],
                )

            # 提取分数（兼容不同版本返回结构）
            faithfulness_score = self._extract_ragas_metric_value(
                result,
                ("faithfulness",),
            )
            relevancy_score = self._extract_ragas_metric_value(
                result,
                ("answer_relevancy", "answer_relevance"),
            )

            # 若双指标均缺失，回退到单指标（faithfulness）保障可用性。
            if faithfulness_score is None and relevancy_score is None:
                with self._ragas_runtime_env():
                    fallback_result = await asyncio.to_thread(_run_eval, [faithfulness_metric])
                faithfulness_score = self._extract_ragas_metric_value(
                    fallback_result,
                    ("faithfulness",),
                )
                relevancy_score = faithfulness_score

            if faithfulness_score is None and relevancy_score is None:
                return None, "empty_score"
            if faithfulness_score is None:
                faithfulness_score = relevancy_score
            if relevancy_score is None:
                relevancy_score = faithfulness_score

            # 平均得分
            ragas_score = (faithfulness_score + relevancy_score) / 2

            details = f"Ragas: faithfulness={faithfulness_score:.3f}, relevancy={relevancy_score:.3f}"

            return ragas_score, details

        except ImportError:
            print("⚠️ Ragas 未安装，跳过 sanity check")
            return None, "ragas_not_installed"
        except Exception as e:
            print(f"⚠️ Ragas 评估异常: {e}")
            return None, f"error:{self._normalize_error_reason(str(e))}"

    def get_metrics(self) -> Dict[str, Any]:
        """获取可观测指标快照。"""
        payload = asdict(self._metrics)
        queue_size = self._eval_queue.qsize() if self._eval_queue else 0
        terminal_count = self._metrics.processed + self._metrics.failed
        payload.update(
            {
                "queue_enabled": self.config.queue_enabled,
                "queue_size": queue_size,
                "queue_maxsize": self._eval_queue.maxsize if self._eval_queue else 0,
                "worker_running": bool(self._worker_task and not self._worker_task.done()),
                "visualize_only": self.config.visualize_only,
                "ragas_circuit_open": self._is_ragas_circuit_open(),
                "ragas_consecutive_failures": self._ragas_consecutive_failures,
                "terminal_count": terminal_count,
                "is_idle": queue_size == 0 and self._metrics.inflight == 0,
            }
        )
        return payload

    def get_runtime_status(self) -> Dict[str, Any]:
        """获取运行时状态（用于仪表盘展示）。"""
        return {
            "queue": {
                "enabled": self.config.queue_enabled,
                "size": self._eval_queue.qsize() if self._eval_queue else 0,
                "inflight": self._metrics.inflight,
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
        self._persist_review_queue()

    def _find_review_item_index(self, sample_id: str) -> int:
        for idx, item in enumerate(self.needs_review_queue):
            if item.get("sample_id") == sample_id:
                return idx
        return -1

    def approve_sample_by_id(self, sample_id: str) -> tuple[bool, str]:
        """人工批准某个样本（按 sample_id，幂等）。"""
        if not sample_id:
            return False, "sample_id is required"

        recorded = self._review_decisions.get(sample_id)
        if recorded:
            if recorded.get("decision") == "approved":
                return True, f"✅ 样本 {sample_id} 已批准（幂等）"
            return False, f"❌ 样本 {sample_id} 已被拒绝，不能重复批准"

        index = self._find_review_item_index(sample_id)
        if index < 0:
            return False, f"样本 {sample_id} 不存在"

        item = self.needs_review_queue.pop(index)
        if (not self.config.visualize_only) and (not item.get("routed", False)):
            self.data_router.route_sample(item["eval_result"])
            item["routed"] = True

        self._review_decisions[sample_id] = {
            "decision": "approved",
            "timestamp": datetime.now().isoformat(),
            "query": item.get("eval_result").query[:120] if item.get("eval_result") else "",
        }
        self._persist_review_queue()
        self._persist_review_decisions()
        message = f"✅ 样本 {sample_id} 已批准"
        print(message)
        return True, message

    def reject_sample_by_id(self, sample_id: str) -> tuple[bool, str]:
        """人工拒绝某个样本（按 sample_id，幂等）。"""
        if not sample_id:
            return False, "sample_id is required"

        recorded = self._review_decisions.get(sample_id)
        if recorded:
            if recorded.get("decision") == "rejected":
                return True, f"❌ 样本 {sample_id} 已拒绝（幂等）"
            return False, f"⚠️ 样本 {sample_id} 已批准，不能重复拒绝"

        index = self._find_review_item_index(sample_id)
        if index < 0:
            return False, f"样本 {sample_id} 不存在"

        item = self.needs_review_queue.pop(index)
        self._review_decisions[sample_id] = {
            "decision": "rejected",
            "timestamp": datetime.now().isoformat(),
            "query": item.get("eval_result").query[:120] if item.get("eval_result") else "",
        }
        self._persist_review_queue()
        self._persist_review_decisions()
        message = f"❌ 样本 {sample_id} 已拒绝"
        print(message)
        return True, message

    def approve_sample(self, index: int) -> tuple[bool, str]:
        """人工批准某个样本（兼容 index 方式）。"""
        if 0 <= index < len(self.needs_review_queue):
            sample_id = self.needs_review_queue[index].get("sample_id")
            return self.approve_sample_by_id(sample_id)
        return False, f"样本 index {index} 不存在"

    def reject_sample(self, index: int) -> tuple[bool, str]:
        """人工拒绝某个样本（兼容 index 方式）。"""
        if 0 <= index < len(self.needs_review_queue):
            sample_id = self.needs_review_queue[index].get("sample_id")
            return self.reject_sample_by_id(sample_id)
        return False, f"样本 index {index} 不存在"


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
