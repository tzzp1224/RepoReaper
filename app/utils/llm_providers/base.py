# 文件路径: app/utils/llm_providers/base.py
"""
LLM 提供商基类定义

定义统一的接口规范，所有供应商实现都必须遵循此规范。
采用适配器模式，将不同供应商的 API 统一为 OpenAI 兼容格式。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from enum import Enum

from app.utils.retry import llm_retry, is_retryable_error

# 配置日志
logger = logging.getLogger("llm_provider")


class LLMProviderType(str, Enum):
    """支持的 LLM 供应商类型"""
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


@dataclass
class LLMConfig:
    """LLM 配置"""
    provider: LLMProviderType
    api_key: str
    model_name: str
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 600
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class LLMMessage:
    """消息格式 (兼容 OpenAI)"""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMUsage:
    """Token 使用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMChoice:
    """响应选项 (兼容 OpenAI)"""
    index: int
    message: Optional[LLMMessage] = None
    delta: Optional[LLMMessage] = None  # 流式响应时使用
    finish_reason: Optional[str] = None


@dataclass
class LLMResponse:
    """
    统一的 LLM 响应格式
    
    设计为兼容 OpenAI 的 ChatCompletion 格式，
    使得现有代码无需大幅修改即可使用。
    """
    id: str
    model: str
    choices: List[LLMChoice]
    usage: Optional[LLMUsage] = None
    created: int = 0
    
    @property
    def content(self) -> str:
        """便捷方法：获取第一个选项的内容"""
        if self.choices and self.choices[0].message:
            return self.choices[0].message.content
        return ""


# 辅助类定义（在 BaseLLMProvider 外部，避免嵌套类问题）
class _CompletionsNamespace:
    """模拟 client.chat.completions 命名空间"""
    def __init__(self, provider: 'BaseLLMProvider'):
        self._provider = provider
    
    async def create(
        self,
        model: str = None,
        messages: List[Dict[str, str]] = None,
        temperature: float = None,
        max_tokens: int = None,
        stream: bool = False,
        timeout: int = None,
        **kwargs
    ) -> Union[LLMResponse, AsyncIterator[LLMResponse]]:
        """
        统一的 completions.create 接口
        
        兼容 OpenAI SDK 调用方式:
        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True
        )
        
        内置重试机制:
        - 自动重试网络错误、超时、速率限制
        - 指数退避策略
        - 最多重试 3 次
        """
        # 合并配置
        _model = model or self._provider.config.model_name
        _temperature = temperature if temperature is not None else self._provider.config.temperature
        _max_tokens = max_tokens or self._provider.config.max_tokens
        _timeout = timeout or self._provider.config.timeout
        
        # 转换消息格式
        _messages = [
            LLMMessage(role=m["role"], content=m["content"]) 
            for m in (messages or [])
        ]
        
        if stream:
            # 流式请求: 返回带重试的异步生成器
            return self._create_stream_with_retry(
                messages=_messages,
                model=_model,
                temperature=_temperature,
                max_tokens=_max_tokens,
                timeout=_timeout,
                **kwargs
            )
        else:
            # 非流式请求: 使用 tenacity 重试
            return await self._create_with_retry(
                messages=_messages,
                model=_model,
                temperature=_temperature,
                max_tokens=_max_tokens,
                timeout=_timeout,
                **kwargs
            )
    
    @llm_retry
    async def _create_with_retry(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        **kwargs
    ) -> LLMResponse:
        """带重试的非流式请求"""
        logger.debug(f"🔄 LLM 请求: model={model}, messages_count={len(messages)}")
        return await self._provider.chat_completions_create(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs
        )
    
    async def _create_stream_with_retry(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        max_retries: int = 3,
        **kwargs
    ) -> AsyncIterator[LLMResponse]:
        """
        带重试的流式请求
        
        注意: 流式请求的重试策略与非流式不同
        - 如果在获取流之前失败，可以重试
        - 如果在流传输过程中失败，需要重新开始
        """
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"🔄 LLM 流式请求 (尝试 {attempt}/{max_retries}): model={model}")
                
                # 获取流生成器
                stream = self._provider.chat_completions_create_stream(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    **kwargs
                )
                
                # 迭代流并 yield
                async for chunk in stream:
                    yield chunk
                
                # 成功完成，退出重试循环
                return
                
            except Exception as e:
                last_error = e
                if is_retryable_error(e) and attempt < max_retries:
                    wait_time = min(2 ** attempt, 30)  # 指数退避
                    logger.warning(
                        f"🔄 LLM 流式请求失败 (尝试 {attempt}/{max_retries}): "
                        f"{type(e).__name__}: {e}. 等待 {wait_time}s 后重试..."
                    )
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    # 不可重试的错误或已达到最大重试次数
                    logger.error(f"❌ LLM 流式请求最终失败: {type(e).__name__}: {e}")
                    raise
        
        # 如果走到这里，说明所有重试都失败了
        if last_error:
            raise last_error


class _ChatNamespace:
    """模拟 client.chat 命名空间"""
    def __init__(self, provider: 'BaseLLMProvider'):
        self._provider = provider
        self.completions = _CompletionsNamespace(provider)


class BaseLLMProvider(ABC):
    """
    LLM 提供商抽象基类
    
    所有供应商实现都需要继承此类并实现以下方法:
    - chat_completions_create: 非流式请求
    - chat_completions_create_stream: 流式请求
    
    为了兼容现有代码，提供一个模拟 OpenAI 客户端的 chat.completions 接口。
    """
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        # 模拟 OpenAI SDK 的接口结构
        self.chat = _ChatNamespace(self)
    
    @abstractmethod
    async def chat_completions_create(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        **kwargs
    ) -> LLMResponse:
        """
        非流式 Chat Completion 请求
        
        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 Token 数
            timeout: 超时时间
            
        Returns:
            LLMResponse: 统一格式的响应
        """
        pass
    
    @abstractmethod
    async def chat_completions_create_stream(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        **kwargs
    ) -> AsyncIterator[LLMResponse]:
        """
        流式 Chat Completion 请求
        
        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 Token 数
            timeout: 超时时间
            
        Yields:
            LLMResponse: 流式响应块
        """
        pass
    
    @abstractmethod
    def validate_connection(self) -> bool:
        """验证连接是否正常"""
        pass
    
    @property
    def provider_name(self) -> str:
        """获取供应商名称"""
        return self.config.provider.value
    
    @property
    def model_name(self) -> str:
        """获取当前模型名称"""
        return self.config.model_name
