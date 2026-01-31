# 文件路径: app/utils/llm_providers/openai_provider.py
"""
OpenAI LLM 提供商实现

支持模型: GPT-4, GPT-4o, GPT-4o-mini, GPT-3.5-turbo 等
"""

from typing import List, AsyncIterator
from openai import AsyncOpenAI

from .base import (
    BaseLLMProvider, 
    LLMConfig, 
    LLMMessage, 
    LLMResponse, 
    LLMChoice, 
    LLMUsage,
    LLMProviderType
)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API 提供商"""
    
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,  # 可选自定义 base_url
            timeout=config.timeout
        )
    
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
        # 转换消息格式
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
            **kwargs
        )
        
        # 转换为统一格式
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
            **kwargs
        )
        
        async for chunk in stream:
            if chunk.choices:
                delta_content = chunk.choices[0].delta.content or ""
                choices = [
                    LLMChoice(
                        index=0,
                        delta=LLMMessage(role="assistant", content=delta_content),
                        finish_reason=chunk.choices[0].finish_reason
                    )
                ]
                yield LLMResponse(
                    id=chunk.id,
                    model=chunk.model,
                    choices=choices,
                    created=chunk.created
                )
    
    def validate_connection(self) -> bool:
        """验证 API Key 有效性"""
        return bool(self.config.api_key)


def create_openai_provider(
    api_key: str,
    model_name: str = "gpt-4o-mini",
    base_url: str = None,
    **kwargs
) -> OpenAIProvider:
    """工厂函数：创建 OpenAI 提供商"""
    config = LLMConfig(
        provider=LLMProviderType.OPENAI,
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        **kwargs
    )
    return OpenAIProvider(config)
