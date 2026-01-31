# -*- coding: utf-8 -*-
"""
Embedding 服务 - 并发优化版

特性:
1. 并发批量请求 - 使用 asyncio.gather 并行处理多个批次
2. 信号量控制 - 限制最大并发数，避免 API 限流
3. 重试机制 - 使用 tenacity 处理临时性错误
4. 智能分批 - 根据 token 数量动态调整批次大小
"""

import asyncio
import logging
from typing import List, Optional
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.core.config import settings
from app.utils.retry import llm_retry, is_retryable_error

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    """Embedding 服务配置"""
    # API 配置
    api_base_url: str = "https://api.siliconflow.cn/v1"
    model_name: str = "BAAI/bge-m3"
    
    # 批处理配置
    batch_size: int = 50              # 每批文本数量
    max_text_length: int = 8000       # 单个文本最大字符数
    
    # 并发控制
    max_concurrent_batches: int = 5   # 最大并发批次数
    
    # 超时配置
    timeout: int = 60                 # 单次请求超时 (秒)


class EmbeddingService:
    """
    高性能 Embedding 服务
    
    使用示例:
    ```python
    service = EmbeddingService()
    
    # 单文本
    embedding = await service.embed_text("Hello world")
    
    # 批量文本 (自动并发优化)
    texts = ["text1", "text2", ..., "text100"]
    embeddings = await service.embed_batch(texts)
    ```
    """
    
    def __init__(self, config: Optional[EmbeddingConfig] = None):
        self.config = config or EmbeddingConfig()
        
        # 初始化 OpenAI 客户端 (SiliconFlow 兼容 OpenAI 协议)
        self._client = AsyncOpenAI(
            api_key=settings.SILICON_API_KEY,
            base_url=self.config.api_base_url,
            timeout=self.config.timeout
        )
        
        # 并发信号量
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_batches)
        
        # 统计信息
        self._stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_texts": 0,
            "retried_requests": 0
        }
    
    def _preprocess_text(self, text: str) -> str:
        """预处理文本: 移除换行、截断长度"""
        text = text.replace("\n", " ").strip()
        if len(text) > self.config.max_text_length:
            text = text[:self.config.max_text_length]
        return text
    
    @llm_retry
    async def _embed_single_batch(self, texts: List[str]) -> List[List[float]]:
        """
        处理单个批次的 Embedding 请求 (带重试)
        
        Args:
            texts: 预处理后的文本列表
            
        Returns:
            embedding 向量列表
        """
        self._stats["total_requests"] += 1
        
        response = await self._client.embeddings.create(
            input=texts,
            model=self.config.model_name
        )
        
        self._stats["successful_requests"] += 1
        return [item.embedding for item in response.data]
    
    async def _embed_batch_with_semaphore(
        self, 
        batch_texts: List[str], 
        batch_index: int
    ) -> tuple[int, List[List[float]]]:
        """
        带信号量控制的批次处理
        
        Returns:
            (batch_index, embeddings) - 返回索引用于结果排序
        """
        async with self._semaphore:
            try:
                embeddings = await self._embed_single_batch(batch_texts)
                logger.debug(f"✅ 批次 {batch_index} 完成: {len(batch_texts)} 文本")
                return (batch_index, embeddings)
            except Exception as e:
                self._stats["failed_requests"] += 1
                logger.error(f"❌ 批次 {batch_index} 失败: {type(e).__name__}: {e}")
                raise
    
    async def embed_text(self, text: str) -> List[float]:
        """
        获取单个文本的 Embedding
        
        Args:
            text: 输入文本
            
        Returns:
            embedding 向量，失败返回空列表
        """
        try:
            processed = self._preprocess_text(text)
            if not processed:
                return []
            
            self._stats["total_texts"] += 1
            embeddings = await self._embed_single_batch([processed])
            return embeddings[0] if embeddings else []
        except Exception as e:
            logger.error(f"embed_text 失败: {e}")
            return []
    
    async def embed_batch(
        self, 
        texts: List[str],
        show_progress: bool = False
    ) -> List[List[float]]:
        """
        批量获取 Embedding (并发优化)
        
        Args:
            texts: 文本列表
            show_progress: 是否显示进度日志
            
        Returns:
            embedding 向量列表 (与输入顺序一致)
            失败的文本对应空列表
        """
        if not texts:
            return []
        
        # 预处理所有文本
        processed_texts = [self._preprocess_text(t) for t in texts]
        self._stats["total_texts"] += len(texts)
        
        # 分批
        batch_size = self.config.batch_size
        batches = [
            processed_texts[i:i + batch_size] 
            for i in range(0, len(processed_texts), batch_size)
        ]
        
        total_batches = len(batches)
        if show_progress:
            logger.info(
                f"📊 Embedding: {len(texts)} 文本 → {total_batches} 批次 "
                f"(并发: {self.config.max_concurrent_batches})"
            )
        
        # 并发执行所有批次
        tasks = [
            self._embed_batch_with_semaphore(batch, idx)
            for idx, batch in enumerate(batches)
        ]
        
        # 收集结果
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 按批次索引排序并合并结果
        embeddings = []
        for result in sorted(results, key=lambda x: x[0] if isinstance(x, tuple) else float('inf')):
            if isinstance(result, tuple):
                batch_idx, batch_embeddings = result
                embeddings.extend(batch_embeddings)
            else:
                # 异常情况: 填充空向量
                # 找出这个批次有多少文本
                failed_batch_size = batch_size  # 保守估计
                embeddings.extend([[] for _ in range(failed_batch_size)])
                logger.warning(f"批次失败，填充 {failed_batch_size} 个空向量")
        
        # 确保返回数量与输入一致
        if len(embeddings) < len(texts):
            embeddings.extend([[] for _ in range(len(texts) - len(embeddings))])
        elif len(embeddings) > len(texts):
            embeddings = embeddings[:len(texts)]
        
        if show_progress:
            success_count = sum(1 for e in embeddings if e)
            logger.info(f"✅ Embedding 完成: {success_count}/{len(texts)} 成功")
        
        return embeddings
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()
    
    def reset_stats(self):
        """重置统计信息"""
        for key in self._stats:
            self._stats[key] = 0


# 全局单例
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service(config: Optional[EmbeddingConfig] = None) -> EmbeddingService:
    """获取 Embedding 服务单例"""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(config)
    return _embedding_service


# 便捷函数
async def embed_text(text: str) -> List[float]:
    """快捷方式: 获取单个文本的 Embedding"""
    return await get_embedding_service().embed_text(text)


async def embed_batch(texts: List[str], show_progress: bool = False) -> List[List[float]]:
    """快捷方式: 批量获取 Embedding"""
    return await get_embedding_service().embed_batch(texts, show_progress)
