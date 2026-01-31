# 文件路径: app/core/config.py
"""
应用配置模块

支持多 LLM 供应商配置:
- OpenAI (GPT-4, GPT-4o 等)
- DeepSeek (deepseek-chat 等)
- Anthropic (Claude 系列)
- Google Gemini (gemini-1.5-pro 等)
"""
import os
from dotenv import load_dotenv
from typing import Optional

# 加载 .env 文件
load_dotenv()


class Settings:
    """应用配置类"""
    
    # --- LLM 供应商选择 ---
    # 支持: "openai", "deepseek", "anthropic", "gemini"
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
    
    # --- API Keys (根据选择的供应商配置对应的 Key) ---
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    
    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # 可选自定义端点
    
    # DeepSeek
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    
    # Anthropic (Claude)
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    
    # Google Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL")  # 可选 OpenAI 兼容端点
    
    # SiliconFlow (Embedding)
    SILICON_API_KEY = os.getenv("SILICON_API_KEY")
    
    # --- 模型配置 ---
    # 如果不指定，将使用各供应商的默认模型
    MODEL_NAME = os.getenv("MODEL_NAME")
    
    # --- 服务配置 ---
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", 8000))
    
    # --- LLM 默认参数 ---
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "600"))
    
    @property
    def current_api_key(self) -> Optional[str]:
        """获取当前选择的供应商的 API Key"""
        key_mapping = {
            "openai": self.OPENAI_API_KEY,
            "deepseek": self.DEEPSEEK_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "gemini": self.GEMINI_API_KEY,
        }
        return key_mapping.get(self.LLM_PROVIDER.lower())
    
    @property
    def current_base_url(self) -> Optional[str]:
        """获取当前选择的供应商的 Base URL"""
        url_mapping = {
            "openai": self.OPENAI_BASE_URL,
            "deepseek": self.DEEPSEEK_BASE_URL,
            "anthropic": None,
            "gemini": self.GEMINI_BASE_URL,
        }
        return url_mapping.get(self.LLM_PROVIDER.lower())
    
    @property
    def default_model_name(self) -> str:
        """获取当前供应商的默认模型名称"""
        defaults = {
            "openai": "gpt-4o-mini",
            "deepseek": "deepseek-chat",
            "anthropic": "claude-3-5-sonnet-20241022",
            "gemini": "gemini-3-flash-preview",
        }
        return self.MODEL_NAME or defaults.get(self.LLM_PROVIDER.lower(), "default")

    def validate(self):
        """启动时检查必要的配置是否存在"""
        provider = self.LLM_PROVIDER.lower()
        print(f"🔧 LLM Provider: {provider.upper()}")
        
        # 1. 检查选择的供应商的 API Key
        if not self.current_api_key:
            key_name = f"{provider.upper()}_API_KEY"
            raise ValueError(
                f"❌ 错误: 缺少 {key_name}。\n"
                f"   当前选择的 LLM 供应商是: {provider}\n"
                f"   请在 .env 文件中设置 {key_name}，或更改 LLM_PROVIDER 为其他供应商。"
            )
        
        # 2. 检查 SiliconCloud Key (Embedding 功能)
        if not self.SILICON_API_KEY:
            print("⚠️ 警告: 未找到 SILICON_API_KEY，向量检索功能可能无法工作。")
            
        # 3. 检查 GitHub Token (可选但建议)
        if not self.GITHUB_TOKEN:
            print("⚠️ 警告: 未找到 GITHUB_TOKEN，GitHub API 请求将受到每小时 60 次的严格限制。")
        
        print(f"✅ 配置验证通过 (Model: {self.default_model_name})")


settings = Settings()
# 立即执行验证，确保启动时就暴露问题
settings.validate()