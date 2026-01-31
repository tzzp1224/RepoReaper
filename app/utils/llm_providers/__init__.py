# 文件路径: app/utils/llm_providers/__init__.py
"""
多 LLM 供应商支持模块

支持的供应商:
- OpenAI (GPT-4, GPT-4o, GPT-3.5-turbo 等)
- DeepSeek (deepseek-chat, deepseek-coder 等)
- Anthropic (Claude 3.5, Claude 3 等)
- Google Gemini (gemini-pro, gemini-1.5-pro 等)
"""

from .base import BaseLLMProvider, LLMResponse, LLMConfig
from .openai_provider import OpenAIProvider
from .deepseek_provider import DeepSeekProvider
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider
from .factory import LLMFactory, get_llm_client

__all__ = [
    "BaseLLMProvider",
    "LLMResponse",
    "LLMConfig",
    "OpenAIProvider",
    "DeepSeekProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "LLMFactory",
    "get_llm_client",
]
