# 文件路径: app/core/config.py
"""
应用配置模块 - 统一配置中心

支持多 LLM 供应商配置:
- OpenAI (GPT-4, GPT-4o 等)
- DeepSeek (deepseek-chat 等)
- Anthropic (Claude 系列)
- Google Gemini (gemini-3-flash-preview 等)
"""
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


# ============================================================
# Agent 分析配置
# ============================================================

@dataclass
class AgentAnalysisConfig:
    """Agent 分析引擎配置"""
    # Repo Map 配置
    initial_map_limit: int = 25           # 初始 Repo Map 文件数量 (提高精度)
    max_symbols_per_file: int = 40        # 每文件最大符号数 (提高精度)
    
    # 分析轮次配置
    max_rounds: int = 4                   # 最大分析轮数 (提高精度，因为报告可复用)
    files_per_round: int = 5              # 每轮选择文件数 (提高精度)
    max_context_length: int = 20000       # 上下文最大长度 (提高精度)
    
    # 优先级配置
    priority_exts: Tuple[str, ...] = (
        '.py', '.java', '.go', '.js', '.ts', '.tsx', '.cpp', '.cs', '.rs'
    )
    priority_keywords: Tuple[str, ...] = (
        'main', 'app', 'core', 'api', 'service', 'utils', 'controller', 'model', 'config'
    )


# ============================================================
# 向量服务配置
# ============================================================

@dataclass
class VectorServiceConfig:
    """向量服务配置"""
    # 数据目录
    data_dir: str = "data"
    context_dir: str = "data/contexts"
    cache_version: str = "2.0"
    
    # Embedding 配置
    embedding_api_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = 50
    embedding_max_length: int = 8000
    embedding_concurrency: int = 5
    embedding_dimensions: int = 1024
    
    # BM25 配置
    tokenize_regex: str = r'[^a-zA-Z0-9_\.@\u4e00-\u9fa5]+'
    
    # 混合搜索 RRF 参数
    rrf_k: int = 60
    rrf_weight_vector: float = 1.0
    rrf_weight_bm25: float = 0.3
    search_oversample: int = 2
    default_top_k: int = 3
    
    # Session LRU 缓存配置
    session_max_count: int = 100          # 内存中最大 session 数


# ============================================================
# 对话记忆配置
# ============================================================

@dataclass
class ConversationConfig:
    """对话记忆配置"""
    # 滑动窗口
    max_recent_turns: int = 10             # 保留最近 N 轮对话
    max_context_tokens: int = 8000        # 最大上下文 token 数
    summary_threshold: int = 15           # 超过 N 轮开始压缩
    # 对话记忆是纯内存存储，服务重启自动清空，无需定时清理


# ============================================================
# Qdrant 配置
# ============================================================

@dataclass
class QdrantServiceConfig:
    """
    Qdrant 向量数据库配置
    
    支持三种模式 (通过环境变量 QDRANT_MODE 切换):
    - local: 本地嵌入式存储 (开发环境, 单 Worker)
    - server: Qdrant Server Docker (生产环境, 多 Worker)
    - cloud: Qdrant Cloud 托管服务
    
    环境变量:
    - QDRANT_MODE: "local" | "server" | "cloud"
    - QDRANT_URL: 服务器 URL (server/cloud 模式)
    - QDRANT_API_KEY: API 密钥 (cloud 模式必需)
    - QDRANT_LOCAL_PATH: 本地存储路径 (local 模式)
    """
    mode: str = os.getenv("QDRANT_MODE", "local")
    url: str = os.getenv("QDRANT_URL", "")
    host: str = os.getenv("QDRANT_HOST", "localhost")
    port: int = int(os.getenv("QDRANT_PORT", "6333"))
    grpc_port: int = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
    prefer_grpc: bool = True
    api_key: str = os.getenv("QDRANT_API_KEY", "")
    
    local_path: str = os.getenv("QDRANT_LOCAL_PATH", "data/qdrant_db")
    
    vector_size: int = 1024               # BGE-M3 维度
    hnsw_m: int = 16
    hnsw_ef_construct: int = 100
    batch_size: int = 100
    timeout: float = 30.0


# ============================================================
# LLM 供应商配置
# ============================================================


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


# ============================================================
# 全局配置实例
# ============================================================

# LLM 设置
settings = Settings()
settings.validate()

# 子系统配置
agent_config = AgentAnalysisConfig()
vector_config = VectorServiceConfig()
conversation_config = ConversationConfig()
qdrant_config = QdrantServiceConfig()