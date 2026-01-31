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
from typing import Dict, Any, Optional, List, Callable
from functools import wraps
from datetime import datetime
from dataclasses import dataclass


# ============================================================================
# 第一部分: Langfuse客户端初始化 (可选)
# ============================================================================

try:
    from langfuse import Langfuse
    from langfuse.decorators import observe, langfuse_context
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False


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
                print("⚠️ Langfuse not installed. Install with: pip install langfuse. Falling back to local logging.")
                self.config.backend = "local"
            else:
                try:
                    self.langfuse_client = Langfuse(
                        host=self.config.langfuse_host,
                        public_key=self.config.langfuse_public_key,
                        secret_key=self.config.langfuse_secret_key,
                        enabled=True,
                        debug=False
                    )
                    print("✅ Langfuse client initialized successfully")
                except Exception as e:
                    print(f"⚠️ Langfuse initialization failed: {e}. Falling back to local logging.")
                    self.config.backend = "local"
        
        # 创建本地日志目录
        os.makedirs(self.config.local_log_dir, exist_ok=True)
    
    def start_trace(self, trace_name: str, session_id: str, metadata: Dict = None) -> str:
        """启动一个新的追踪链"""
        import uuid
        trace_id = str(uuid.uuid4())
        self.current_trace_id = trace_id
        
        if self.langfuse_client:
            self.langfuse_client.trace(
                name=trace_name,
                input=metadata or {},
                session_id=session_id
            )
            print(f"📍 Trace started: {trace_id}")
        else:
            self._log_locally("trace_start", {
                "trace_id": trace_id,
                "name": trace_name,
                "session_id": session_id,
                "metadata": metadata,
                "timestamp": datetime.now().isoformat()
            })
        
        return trace_id
    
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
            "metadata": metadata or {}
        }
        
        if self.langfuse_client:
            try:
                # Langfuse:记录到云端
                self.langfuse_client.span(
                    name=span_name,
                    input=input_data,
                    output=output_data,
                    metadata={
                        "operation": operation,
                        "latency_ms": latency_ms,
                        **(token_usage or {}),
                        **(metadata or {})
                    }
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
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self.langfuse_client.event(
                    name=f"tool_call:{tool_name}",
                    input=parameters,
                    output=result,
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
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self.langfuse_client.event(
                    name="retrieval_debug",
                    input={"query": query},
                    output={"files": retrieved_files},
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
            "metadata": metadata or {}
        }
        
        if self.langfuse_client:
            try:
                self.langfuse_client.generation(
                    name="llm_generation",
                    model=model,
                    input=prompt_messages,
                    output=generated_text[:1000] if generated_text else "",
                    usage={
                        "prompt_tokens": prompt_tokens or 0,
                        "completion_tokens": completion_tokens or 0,
                        "total_tokens": total_tokens or 0
                    },
                    metadata={
                        "ttft_ms": ttft_ms,
                        "total_latency_ms": total_latency_ms,
                        "is_streaming": is_streaming,
                        **(metadata or {})
                    }
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
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        
        if self.langfuse_client:
            try:
                self.langfuse_client.event(
                    name="ttft",
                    input={},
                    output={"ttft_ms": ttft_ms},
                    metadata=ttft_record
                )
            except Exception as e:
                print(f"⚠️ Failed to record TTFT: {e}")
        
        self._log_locally("ttft", ttft_record)

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
            "timestamp": datetime.now().isoformat()
        }
        
        if self.langfuse_client:
            try:
                self.langfuse_client.event(
                    name=event_name,
                    input={},
                    output=event_data or {},
                    metadata=event_data or {}
                )
            except Exception as e:
                print(f"⚠️ Failed to record event '{event_name}': {e}")
        
        self._log_locally("event", event_record)
    
    def _log_locally(self, log_type: str, data: Dict) -> None:
        """本地日志记录"""
        log_file = os.path.join(
            self.config.local_log_dir,
            f"{log_type}_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + '\n')
    
    def get_trace_url(self, trace_id: str = None) -> str:
        """获取Langfuse中该trace的URL (用于前端跳转)"""
        if not self.langfuse_client or not trace_id:
            return None
        
        # Langfuse云端URL格式
        return f"{self.config.langfuse_host}/traces/{trace_id}"


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
    backend="langfuse" if LANGFUSE_AVAILABLE else "local"
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
