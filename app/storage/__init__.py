# -*- coding: utf-8 -*-
"""
存储层模块

提供向量存储的抽象和实现
"""

from app.storage.base import (
    Document,
    SearchResult,
    CollectionStats,
    StorageBackend,
    BaseVectorStore,
)
from app.storage.qdrant_store import (
    QdrantConfig,
    QdrantVectorStore,
    QdrantStoreFactory,
    get_qdrant_factory,
)

__all__ = [
    # 基础类型
    "Document",
    "SearchResult",
    "CollectionStats",
    "StorageBackend",
    "BaseVectorStore",
    # Qdrant
    "QdrantConfig",
    "QdrantVectorStore",
    "QdrantStoreFactory",
    "get_qdrant_factory",
]
