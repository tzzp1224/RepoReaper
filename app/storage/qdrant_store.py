# -*- coding: utf-8 -*-
"""
Qdrant 向量存储实现

特性:
1. 异步原生 - 使用 qdrant-client AsyncQdrantClient
2. 高性能 - 批量 upsert、HNSW 索引、payload 索引
3. 混合搜索 - 向量 + 稀疏向量 (FastEmbed)
4. 连接池 - gRPC 长连接复用
5. 可观测 - 完整的日志和指标
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Set
from contextlib import asynccontextmanager

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    PayloadSchemaType,
)

from app.storage.base import (
    BaseVectorStore,
    Document,
    SearchResult,
    CollectionStats,
)

logger = logging.getLogger(__name__)


# ============================================================
# 配置
# ============================================================

@dataclass
class QdrantConfig:
    """
    Qdrant 配置
    
    支持三种模式:
    - local: 本地嵌入式 (开发/单进程)
    - server: Qdrant Server (多 Worker 生产环境)
    - cloud: Qdrant Cloud (托管服务)
    
    环境变量:
    - QDRANT_MODE: "local" | "server" | "cloud"
    - QDRANT_URL: 服务器地址 (server/cloud 模式)
    - QDRANT_API_KEY: API 密钥 (cloud 模式必需)
    - QDRANT_LOCAL_PATH: 本地存储路径 (local 模式)
    """
    # 模式: "local" | "server" | "cloud"
    mode: str = "local"
    
    # Server/Cloud 模式配置
    url: Optional[str] = None
    host: str = "localhost"
    port: int = 6333
    grpc_port: int = 6334
    prefer_grpc: bool = True
    api_key: Optional[str] = None
    
    # Local 模式配置
    local_path: str = "data/qdrant_db"
    
    # 向量配置
    vector_size: int = 1024  # BGE-M3 维度
    distance: Distance = Distance.COSINE
    
    # 索引配置
    hnsw_m: int = 16              # HNSW 图的边数
    hnsw_ef_construct: int = 100  # 构建时的搜索深度
    
    # 批量操作
    batch_size: int = 100
    
    # 超时
    timeout: float = 30.0
    
    @classmethod
    def from_env(cls) -> "QdrantConfig":
        """从环境变量加载配置"""
        mode = os.getenv("QDRANT_MODE", "local").lower()
        
        return cls(
            mode=mode,
            url=os.getenv("QDRANT_URL"),
            host=os.getenv("QDRANT_HOST", "localhost"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
            grpc_port=int(os.getenv("QDRANT_GRPC_PORT", "6334")),
            api_key=os.getenv("QDRANT_API_KEY"),
            local_path=os.getenv("QDRANT_LOCAL_PATH", "data/qdrant_db"),
            vector_size=int(os.getenv("QDRANT_VECTOR_SIZE", "1024")),
            prefer_grpc=os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true",
        )
    
    @property
    def is_local(self) -> bool:
        return self.mode == "local"
    
    @property
    def is_server(self) -> bool:
        return self.mode == "server"
    
    @property
    def is_cloud(self) -> bool:
        return self.mode == "cloud"
    
    def validate(self) -> None:
        """验证配置"""
        if self.is_cloud and not self.api_key:
            raise ValueError("QDRANT_API_KEY is required for cloud mode")
        if (self.is_server or self.is_cloud) and not (self.url or self.host):
            raise ValueError("QDRANT_URL or QDRANT_HOST is required for server/cloud mode")


# ============================================================
# 全局共享客户端单例
# ============================================================

_shared_client: Optional[AsyncQdrantClient] = None
_shared_config: Optional[QdrantConfig] = None
_client_lock = asyncio.Lock()


async def get_shared_client(config: Optional[QdrantConfig] = None) -> AsyncQdrantClient:
    """
    获取共享的 Qdrant 客户端单例
    
    支持三种模式:
    - local: 本地嵌入式存储 (单进程，开发环境)
    - server: Qdrant Server (多 Worker，Docker 部署)
    - cloud: Qdrant Cloud (托管服务)
    """
    global _shared_client, _shared_config
    
    async with _client_lock:
        if _shared_client is None:
            _shared_config = config or QdrantConfig.from_env()
            _shared_config.validate()
            
            if _shared_config.is_local:
                # Local 模式: 嵌入式存储
                os.makedirs(_shared_config.local_path, exist_ok=True)
                _shared_client = AsyncQdrantClient(
                    path=_shared_config.local_path,
                    timeout=_shared_config.timeout,
                )
                logger.info(f"📦 Qdrant 本地模式: {_shared_config.local_path}")
                
            elif _shared_config.is_server:
                # Server 模式: 连接 Qdrant Server
                if _shared_config.url:
                    _shared_client = AsyncQdrantClient(
                        url=_shared_config.url,
                        prefer_grpc=_shared_config.prefer_grpc,
                        timeout=_shared_config.timeout,
                    )
                    logger.info(f"🌐 Qdrant Server 模式: {_shared_config.url}")
                else:
                    _shared_client = AsyncQdrantClient(
                        host=_shared_config.host,
                        port=_shared_config.port,
                        grpc_port=_shared_config.grpc_port,
                        prefer_grpc=_shared_config.prefer_grpc,
                        timeout=_shared_config.timeout,
                    )
                    logger.info(f"🌐 Qdrant Server 模式: {_shared_config.host}:{_shared_config.port}")
                    
            else:
                # Cloud 模式: 连接 Qdrant Cloud
                _shared_client = AsyncQdrantClient(
                    url=_shared_config.url,
                    api_key=_shared_config.api_key,
                    timeout=_shared_config.timeout,
                )
                logger.info(f"☁️ Qdrant Cloud 模式: {_shared_config.url}")
        
        return _shared_client
        
        return _shared_client


async def close_shared_client() -> None:
    """关闭共享客户端"""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.close()
        _shared_client = None
        logger.info("🔒 Qdrant 共享客户端已关闭")


# ============================================================
# Qdrant 存储实现
# ============================================================

class QdrantVectorStore(BaseVectorStore):
    """
    Qdrant 向量存储
    
    使用示例:
    ```python
    config = QdrantConfig.from_env()
    store = QdrantVectorStore("my_collection", config)
    
    await store.initialize()
    
    # 添加文档
    docs = [Document(id="1", content="hello", metadata={"file": "a.py"})]
    embeddings = [[0.1, 0.2, ...]]
    await store.add_documents(docs, embeddings)
    
    # 搜索
    results = await store.search(query_embedding, top_k=5)
    
    await store.close()
    ```
    """
    
    # Payload 字段名常量
    FIELD_CONTENT = "content"
    FIELD_FILE = "file"
    FIELD_METADATA = "metadata"
    
    def __init__(
        self,
        collection_name: str,
        config: Optional[QdrantConfig] = None
    ):
        self.collection_name = self._sanitize_name(collection_name)
        self.config = config or QdrantConfig.from_env()
        self._initialized = False
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """清理集合名称"""
        import re
        clean = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        return clean[:63] if clean else "default"
    
    async def _get_client(self) -> AsyncQdrantClient:
        """获取共享客户端 (解决 Qdrant Local 并发访问问题)"""
        return await get_shared_client(self.config)
    
    async def initialize(self) -> None:
        """初始化集合"""
        if self._initialized:
            return
        
        client = await self._get_client()
        
        # 检查集合是否存在
        collections = await client.get_collections()
        exists = any(c.name == self.collection_name for c in collections.collections)
        
        if not exists:
            # 创建集合
            await client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.config.vector_size,
                    distance=self.config.distance,
                    hnsw_config=models.HnswConfigDiff(
                        m=self.config.hnsw_m,
                        ef_construct=self.config.hnsw_ef_construct,
                    ),
                ),
                # 启用 payload 索引以加速过滤
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=0,  # 立即索引
                ),
            )
            
            # 创建 payload 索引
            await client.create_payload_index(
                collection_name=self.collection_name,
                field_name=self.FIELD_FILE,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            
            logger.info(f"✅ 创建集合: {self.collection_name}")
        else:
            logger.debug(f"📂 集合已存在: {self.collection_name}")
        
        self._initialized = True
    
    async def close(self) -> None:
        """
        关闭连接 (使用共享客户端时不实际关闭)
        
        注意: 由于使用共享客户端，单个 Store 的 close() 不会关闭客户端。
        全局关闭请使用 close_shared_client()
        """
        self._initialized = False
        logger.debug(f"🔌 Store 已关闭: {self.collection_name}")
    
    async def add_documents(
        self,
        documents: List[Document],
        embeddings: List[List[float]]
    ) -> int:
        """批量添加文档"""
        if not documents or not embeddings:
            return 0
        
        if len(documents) != len(embeddings):
            raise ValueError(f"文档数量 ({len(documents)}) 与向量数量 ({len(embeddings)}) 不匹配")
        
        await self.initialize()
        client = await self._get_client()
        
        # 过滤空向量
        valid_pairs = [
            (doc, emb) for doc, emb in zip(documents, embeddings)
            if emb and len(emb) == self.config.vector_size
        ]
        
        if not valid_pairs:
            logger.warning("没有有效的文档向量对")
            return 0
        
        # 构建 Points
        points = []
        for doc, embedding in valid_pairs:
            point = PointStruct(
                id=self._generate_point_id(doc.id),
                vector=embedding,
                payload={
                    self.FIELD_CONTENT: doc.content,
                    self.FIELD_FILE: doc.file_path,
                    self.FIELD_METADATA: doc.metadata,
                    "doc_id": doc.id,
                },
            )
            points.append(point)
        
        # 批量 upsert
        total_added = 0
        batch_size = self.config.batch_size
        
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            try:
                await client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                    wait=True,
                )
                total_added += len(batch)
            except Exception as e:
                logger.error(f"批次 {i // batch_size + 1} 写入失败: {e}")
        
        logger.info(f"✅ 写入 {total_added}/{len(points)} 个文档到 {self.collection_name}")
        return total_added
    
    def _generate_point_id(self, doc_id: str) -> int:
        """生成数值型 Point ID (Qdrant 要求)"""
        import hashlib
        hash_bytes = hashlib.sha256(doc_id.encode()).digest()
        # 取前 8 字节转为正整数
        return int.from_bytes(hash_bytes[:8], byteorder='big') & 0x7FFFFFFFFFFFFFFF
    
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_conditions: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """向量相似度搜索"""
        if not query_embedding:
            return []
        
        await self.initialize()
        client = await self._get_client()
        
        # 构建过滤器
        query_filter = None
        if filter_conditions:
            must_conditions = []
            for field, value in filter_conditions.items():
                must_conditions.append(
                    FieldCondition(
                        key=field,
                        match=MatchValue(value=value),
                    )
                )
            query_filter = Filter(must=must_conditions)
        
        try:
            # 使用 query_points (qdrant-client >= 1.7.0)
            results = await client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                limit=top_k,
                query_filter=query_filter,
                with_payload=True,
                score_threshold=0.0,
            )
            
            search_results = []
            for hit in results.points:
                payload = hit.payload or {}
                doc = Document(
                    id=payload.get("doc_id", str(hit.id)),
                    content=payload.get(self.FIELD_CONTENT, ""),
                    metadata=payload.get(self.FIELD_METADATA, {}),
                )
                search_results.append(SearchResult(
                    document=doc,
                    score=hit.score,
                    source="vector",
                ))
            
            return search_results
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []
    
    async def delete_collection(self) -> bool:
        """删除集合"""
        try:
            client = await self._get_client()
            await client.delete_collection(self.collection_name)
            self._initialized = False
            logger.info(f"🗑️ 删除集合: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"删除集合失败: {e}")
            return False
    
    async def get_stats(self) -> CollectionStats:
        """获取集合统计"""
        await self.initialize()
        client = await self._get_client()
        
        try:
            info = await client.get_collection(self.collection_name)
            
            # 获取所有唯一文件
            indexed_files: Set[str] = set()
            scroll_result = await client.scroll(
                collection_name=self.collection_name,
                limit=10000,
                with_payload=[self.FIELD_FILE],
            )
            
            for point in scroll_result[0]:
                if point.payload:
                    file_path = point.payload.get(self.FIELD_FILE)
                    if file_path:
                        indexed_files.add(file_path)
            
            return CollectionStats(
                name=self.collection_name,
                document_count=info.points_count or 0,
                indexed_files=indexed_files,
                vector_dimension=self.config.vector_size,
            )
        except Exception as e:
            logger.error(f"获取统计失败: {e}")
            return CollectionStats(name=self.collection_name, document_count=0)
    
    async def get_documents_by_file(self, file_path: str) -> List[Document]:
        """根据文件路径获取文档"""
        await self.initialize()
        client = await self._get_client()
        
        try:
            scroll_result = await client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key=self.FIELD_FILE,
                            match=MatchValue(value=file_path),
                        )
                    ]
                ),
                limit=1000,
                with_payload=True,
            )
            
            documents = []
            for point in scroll_result[0]:
                payload = point.payload or {}
                doc = Document(
                    id=payload.get("doc_id", str(point.id)),
                    content=payload.get(self.FIELD_CONTENT, ""),
                    metadata=payload.get(self.FIELD_METADATA, {}),
                )
                documents.append(doc)
            
            # 按行号排序
            documents.sort(key=lambda d: d.metadata.get("start_line", 0))
            return documents
            
        except Exception as e:
            logger.error(f"获取文件文档失败: {e}")
            return []
    
    async def get_all_documents(self) -> List[Document]:
        """获取所有文档 (用于 BM25 索引构建)"""
        await self.initialize()
        client = await self._get_client()
        
        documents = []
        offset = None
        
        try:
            while True:
                scroll_result = await client.scroll(
                    collection_name=self.collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                )
                
                points, next_offset = scroll_result
                
                for point in points:
                    payload = point.payload or {}
                    doc = Document(
                        id=payload.get("doc_id", str(point.id)),
                        content=payload.get(self.FIELD_CONTENT, ""),
                        metadata=payload.get(self.FIELD_METADATA, {}),
                    )
                    documents.append(doc)
                
                if next_offset is None:
                    break
                offset = next_offset
            
            return documents
            
        except Exception as e:
            logger.error(f"获取所有文档失败: {e}")
            return []


# ============================================================
# 工厂
# ============================================================

class QdrantStoreFactory:
    """Qdrant 存储工厂"""
    
    def __init__(self, config: Optional[QdrantConfig] = None):
        self.config = config or QdrantConfig.from_env()
    
    def create(self, collection_name: str) -> QdrantVectorStore:
        """创建存储实例"""
        return QdrantVectorStore(collection_name, self.config)
    
    async def get_client(self) -> AsyncQdrantClient:
        """获取共享的 Qdrant 客户端"""
        return await get_shared_client(self.config)


# 全局工厂实例
_qdrant_factory: Optional[QdrantStoreFactory] = None


def get_qdrant_factory(config: Optional[QdrantConfig] = None) -> QdrantStoreFactory:
    """获取工厂单例"""
    global _qdrant_factory
    if _qdrant_factory is None:
        _qdrant_factory = QdrantStoreFactory(config)
    return _qdrant_factory
