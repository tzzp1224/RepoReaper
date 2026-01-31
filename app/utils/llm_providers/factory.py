# 文件路径: app/utils/llm_providers/factory.py
"""
LLM 工厂模块

提供统一的 LLM 客户端创建接口，根据配置自动选择合适的供应商。
"""

import os
from typing import Optional

from .base import BaseLLMProvider, LLMConfig, LLMProviderType
from .openai_provider import OpenAIProvider
from .deepseek_provider import DeepSeekProvider, DEEPSEEK_DEFAULT_BASE_URL
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider


class LLMFactory:
    """
    LLM 客户端工厂
    
    根据提供商类型创建对应的客户端实例。
    支持从环境变量自动配置。
    """
    
    # 提供商类到枚举的映射
    _providers = {
        LLMProviderType.OPENAI: OpenAIProvider,
        LLMProviderType.DEEPSEEK: DeepSeekProvider,
        LLMProviderType.ANTHROPIC: AnthropicProvider,
        LLMProviderType.GEMINI: GeminiProvider,
    }
    
    # 默认模型名称映射
    _default_models = {
        LLMProviderType.OPENAI: "gpt-4o-mini",
        LLMProviderType.DEEPSEEK: "deepseek-chat",
        LLMProviderType.ANTHROPIC: "claude-3-5-sonnet-20241022",
        LLMProviderType.GEMINI: "gemini-1.5-flash",
    }
    
    # 默认 Base URL 映射
    _default_base_urls = {
        LLMProviderType.OPENAI: None,  # 使用 SDK 默认
        LLMProviderType.DEEPSEEK: DEEPSEEK_DEFAULT_BASE_URL,
        LLMProviderType.ANTHROPIC: None,
        LLMProviderType.GEMINI: None,
    }
    
    @classmethod
    def create(
        cls,
        provider: str,
        api_key: str,
        model_name: str = None,
        base_url: str = None,
        **kwargs
    ) -> Optional[BaseLLMProvider]:
        """
        创建 LLM 客户端
        
        Args:
            provider: 提供商名称 ("openai", "deepseek", "anthropic", "gemini")
            api_key: API Key
            model_name: 模型名称 (可选，使用默认值)
            base_url: 自定义 API 端点 (可选)
            **kwargs: 其他配置参数
            
        Returns:
            BaseLLMProvider 实例，或 None (如果创建失败)
        """
        try:
            # 解析提供商类型
            provider_type = LLMProviderType(provider.lower())
        except ValueError:
            print(f"❌ 不支持的 LLM 提供商: {provider}")
            print(f"   支持的提供商: {', '.join([p.value for p in LLMProviderType])}")
            return None
        
        if not api_key:
            print(f"❌ 未提供 {provider} 的 API Key")
            return None
        
        # 获取提供商类
        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            print(f"❌ 提供商 {provider} 未实现")
            return None
        
        # 构建配置
        config = LLMConfig(
            provider=provider_type,
            api_key=api_key,
            model_name=model_name or cls._default_models.get(provider_type, "default"),
            base_url=base_url or cls._default_base_urls.get(provider_type),
            **kwargs
        )
        
        try:
            client = provider_class(config)
            if client.validate_connection():
                print(f"✅ {provider.upper()} Client 初始化成功 (Model: {config.model_name})")
                return client
            else:
                print(f"❌ {provider.upper()} Client 验证失败")
                return None
        except Exception as e:
            print(f"❌ {provider.upper()} Client 初始化失败: {e}")
            return None
    
    @classmethod
    def create_from_env(cls, provider: str = None) -> Optional[BaseLLMProvider]:
        """
        从环境变量创建 LLM 客户端
        
        环境变量命名规范:
        - LLM_PROVIDER: 提供商名称 (可被参数覆盖)
        - {PROVIDER}_API_KEY: API Key (如 OPENAI_API_KEY, DEEPSEEK_API_KEY)
        - {PROVIDER}_BASE_URL: 自定义端点 (可选)
        - MODEL_NAME: 模型名称 (可选)
        
        Args:
            provider: 提供商名称 (可选，默认从 LLM_PROVIDER 环境变量读取)
            
        Returns:
            BaseLLMProvider 实例
        """
        # 确定提供商
        _provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
        _provider = _provider.lower()
        
        # 获取 API Key (支持多种命名方式)
        key_env_names = [
            f"{_provider.upper()}_API_KEY",
            f"{_provider.upper()}API_KEY",
        ]
        
        api_key = None
        for key_name in key_env_names:
            api_key = os.getenv(key_name)
            if api_key:
                break
        
        if not api_key:
            print(f"❌ 未找到 {_provider.upper()} API Key")
            print(f"   请设置环境变量: {key_env_names[0]}")
            return None
        
        # 获取可选配置
        base_url = os.getenv(f"{_provider.upper()}_BASE_URL")
        model_name = os.getenv("MODEL_NAME")
        
        return cls.create(
            provider=_provider,
            api_key=api_key,
            model_name=model_name,
            base_url=base_url
        )


def get_llm_client(provider: str = None) -> Optional[BaseLLMProvider]:
    """
    便捷函数：获取 LLM 客户端
    
    Args:
        provider: 提供商名称 (可选)
        
    Returns:
        BaseLLMProvider 实例
    """
    return LLMFactory.create_from_env(provider)
