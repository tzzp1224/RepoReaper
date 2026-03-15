# 文件路径: app/services/tracing_service.py
"""
Langfuse集成模块 - 用于端到端追踪和观测

核心能力:
1. 自动捕获每一步的延迟、Token成本、输入输出
2. 记录完整的调用链路: Query -> Rewrite -> Retrieval -> Generation
3. 记录Tool调用和参数
4. 集成到评估流程

Langfuse支持:
- 本地部署 (docker run ... langfuse)
- 云端托管 (app.langfuse.com)

Author: Dexter
Date: 2025-01-27
"""

import time
import json
import os
import sys
import inspect
from typing import Dict, Any, Optional, List, Callable
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from datetime import datetime
from dataclasses import dataclass


# ============================================================================
# 第一部分: Langfuse客户端初始化 (可选)
# ============================================================================

LANGFUSE_IMPORT_ERROR = None
_LANGFUSE_ENABLED_ENV = os.getenv("LANGFUSE_ENABLED", "true").strip().lower()
_LANGFUSE_ENABLED = _LANGFUSE_ENABLED_ENV not in {"0", "false", "no", "off"}
# 检查Python版本兼容性
PYTHON_VERSION = sys.version_info[:2]
LANGFUSE_PYTHON_SUPPORTED = PYTHON_VERSION < (3, 14)
LANGFUSE_DISABLED_REASON = None

if _LANGFUSE_ENABLED:
    # 版本门控
    if LANGFUSE_PYTHON_SUPPORTED:
        try:
            from langfuse import Langfuse
            LANGFUSE_AVAILABLE = True
        except ModuleNotFoundError as e:
            LANGFUSE_IMPORT_ERROR = e
            if e.name=="langfuse":
                LANGFUSE_DISABLED_REASON = f"⚠️ Langfuse not installed. Install with: pip install langfuse. Falling back to local logging."
            else:
                LANGFUSE_DISABLED_REASON = f"⚠️ Langfuse import failed: {e}. Falling back to local logging."
            LANGFUSE_AVAILABLE = False
        except Exception as e:
            LANGFUSE_IMPORT_ERROR = e
            LANGFUSE_DISABLED_REASON = f"⚠️ Langfuse import failed: {e}. Falling back to local logging."
            LANGFUSE_AVAILABLE = False

    else:
        LANGFUSE_AVAILABLE = False
        LANGFUSE_DISABLED_REASON = f"⚠️ Langfuse disabled on Python 3.14+ due to SDK compatibility issues, but current version is {PYTHON_VERSION[0]}.{PYTHON_VERSION[1]}. Falling back to local logging."
else:
    LANGFUSE_AVAILABLE = False
    LANGFUSE_DISABLED_REASON = "⚠️ Langfuse disabled by LANGFUSE_ENABLED. Falling back to local logging."

_TRACE_ID_CTX: ContextVar[Optional[str]] = ContextVar("langfuse_trace_id", default=None)
_SESSION_ID_CTX: ContextVar[Optional[str]] = ContextVar("langfuse_session_id", default=None)


@dataclass
class TracingConfig:
    """追踪配置"""
    enabled: bool = True
    backend: str = "langfuse"  # "langfuse" or "local"
    langfuse_host: str = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    capture_token_usage: bool = True
    capture_latency: bool = True
    local_log_dir: str = "logs/traces"


class TracingService:
    """
    统一的追踪服务
    支持Langfuse和本地日志两种后端
    """
    
    def __init__(self, config: TracingConfig = None):
        self.config = config or TracingConfig()
        self.langfuse_client = None
        self.current_trace_id = None
        
        if self.config.enabled and self.config.backend == "langfuse":
            if not LANGFUSE_AVAILABLE:
                # 打印门控提示信息
                if LANGFUSE_DISABLED_REASON:
                    print(LANGFUSE_DISABLED_REASON)
                else:
                    print("⚠️ Langfuse unavailable. Falling back to local logging.")
                self.config.backend = "local"
            else:
                try:
                    self.langfuse_client = Langfuse(
                        host=self.config.langfuse_host,
                        public_key=self.config.langfuse_public_key,
                        secret_key=self.config.langfuse_secret_key,
                        # 报错 Langfuse.__init__() got an unexpected keyword argument enabled'.
                        # 暂时不处理该参数确保langfuse可用
                        # enabled=True,
                        debug=False
                    )
                    self._log_langfuse_capabilities()
                    print("✅ Langfuse client initialized successfully")
                except Exception as e:
                    print(f"⚠️ Langfuse initialization failed: {e}. Falling back to local logging.")
                    self.config.backend = "local"
        
        # 创建本地日志目录
        os.makedirs(self.config.local_log_dir, exist_ok=True)

    def _log_langfuse_capabilities(self) -> None:
        """打印当前 Langfuse 客户端能力，便于排查 SDK 版本差异。"""
        if not self.langfuse_client:
            return
        capabilities = {
            "trace": hasattr(self.langfuse_client, "trace"),
            "span": hasattr(self.langfuse_client, "span"),
            "event": hasattr(self.langfuse_client, "event"),
            "generation": hasattr(self.langfuse_client, "generation"),
            "create_event": hasattr(self.langfuse_client, "create_event"),
            "create_score": hasattr(self.langfuse_client, "create_score"),
            "score_current_trace": hasattr(self.langfuse_client, "score_current_trace"),
            "start_span": hasattr(self.langfuse_client, "start_span"),
            "start_generation": hasattr(self.langfuse_client, "start_generation"),
            "start_observation": hasattr(self.langfuse_client, "start_observation"),
            "create_trace_id": hasattr(self.langfuse_client, "create_trace_id"),
        }
        print(f"🔎 Langfuse capabilities: {capabilities}")

    def get_current_trace_id(self) -> Optional[str]:
        """获取当前上下文中的 trace_id。"""
        return _TRACE_ID_CTX.get()

    def get_current_session_id(self) -> Optional[str]:
        """获取当前上下文中的 session_id。"""
        return _SESSION_ID_CTX.get()

    def _set_trace_context(self, trace_id: Optional[str], session_id: Optional[str] = None) -> None:
        """设置当前上下文的 trace/session。"""
        _TRACE_ID_CTX.set(trace_id)
        _SESSION_ID_CTX.set(session_id)
        self.current_trace_id = trace_id

    def clear_trace_context(self) -> None:
        """清空当前上下文的 trace/session。"""
        self._set_trace_context(None, None)

    @contextmanager
    def trace_scope(self, trace_id: Optional[str], session_id: Optional[str] = None):
        """临时绑定 trace/session 上下文，退出时自动恢复。"""
        trace_token = _TRACE_ID_CTX.set(trace_id)
        session_token = _SESSION_ID_CTX.set(session_id)
        self.current_trace_id = trace_id
        try:
            yield
        finally:
            _TRACE_ID_CTX.reset(trace_token)
            _SESSION_ID_CTX.reset(session_token)
            self.current_trace_id = _TRACE_ID_CTX.get()

    def _trace_context_payload(self) -> Optional[Dict[str, str]]:
        """构建 Langfuse trace_context。"""
        trace_id = self.get_current_trace_id()
        if not trace_id:
            return None
        return {"trace_id": trace_id}

    def _with_trace_metadata(self, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """将 trace/session 信息补充到 metadata。"""
        payload = dict(metadata or {})
        trace_id = self.get_current_trace_id()
        session_id = self.get_current_session_id()
        if trace_id and "trace_id" not in payload:
            payload["trace_id"] = trace_id
        if session_id and "session_id" not in payload:
            payload["session_id"] = session_id
        return payload

    def _invoke_langfuse(self, method_name: str, **kwargs):
        """调用 Langfuse 方法并自动过滤不支持参数。"""
        if not self.langfuse_client:
            return None, False

        method = getattr(self.langfuse_client, method_name, None)
        if not callable(method):
            return None, False

        call_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        try:
            signature = inspect.signature(method)
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            if not accepts_kwargs:
                call_kwargs = {
                    k: v
                    for k, v in call_kwargs.items()
                    if k in signature.parameters
                }
        except (TypeError, ValueError):
            pass

        return method(**call_kwargs), True

    @staticmethod
    def _end_observation(observation: Any) -> None:
        """结束 start_observation 返回的 observation。"""
        if observation is None:
            return
        end_method = getattr(observation, "end", None)
        if callable(end_method):
            end_method()

    def _emit_event_compat(self, name: str, input_data: Any, output_data: Any, metadata: Dict) -> None:
        """兼容不同 SDK 版本的事件上报 API。"""
        if not self.langfuse_client:
            return

        event_metadata = self._with_trace_metadata(metadata)
        trace_context = self._trace_context_payload()
        trace_id = self.get_current_trace_id()

        _, called = self._invoke_langfuse(
            "event",
            name=name,
            input=input_data,
            output=output_data,
            metadata=event_metadata,
            trace_id=trace_id,
            trace_context=trace_context,
        )
        if called:
            return

        _, called = self._invoke_langfuse(
            "create_event",
            name=name,
            input=input_data,
            output=output_data,
            metadata=event_metadata,
            trace_context=trace_context,
            trace_id=trace_id,
        )
        if called:
            return

        observation, called = self._invoke_langfuse(
            "start_observation",
            trace_context=trace_context,
            name=name,
            as_type="span",
            input=input_data,
            output=output_data,
            metadata=event_metadata,
        )
        if called:
            self._end_observation(observation)
            return

        raise AttributeError("Langfuse client has no compatible event API")
    
    def start_trace(self, trace_name: str, session_id: str, metadata: Dict = None) -> str:
        """启动一个新的追踪链"""
        import uuid

        trace_id = str(uuid.uuid4())
        if self.langfuse_client:
            try:
                generated, called = self._invoke_langfuse("create_trace_id")
                if called and generated:
                    trace_id = str(generated)
            except Exception:
                pass

        self._set_trace_context(trace_id=trace_id, session_id=session_id)
        
        if self.langfuse_client:
            try:
                trace_payload = self._with_trace_metadata(
                    {"session_id": session_id, "trace_name": trace_name}
                )

                _, called = self._invoke_langfuse(
                    "trace",
                    id=trace_id,
                    trace_id=trace_id,
                    name=trace_name,
                    input=metadata or {},
                    metadata=trace_payload,
                    session_id=session_id,
                )
                if not called:
                    self._emit_event_compat(
                        name=f"trace_start:{trace_name}",
                        input_data=metadata or {},
                        output_data={"trace_id": trace_id},
                        metadata=trace_payload,
                    )
                print(f"📍 Trace started: {trace_id}")
            except Exception as e:
                print(f"⚠️ Failed to start trace in Langfuse: {e}")
        else:
            self._log_locally("trace_start", {
                "trace_id": trace_id,
                "name": trace_name,
                "session_id": session_id,
                "metadata": metadata,
                "timestamp": datetime.now().isoformat()
            })
        
        return trace_id

    def end_trace(self, metadata: Dict[str, Any] = None) -> None:
        """结束当前 trace 并清理上下文。"""
        trace_id = self.get_current_trace_id()
        if trace_id:
            try:
                self.add_event("trace_end", metadata or {})
            except Exception as e:
                print(f"⚠️ Failed to end trace cleanly: {e}")
        self.clear_trace_context()
    
    def record_span(
        self,
        span_name: str,
        operation: str,
        input_data: Any,
        output_data: Any,
        latency_ms: float,
        token_usage: Dict[str, int] = None,
        metadata: Dict = None
    ) -> None:
        """记录一个操作的跨度"""
        
        span_record = {
            "span_name": span_name,
            "operation": operation,
            "latency_ms": latency_ms,
            "timestamp": datetime.now().isoformat(),
            "token_usage": token_usage or {},
            "metadata": self._with_trace_metadata(metadata)
        }
        
        if self.langfuse_client:
            try:
                # Langfuse:记录到云端
                span_metadata = {
                    "operation": operation,
                    "latency_ms": latency_ms,
                    **(token_usage or {}),
                    **self._with_trace_metadata(metadata),
                }
                trace_context = self._trace_context_payload()
                trace_id = self.get_current_trace_id()

                observation, called = self._invoke_langfuse(
                    "start_observation",
                    trace_context=trace_context,
                    name=span_name,
                    as_type="span",
                    input=input_data,
                    output=output_data,
                    metadata=span_metadata,
                )
                if called:
                    self._end_observation(observation)
                else:
                    _, span_called = self._invoke_langfuse(
                        "span",
                        name=span_name,
                        input=input_data,
                        output=output_data,
                        metadata=span_metadata,
                        trace_id=trace_id,
                        trace_context=trace_context,
                    )
                    if not span_called:
                        self._emit_event_compat(
                            name=f"span:{span_name}",
                            input_data=input_data,
                            output_data=output_data,
                            metadata=span_metadata
                        )
            except Exception as e:
                print(f"⚠️ Failed to record span to Langfuse: {e}")
        
        # 本地日志
        self._log_locally("span", span_record)

    def record_tool_call(
        self,
        tool_name: str,
        parameters: Dict,
        result: Any,
        latency_ms: float,
        success: bool,
        error: str = None
    ) -> None:
        """记录工具调用"""
        
        tool_record = {
            "tool_name": tool_name,
            "parameters": parameters,
            "result": str(result)[:500] if result else None,
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
            "trace_id": self.get_current_trace_id(),
            "session_id": self.get_current_session_id(),
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self._emit_event_compat(
                    name=f"tool_call:{tool_name}",
                    input_data=parameters,
                    output_data=result,
                    metadata={
                        "latency_ms": latency_ms,
                        "success": success,
                        "error": error
                    }
                )
            except Exception as e:
                print(f"⚠️ Failed to record tool call: {e}")
        
        self._log_locally("tool_call", tool_record)
    
    def record_retrieval_debug(
        self,
        query: str,
        retrieved_files: List[str],
        vector_scores: List[float],
        bm25_scores: List[float],
        latency_ms: float
    ) -> None:
        """记录检索过程的调试信息"""
        
        retrieval_record = {
            "query": query,
            "retrieved_count": len(retrieved_files),
            "files": retrieved_files,
            "vector_scores": vector_scores,
            "bm25_scores": bm25_scores,
            "latency_ms": latency_ms,
            "trace_id": self.get_current_trace_id(),
            "session_id": self.get_current_session_id(),
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self._emit_event_compat(
                    name="retrieval_debug",
                    input_data={"query": query},
                    output_data={"files": retrieved_files},
                    metadata=retrieval_record
                )
            except Exception as e:
                print(f"⚠️ Failed to record retrieval debug: {e}")
        
        self._log_locally("retrieval", retrieval_record)
    
    def record_llm_generation(
        self,
        model: str,
        prompt_messages: List[Dict],
        generated_text: str,
        ttft_ms: float = None,
        total_latency_ms: float = None,
        prompt_tokens: int = None,
        completion_tokens: int = None,
        total_tokens: int = None,
        is_streaming: bool = False,
        metadata: Dict = None
    ) -> None:
        """
        记录 LLM 生成的完整信息，包括 Token 消耗和 TTFT
        
        Args:
            model: 模型名称 (如 "gpt-4", "claude-3")
            prompt_messages: 发送给 LLM 的消息列表
            generated_text: 生成的文本（可截断）
            ttft_ms: Time To First Token，首 token 延迟（毫秒）
            total_latency_ms: 总生成延迟（毫秒）
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            total_tokens: 总 token 数
            is_streaming: 是否流式输出
            metadata: 额外元数据
        """
        llm_record = {
            "model": model,
            "is_streaming": is_streaming,
            "prompt_preview": str(prompt_messages)[:500],  # 截断避免日志过大
            "generated_preview": generated_text[:500] if generated_text else "",
            "generated_length": len(generated_text) if generated_text else 0,
            # Token 统计
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            },
            # 延迟统计
            "latency": {
                "ttft_ms": ttft_ms,  # Time To First Token
                "total_ms": total_latency_ms,
                "tokens_per_second": round(completion_tokens / (total_latency_ms / 1000), 2) 
                    if completion_tokens and total_latency_ms and total_latency_ms > 0 else None
            },
            "timestamp": datetime.now().isoformat(),
            "trace_id": self.get_current_trace_id(),
            "session_id": self.get_current_session_id(),
            "metadata": self._with_trace_metadata(metadata),
        }
        
        if self.langfuse_client:
            try:
                gen_metadata = {
                    "ttft_ms": ttft_ms,
                    "total_latency_ms": total_latency_ms,
                    "is_streaming": is_streaming,
                    **self._with_trace_metadata(metadata),
                }
                usage_details = {
                    "input": prompt_tokens or 0,
                    "output": completion_tokens or 0,
                    "total": total_tokens or 0,
                }
                trace_context = self._trace_context_payload()
                trace_id = self.get_current_trace_id()

                observation, called = self._invoke_langfuse(
                    "start_observation",
                    trace_context=trace_context,
                    name="llm_generation",
                    as_type="generation",
                    model=model,
                    input=prompt_messages,
                    output=generated_text[:1000] if generated_text else "",
                    metadata=gen_metadata,
                    usage_details=usage_details,
                )
                if called:
                    self._end_observation(observation)
                else:
                    _, generation_called = self._invoke_langfuse(
                        "generation",
                        name="llm_generation",
                        model=model,
                        input=prompt_messages,
                        output=generated_text[:1000] if generated_text else "",
                        usage={
                            "prompt_tokens": prompt_tokens or 0,
                            "completion_tokens": completion_tokens or 0,
                            "total_tokens": total_tokens or 0
                        },
                        metadata=gen_metadata,
                        trace_id=trace_id,
                        trace_context=trace_context,
                    )
                    if not generation_called:
                        self._emit_event_compat(
                            name="llm_generation",
                            input_data={"model": model, "messages": prompt_messages},
                            output_data={
                                "text": generated_text[:1000] if generated_text else "",
                                "usage": {
                                    "prompt_tokens": prompt_tokens or 0,
                                    "completion_tokens": completion_tokens or 0,
                                    "total_tokens": total_tokens or 0
                                }
                            },
                            metadata=gen_metadata
                        )
            except Exception as e:
                print(f"⚠️ Failed to record LLM generation to Langfuse: {e}")
        
        self._log_locally("llm_generation", llm_record)
    
    def record_ttft(self, ttft_ms: float, model: str = None, metadata: Dict = None) -> None:
        """
        单独记录 TTFT (Time To First Token)
        用于流式生成时在收到第一个 token 时立即记录
        
        Args:
            ttft_ms: 首 token 延迟（毫秒）
            model: 模型名称
            metadata: 额外元数据
        """
        ttft_record = {
            "ttft_ms": ttft_ms,
            "model": model,
            "trace_id": self.get_current_trace_id(),
            "session_id": self.get_current_session_id(),
            "timestamp": datetime.now().isoformat(),
            "metadata": self._with_trace_metadata(metadata),
        }
        
        if self.langfuse_client:
            try:
                self._emit_event_compat(
                    name="ttft",
                    input_data={},
                    output_data={"ttft_ms": ttft_ms},
                    metadata=ttft_record
                )
            except Exception as e:
                print(f"⚠️ Failed to record TTFT: {e}")
        
        self._log_locally("ttft", ttft_record)

    def record_score(
        self,
        score_name: str,
        value: Any,
        data_type: str = "NUMERIC",
        comment: str = None,
        metadata: Dict[str, Any] = None,
        trace_id: str = None,
        session_id: str = None,
        observation_id: str = None,
    ) -> None:
        """记录评估分数到 Langfuse（失败自动降级）。"""
        effective_trace_id = trace_id or self.get_current_trace_id()
        effective_session_id = session_id or self.get_current_session_id()

        score_record = {
            "score_name": score_name,
            "value": value,
            "data_type": data_type,
            "comment": comment,
            "trace_id": effective_trace_id,
            "session_id": effective_session_id,
            "observation_id": observation_id,
            "timestamp": datetime.now().isoformat(),
            "metadata": self._with_trace_metadata(metadata),
        }

        if self.langfuse_client:
            try:
                score_metadata = self._with_trace_metadata(metadata)

                # 优先绑定当前 trace，保证在 trace_scope 中自动关联。
                if trace_id is None and observation_id is None and effective_trace_id:
                    _, called = self._invoke_langfuse(
                        "score_current_trace",
                        name=score_name,
                        value=value,
                        data_type=data_type,
                        comment=comment,
                        metadata=score_metadata,
                    )
                    if called:
                        self._log_locally("score", score_record)
                        return

                _, called = self._invoke_langfuse(
                    "create_score",
                    name=score_name,
                    value=value,
                    data_type=data_type,
                    comment=comment,
                    metadata=score_metadata,
                    trace_id=effective_trace_id,
                    session_id=effective_session_id,
                    observation_id=observation_id,
                )
                if not called:
                    raise AttributeError("Langfuse client has no compatible score API")
            except Exception as e:
                print(f"⚠️ Failed to record score '{score_name}': {e}")

        self._log_locally("score", score_record)

    def add_event(self, event_name: str, event_data: Dict[str, Any] = None) -> None:
        """
        添加事件记录
        
        Args:
            event_name: 事件名称 (如 "repo_map_generated", "file_read_failed" 等)
            event_data: 事件相关数据
        """
        event_record = {
            "event_name": event_name,
            "event_data": event_data or {},
            "trace_id": self.get_current_trace_id(),
            "session_id": self.get_current_session_id(),
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self._emit_event_compat(
                    name=event_name,
                    input_data={},
                    output_data=event_data or {},
                    metadata=self._with_trace_metadata(event_data)
                )
            except Exception as e:
                print(f"⚠️ Failed to record event '{event_name}': {e}")
        
        self._log_locally("event", event_record)
    
    def _log_locally(self, log_type: str, data: Dict) -> None:
        """本地日志记录"""
        os.makedirs(self.config.local_log_dir, exist_ok=True)
        log_file = os.path.join(
            self.config.local_log_dir,
            f"{log_type}_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + '\n')
    
    def get_trace_url(self, trace_id: str = None) -> str:
        """获取Langfuse中该trace的URL (用于前端跳转)"""
        if not self.langfuse_client:
            return None

        effective_trace_id = trace_id or self.get_current_trace_id()
        if not effective_trace_id:
            return None

        try:
            url, called = self._invoke_langfuse("get_trace_url", trace_id=effective_trace_id)
            if called and url:
                return str(url)
        except Exception:
            pass

        return f"{self.config.langfuse_host}/traces/{effective_trace_id}"


# ============================================================================
# 第二部分: 装饰器 - 自动追踪
# ============================================================================

def traced(operation_name: str, capture_args: List[str] = None):
    """
    装饰器: 自动为被装饰函数添加追踪
    
    使用示例:
    @traced("query_rewrite", capture_args=["user_query"])
    async def rewrite_query(user_query: str):
        ...
    """
    
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            
            # 捕获输入参数
            input_data = {}
            if capture_args:
                for arg_name in capture_args:
                    if arg_name in kwargs:
                        input_data[arg_name] = kwargs[arg_name]
            
            try:
                result = await func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000
                
                # 记录跨度
                tracing_service.record_span(
                    span_name=operation_name,
                    operation=func.__name__,
                    input_data=input_data,
                    output_data={"success": True},
                    latency_ms=latency_ms
                )
                
                return result
            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                tracing_service.record_span(
                    span_name=operation_name,
                    operation=func.__name__,
                    input_data=input_data,
                    output_data={"error": str(e)},
                    latency_ms=latency_ms,
                    metadata={"error": True}
                )
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            
            input_data = {}
            if capture_args:
                for arg_name in capture_args:
                    if arg_name in kwargs:
                        input_data[arg_name] = kwargs[arg_name]
            
            try:
                result = func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000
                
                tracing_service.record_span(
                    span_name=operation_name,
                    operation=func.__name__,
                    input_data=input_data,
                    output_data={"success": True},
                    latency_ms=latency_ms
                )
                
                return result
            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                tracing_service.record_span(
                    span_name=operation_name,
                    operation=func.__name__,
                    input_data=input_data,
                    output_data={"error": str(e)},
                    latency_ms=latency_ms,
                    metadata={"error": True}
                )
                raise
        
        # 判断是async还是sync
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# ============================================================================
# 第三部分: 全局实例
# ============================================================================

tracing_config = TracingConfig(
    enabled=True,
    # backend="langfuse" if LANGFUSE_AVAILABLE else "local"
    # 改为用户是否启用langfuse
    backend="langfuse" if _LANGFUSE_ENABLED else "local",
)

tracing_service = TracingService(config=tracing_config)


# ============================================================================
# 第四部分: 集成示例 (如何在agent_service.py中使用)
# ============================================================================

"""
在你的agent_service.py中添加:

1. 导入追踪服务:
   from app.services.tracing_service import tracing_service

2. 在agent_stream函数开始:
   trace_id = tracing_service.start_trace(
       trace_name="github_agent_analysis",
       session_id=session_id,
       metadata={"repo_url": repo_url, "language": language}
   )

3. 在generate_repo_map函数周围:
   start_time = time.time()
   file_tree_str, mapped_files = await generate_repo_map(repo_url, file_list, limit=limit)
   latency_ms = (time.time() - start_time) * 1000
   
   tracing_service.record_span(
       span_name="generate_repo_map",
       operation="repo_mapping",
       input_data={"file_count": len(file_list), "limit": limit},
       output_data={"files_in_map": len(mapped_files)},
       latency_ms=latency_ms
   )

4. 在process_single_file中记录检索:
   tracing_service.record_retrieval_debug(
       query=search_query,
       retrieved_files=valid_files,
       vector_scores=vector_scores,
       bm25_scores=bm25_scores,
       latency_ms=search_latency
   )

5. 工具调用记录:
   start_time = time.time()
   try:
       result = get_file_content(repo_url, file_path)
       tracing_service.record_tool_call(
           tool_name="get_file_content",
           parameters={"file_path": file_path},
           result=result[:100] if result else None,
           latency_ms=(time.time() - start_time) * 1000,
           success=True
       )
   except Exception as e:
       tracing_service.record_tool_call(
           tool_name="get_file_content",
           parameters={"file_path": file_path},
           result=None,
           latency_ms=(time.time() - start_time) * 1000,
           success=False,
           error=str(e)
       )
"""

import asyncio
