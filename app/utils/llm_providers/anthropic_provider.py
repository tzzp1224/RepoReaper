# 文件路径: app/utils/llm_providers/anthropic_provider.py
"""
Anthropic (Claude) LLM 提供商实现

支持模型: claude-3-5-sonnet, claude-3-opus, claude-3-haiku 等
"""

import uuid
import time
from typing import List, AsyncIterator

from .base import (
    BaseLLMProvider, 
    LLMConfig, 
    LLMMessage, 
    LLMResponse, 
    LLMChoice, 
    LLMUsage,
    LLMProviderType
)


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic (Claude) API 提供商
    
    注意: Anthropic 的消息格式与 OpenAI 略有不同:
    - system 消息需要单独传递
    - messages 只包含 user/assistant 轮次
    """
    
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=config.api_key,
                timeout=config.timeout
            )
            self._available = True
        except ImportError:
            print("⚠️ anthropic 包未安装，请运行: pip install anthropic")
            self._client = None
            self._available = False
    
    def _extract_system_message(self, messages: List[LLMMessage]) -> tuple:
        """
        提取 system 消息
        
        Anthropic 需要将 system 消息单独传递,
        不能放在 messages 列表中。
        
        Returns:
            (system_prompt, filtered_messages)
        """
        system_prompt = None
        filtered_messages = []
        
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                filtered_messages.append(msg)
        
        return system_prompt, filtered_messages
    
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
        if not self._available:
            raise RuntimeError("Anthropic client not available. Please install: pip install anthropic")
        
        system_prompt, filtered_messages = self._extract_system_message(messages)
        
        # 转换消息格式
        api_messages = [
            {"role": m.role, "content": m.content} 
            for m in filtered_messages
        ]
        
        # 构建请求参数
        request_params = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if system_prompt:
            request_params["system"] = system_prompt
        
        response = await self._client.messages.create(**request_params)
        
        # 转换为统一格式
        content = ""
        if response.content:
            # Anthropic 的 content 是一个 list
            for block in response.content:
                if hasattr(block, 'text'):
                    content += block.text
        
        choices = [
            LLMChoice(
                index=0,
                message=LLMMessage(role="assistant", content=content),
                finish_reason=response.stop_reason
            )
        ]
        
        usage = LLMUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens
        )
        
        return LLMResponse(
            id=response.id,
            model=response.model,
            choices=choices,
            usage=usage,
            created=int(time.time())
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
        if not self._available:
            raise RuntimeError("Anthropic client not available. Please install: pip install anthropic")
        
        system_prompt, filtered_messages = self._extract_system_message(messages)
        
        api_messages = [
            {"role": m.role, "content": m.content} 
            for m in filtered_messages
        ]
        
        request_params = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if system_prompt:
            request_params["system"] = system_prompt
        
        response_id = f"msg_{uuid.uuid4().hex[:24]}"
        
        async with self._client.messages.stream(**request_params) as stream:
            async for text in stream.text_stream:
                choices = [
                    LLMChoice(
                        index=0,
                        delta=LLMMessage(role="assistant", content=text),
                        finish_reason=None
                    )
                ]
                yield LLMResponse(
                    id=response_id,
                    model=model,
                    choices=choices,
                    created=int(time.time())
                )
    
    def validate_connection(self) -> bool:
        """验证连接"""
        return self._available and bool(self.config.api_key)


def create_anthropic_provider(
    api_key: str,
    model_name: str = "claude-3-5-sonnet-20241022",
    **kwargs
) -> AnthropicProvider:
    """工厂函数：创建 Anthropic 提供商"""
    config = LLMConfig(
        provider=LLMProviderType.ANTHROPIC,
        api_key=api_key,
        model_name=model_name,
        **kwargs
    )
    return AnthropicProvider(config)
