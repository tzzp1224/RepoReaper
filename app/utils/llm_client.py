# 文件路径: app/utils/llm_client.py
"""
统一 LLM 客户端入口

支持多个 LLM 供应商，通过 LLM_PROVIDER 环境变量切换:
- openai: OpenAI (GPT-4, GPT-4o 等)
- deepseek: DeepSeek (deepseek-chat, deepseek-coder 等)
- anthropic: Anthropic (Claude 3.5, Claude 3 等)
- gemini: Google Gemini (gemini-1.5-pro 等)

使用方式 (与原来完全兼容):
    from app.utils.llm_client import client
    
    response = await client.chat.completions.create(
        model=settings.default_model_name,
        messages=[{"role": "user", "content": "Hello"}],
        stream=True
    )
"""

from app.core.config import settings
from app.utils.llm_providers import LLMFactory, BaseLLMProvider
from typing import Optional

# 全局客户端实例
client: Optional[BaseLLMProvider] = None

def _initialize_client() -> Optional[BaseLLMProvider]:
    """
    初始化 LLM 客户端
    
    根据配置的 LLM_PROVIDER 创建对应的客户端实例。
    """
    provider = settings.LLM_PROVIDER.lower()
    api_key = settings.current_api_key
    base_url = settings.current_base_url
    model_name = settings.default_model_name
    
    if not api_key:
        print(f"❌ 未找到 {provider.upper()}_API_KEY")
        return None
    
    try:
        return LLMFactory.create(
            provider=provider,
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
        )
    except Exception as e:
        print(f"❌ LLM Client 初始化失败: {e}")
        return None


def get_client() -> Optional[BaseLLMProvider]:
    """
    获取 LLM 客户端实例
    
    如果客户端尚未初始化，会自动初始化。
    """
    global client
    if client is None:
        client = _initialize_client()
    return client


def reinitialize_client(
    provider: str = None,
    api_key: str = None,
    model_name: str = None,
    base_url: str = None,
) -> Optional[BaseLLMProvider]:
    """
    重新初始化客户端
    
    用于运行时切换 LLM 供应商或模型。
    
    Args:
        provider: 新的供应商 (可选)
        api_key: 新的 API Key (可选)
        model_name: 新的模型名称 (可选)
        base_url: 新的 Base URL (可选)
    """
    global client
    
    _provider = provider or settings.LLM_PROVIDER
    _api_key = api_key or settings.current_api_key
    _model_name = model_name or settings.default_model_name
    _base_url = base_url or settings.current_base_url
    
    try:
        client = LLMFactory.create(
            provider=_provider,
            api_key=_api_key,
            model_name=_model_name,
            base_url=_base_url,
        )
        return client
    except Exception as e:
        print(f"❌ 重新初始化失败: {e}")
        return None


# 自动初始化客户端
client = _initialize_client()