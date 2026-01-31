# 文件路径: app/utils/llm_providers/gemini_provider.py
"""
Google Gemini LLM 提供商实现

支持模型: gemini-1.5-pro, gemini-1.5-flash, gemini-pro 等
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


class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini API 提供商
    
    支持两种方式:
    1. 使用 google-generativeai SDK (原生)
    2. 使用 OpenAI 兼容接口 (通过 AI Studio 或 Vertex AI)
    """
    
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._available = False
        self._use_openai_compat = config.base_url is not None
        
        if self._use_openai_compat:
            # 使用 OpenAI 兼容模式 (推荐)
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=config.api_key,
                    base_url=config.base_url,
                    timeout=config.timeout
                )
                self._available = True
                print(f"✅ Gemini Provider (OpenAI Compatible) initialized")
            except ImportError:
                print("⚠️ openai 包未安装")
        else:
            # 使用 Google AI SDK (原生模式)
            try:
                import google.generativeai as genai
                genai.configure(api_key=config.api_key)
                self._genai = genai
                self._model = genai.GenerativeModel(config.model_name)
                self._available = True
                print(f"✅ Gemini Provider (Native SDK) initialized")
            except ImportError:
                print("⚠️ google-generativeai 包未安装，请运行: pip install google-generativeai")
                self._genai = None
                self._model = None
    
    def _convert_messages_to_gemini(self, messages: List[LLMMessage]) -> tuple:
        """
        转换消息格式为 Gemini 格式
        
        Gemini 的消息格式:
        - 不支持 system 角色，需要将其合并到第一条 user 消息
        - role: "user" | "model" (不是 "assistant")
        
        Returns:
            (history, current_message)
        """
        system_content = ""
        converted = []
        
        for msg in messages:
            if msg.role == "system":
                system_content = msg.content + "\n\n"
            elif msg.role == "assistant":
                converted.append({"role": "model", "parts": [msg.content]})
            else:  # user
                content = msg.content
                if system_content and len(converted) == 0:
                    content = system_content + content
                    system_content = ""
                converted.append({"role": "user", "parts": [content]})
        
        if not converted:
            return [], ""
        
        # 最后一条作为当前消息
        if len(converted) == 1:
            return [], converted[0]["parts"][0]
        else:
            return converted[:-1], converted[-1]["parts"][0]
    
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
            raise RuntimeError("Gemini client not available")
        
        if self._use_openai_compat:
            # OpenAI 兼容模式
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
        else:
            # Native SDK 模式
            history, current_msg = self._convert_messages_to_gemini(messages)
            
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            
            chat = self._model.start_chat(history=history)
            response = await chat.send_message_async(
                current_msg,
                generation_config=generation_config
            )
            
            content = response.text if response.text else ""
            
            choices = [
                LLMChoice(
                    index=0,
                    message=LLMMessage(role="assistant", content=content),
                    finish_reason="stop"
                )
            ]
            
            # Gemini 原生 SDK 的 token 统计
            usage = None
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = LLMUsage(
                    prompt_tokens=getattr(response.usage_metadata, 'prompt_token_count', 0),
                    completion_tokens=getattr(response.usage_metadata, 'candidates_token_count', 0),
                    total_tokens=getattr(response.usage_metadata, 'total_token_count', 0)
                )
            
            return LLMResponse(
                id=f"gemini-{uuid.uuid4().hex[:12]}",
                model=model,
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
            raise RuntimeError("Gemini client not available")
        
        if self._use_openai_compat:
            # OpenAI 兼容模式
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
        else:
            # Native SDK 流式
            history, current_msg = self._convert_messages_to_gemini(messages)
            
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            
            chat = self._model.start_chat(history=history)
            response = await chat.send_message_async(
                current_msg,
                generation_config=generation_config,
                stream=True
            )
            
            response_id = f"gemini-{uuid.uuid4().hex[:12]}"
            
            async for chunk in response:
                if chunk.text:
                    choices = [
                        LLMChoice(
                            index=0,
                            delta=LLMMessage(role="assistant", content=chunk.text),
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


def create_gemini_provider(
    api_key: str,
    model_name: str = "gemini-1.5-flash",
    base_url: str = None,
    **kwargs
) -> GeminiProvider:
    """
    工厂函数：创建 Gemini 提供商
    
    Args:
        api_key: Google AI API Key
        model_name: 模型名称
        base_url: OpenAI 兼容端点 (可选)
                  如果不提供，则使用原生 SDK
    """
    config = LLMConfig(
        provider=LLMProviderType.GEMINI,
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        **kwargs
    )
    return GeminiProvider(config)
