# 文件路径: app/utils/retry.py
"""
LLM 调用重试机制

使用 tenacity 库实现智能重试策略:
- 指数退避 (Exponential Backoff)
- 可重试异常识别
- 最大重试次数限制
- 详细日志记录
"""

import logging
from typing import Callable, Type, Tuple, Any
from functools import wraps

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log,
    RetryError,
)

# 配置日志
logger = logging.getLogger("llm_retry")
logger.setLevel(logging.INFO)


# ============================================================================
# 可重试的异常类型定义
# ============================================================================

# 网络/临时性错误 - 应该重试
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# 尝试导入各 SDK 的异常类型
try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )
    RETRYABLE_EXCEPTIONS = RETRYABLE_EXCEPTIONS + (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )
except ImportError:
    pass

try:
    from anthropic import (
        APIConnectionError as AnthropicConnectionError,
        APITimeoutError as AnthropicTimeoutError,
        RateLimitError as AnthropicRateLimitError,
        InternalServerError as AnthropicServerError,
    )
    RETRYABLE_EXCEPTIONS = RETRYABLE_EXCEPTIONS + (
        AnthropicConnectionError,
        AnthropicTimeoutError,
        AnthropicRateLimitError,
        AnthropicServerError,
    )
except ImportError:
    pass

try:
    import httpx
    RETRYABLE_EXCEPTIONS = RETRYABLE_EXCEPTIONS + (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
    )
except ImportError:
    pass


# ============================================================================
# 重试配置
# ============================================================================

class RetryConfig:
    """重试配置"""
    MAX_ATTEMPTS: int = 3                    # 最大重试次数
    MIN_WAIT_SECONDS: float = 1.0           # 最小等待时间
    MAX_WAIT_SECONDS: float = 30.0          # 最大等待时间
    EXPONENTIAL_MULTIPLIER: float = 2.0     # 指数退避乘数


# ============================================================================
# 重试装饰器
# ============================================================================

def create_retry_decorator(
    max_attempts: int = RetryConfig.MAX_ATTEMPTS,
    min_wait: float = RetryConfig.MIN_WAIT_SECONDS,
    max_wait: float = RetryConfig.MAX_WAIT_SECONDS,
):
    """
    创建 LLM 调用重试装饰器
    
    Args:
        max_attempts: 最大重试次数
        min_wait: 最小等待时间 (秒)
        max_wait: 最大等待时间 (秒)
        
    Returns:
        tenacity retry 装饰器
    """
    return retry(
        # 重试条件: 仅对可重试异常进行重试
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        # 停止条件: 达到最大重试次数
        stop=stop_after_attempt(max_attempts),
        # 等待策略: 指数退避
        wait=wait_exponential(
            multiplier=RetryConfig.EXPONENTIAL_MULTIPLIER,
            min=min_wait,
            max=max_wait,
        ),
        # 日志: 重试前记录
        before_sleep=before_sleep_log(logger, logging.WARNING),
        # 日志: 重试后记录
        after=after_log(logger, logging.DEBUG),
        # 重新抛出最后一个异常
        reraise=True,
    )


# 默认的重试装饰器实例
llm_retry = create_retry_decorator()


def with_retry(func: Callable) -> Callable:
    """
    为异步函数添加重试能力的装饰器
    
    Usage:
        @with_retry
        async def call_llm(...):
            ...
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        @llm_retry
        async def _inner():
            return await func(*args, **kwargs)
        return await _inner()
    return wrapper


# ============================================================================
# 便捷函数
# ============================================================================

async def retry_async(
    coro_func: Callable,
    *args,
    max_attempts: int = RetryConfig.MAX_ATTEMPTS,
    **kwargs
) -> Any:
    """
    带重试的异步调用
    
    Usage:
        result = await retry_async(
            client.chat.completions.create,
            model="gpt-4",
            messages=[...]
        )
    """
    decorator = create_retry_decorator(max_attempts=max_attempts)
    
    @decorator
    async def _call():
        return await coro_func(*args, **kwargs)
    
    return await _call()


def is_retryable_error(error: Exception) -> bool:
    """判断异常是否可重试"""
    return isinstance(error, RETRYABLE_EXCEPTIONS)


def log_retry_info(attempt: int, max_attempts: int, error: Exception, wait_time: float):
    """记录重试信息的辅助函数"""
    logger.warning(
        f"🔄 LLM 调用失败 (尝试 {attempt}/{max_attempts}): {type(error).__name__}: {error}. "
        f"等待 {wait_time:.1f}s 后重试..."
    )
