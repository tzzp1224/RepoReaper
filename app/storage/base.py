# -*- coding: utf-8 -*-
"""
向量存储抽象层

设计原则:
1. 接口与实现分离 - 易于切换存储后端
2. 异步优先 - 所有 I/O 操作都是异步的
3. 类型安全 - 完整的类型注解
4. 可观测 - 内置指标收集
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class Document:
    """文档数据模型"""
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    
    @property
    def file_path(self) -> str:
        return self.metadata.get("file", "")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "metadata": self.metadata,
        }


@dataclass
class SearchResult:
    """搜索结果"""
    document: Document
    score: float
    source: str = "vector"  # "vector" | "bm25" | "hybrid"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.document.id,
            "content": self.document.content,
            "file": self.document.file_path,
            "metadata": self.document.metadata,
            "score": self.score,
            "source": self.source,
        }


@dataclass
class CollectionStats:
    """集合统计信息"""
    name: str
    document_count: int
    indexed_files: Set[str] = field(default_factory=set)
    vector_dimension: int = 0


class StorageBackend(Enum):
    """存储后端类型"""
    QDRANT = "qdrant"
    CHROMA = "chroma"  # 保留兼容性


# ============================================================
# 抽象基类
# ============================================================

class BaseVectorStore(ABC):
    """
    向量存储抽象基类
    
    所有存储后端必须实现这些方法
    """
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化存储连接"""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """关闭连接"""
        pass
    
    @abstractmethod
    async def add_documents(
        self,
        documents: List[Document],
        embeddings: List[List[float]]
    ) -> int:
        """
        添加文档
        
        Args:
            documents: 文档列表
            embeddings: 对应的嵌入向量
            
        Returns:
            成功添加的文档数量
        """
        pass
    
    @abstractmethod
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_conditions: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        向量相似度搜索
        
        Args:
            query_embedding: 查询向量
            top_k: 返回数量
            filter_conditions: 过滤条件
            
        Returns:
            搜索结果列表
        """
        pass
    
    @abstractmethod
    async def delete_collection(self) -> bool:
        """删除当前集合"""
        pass
    
    @abstractmethod
    async def get_stats(self) -> CollectionStats:
        """获取集合统计信息"""
        pass
    
    @abstractmethod
    async def get_documents_by_file(self, file_path: str) -> List[Document]:
        """根据文件路径获取文档"""
        pass


class BaseVectorStoreFactory(ABC):
    """向量存储工厂基类"""
    
    @abstractmethod
    def create(self, collection_name: str) -> BaseVectorStore:
        """创建存储实例"""
        pass
