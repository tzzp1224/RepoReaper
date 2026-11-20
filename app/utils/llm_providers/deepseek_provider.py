# 文件路径: app/utils/llm_providers/deepseek_provider.py
"""
DeepSeek LLM 提供商实现

DeepSeek API 兼容 OpenAI 协议，因此直接复用 OpenAI SDK。
"""

from typing import List, AsyncIterator
from urllib.parse import urlparse
from openai import AsyncOpenAI
from app.core.config import DEEPSEEK_DEFAULT_MODEL

from .base import (
    BaseLLMProvider, 
    LLMConfig, 
    LLMMessage, 
    LLMResponse, 
    LLMChoice, 
    LLMUsage,
    LLMProviderType
)


# DeepSeek 默认 API 端点
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(BaseLLMProvider):
    """
    DeepSeek API 提供商
    
    DeepSeek 使用 OpenAI 兼容协议，因此可以直接使用 OpenAI SDK。
    """
    
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        # 确保使用正确的 base_url
        base_url = config.base_url or DEEPSEEK_DEFAULT_BASE_URL
        self._is_official_endpoint = urlparse(base_url).hostname == "api.deepseek.com"
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=base_url,
            timeout=config.timeout
        )

    def _request_options(self, model: str, options: dict) -> dict:
        if model == DEEPSEEK_DEFAULT_MODEL and self._is_official_endpoint:
            options["extra_body"] = {"thinking": {"type": "disabled"}, **(options.get("extra_body") or {})}
        return options
    
    async def chat_completions_create(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        **kwargs
    ) -> LLMResponse:
        """非流式请求"""
        api_messages = [
            {"role": m.role, "content": m.content} 
            for m in messages
        ]
        
        response = await self._client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **self._request_options(model, kwargs)
        )
        
        choices = [
            LLMChoice(
                index=c.index,
                message=LLMMessage(role=c.message.role, content=c.message.content),
                finish_reason=c.finish_reason
            )
            for c in response.choices
        ]
        
        usage = None
        if response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens
            )
        
        return LLMResponse(
            id=response.id,
            model=response.model,
            choices=choices,
            usage=usage,
            created=response.created
        )
    
    async def chat_completions_create_stream(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        **kwargs
    ) -> AsyncIterator[LLMResponse]:
        """流式请求"""
        api_messages = [
            {"role": m.role, "content": m.content} 
            for m in messages
        ]
        
        stream = await self._client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            stream=True,
            **self._request_options(model, kwargs)
        )
        
        try:
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                choices = [LLMChoice(index=choice.index,
                                     delta=LLMMessage(role="assistant", content=choice.delta.content or ""),
                                     finish_reason=choice.finish_reason) for choice in chunk.choices]
                if choices:
                    yield LLMResponse(
                        id=chunk.id, model=chunk.model, choices=choices, created=chunk.created,
                        usage=LLMUsage(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
                        if usage else None,
                    )
        finally:
            close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if close is not None:
                await close()
    
    def validate_connection(self) -> bool:
        """验证 API Key 有效性"""
        return bool(self.config.api_key)


def create_deepseek_provider(
    api_key: str,
    model_name: str = DEEPSEEK_DEFAULT_MODEL,
    base_url: str = None,
    **kwargs
) -> DeepSeekProvider:
    """工厂函数：创建 DeepSeek 提供商"""
    config = LLMConfig(
        provider=LLMProviderType.DEEPSEEK,
        api_key=api_key,
        model_name=model_name,
        base_url=base_url or DEEPSEEK_DEFAULT_BASE_URL,
        **kwargs
    )
    return DeepSeekProvider(config)
