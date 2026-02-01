# -*- coding: utf-8 -*-
"""
仓库级分布式锁

解决问题:
1. 同一仓库的并发写入竞争 (两人同时输入同一 URL)
2. 重新分析时的数据一致性 (用户 A 重分析，用户 B 同时查询)

设计原则:
- 单进程: asyncio.Lock (内存锁)
- 多进程: 文件锁 (fcntl/msvcrt)
- 多节点: 可选 Redis 分布式锁 (生产环境)

使用示例:
```python
async with RepoLock.acquire(session_id):
    # 独占访问该仓库的写操作
    await vector_store.reset()
    await vector_store.add_documents(docs)
```
"""

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 锁配置
# ============================================================

@dataclass
class LockConfig:
    """锁配置"""
    # 锁类型: "memory" | "file" | "redis"
    backend: str = os.getenv("LOCK_BACKEND", "file")
    
    # 文件锁目录
    lock_dir: str = os.getenv("LOCK_DIR", "data/locks")
    
    # Redis 配置 (可选)
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # 锁超时 (秒)
    lock_timeout: float = float(os.getenv("LOCK_TIMEOUT", "300"))  # 5分钟
    
    # 等待超时 (秒)
    acquire_timeout: float = float(os.getenv("LOCK_ACQUIRE_TIMEOUT", "60"))


# ============================================================
# 锁后端抽象
# ============================================================

class LockBackend(ABC):
    """锁后端接口"""
    
    @abstractmethod
    async def acquire(self, key: str, timeout: float) -> bool:
        """获取锁"""
        pass
    
    @abstractmethod
    async def release(self, key: str) -> None:
        """释放锁"""
        pass
    
    @abstractmethod
    async def is_locked(self, key: str) -> bool:
        """检查是否已锁定"""
        pass


# ============================================================
# 内存锁 (单进程)
# ============================================================

class MemoryLockBackend(LockBackend):
    """
    内存锁后端 (asyncio.Lock)
    
    适用于: 单 Worker 部署
    """
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()
    
    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]
    
    async def acquire(self, key: str, timeout: float) -> bool:
        lock = await self._get_lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
    
    async def release(self, key: str) -> None:
        if key in self._locks:
            lock = self._locks[key]
            if lock.locked():
                lock.release()
    
    async def is_locked(self, key: str) -> bool:
        if key not in self._locks:
            return False
        return self._locks[key].locked()


# ============================================================
# 文件锁 (多进程，单节点)
# ============================================================

class FileLockBackend(LockBackend):
    """
    文件锁后端
    
    适用于: 多 Worker 单节点部署 (Gunicorn + Qdrant Server)
    
    实现:
    - Windows: msvcrt.locking
    - Unix: fcntl.flock
    """
    
    def __init__(self, lock_dir: str):
        self._lock_dir = Path(lock_dir)
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._handles: Dict[str, object] = {}
        self._memory_locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()
    
    def _get_lock_path(self, key: str) -> Path:
        # 清理 key，避免路径注入
        safe_key = "".join(c if c.isalnum() or c in "_-" else "_" for c in key)
        return self._lock_dir / f"{safe_key}.lock"
    
    async def _get_memory_lock(self, key: str) -> asyncio.Lock:
        """同进程内的内存锁，防止同一进程内多个协程竞争文件锁"""
        async with self._meta_lock:
            if key not in self._memory_locks:
                self._memory_locks[key] = asyncio.Lock()
            return self._memory_locks[key]
    
    async def acquire(self, key: str, timeout: float) -> bool:
        # 先获取内存锁
        mem_lock = await self._get_memory_lock(key)
        try:
            await asyncio.wait_for(mem_lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        
        # 再获取文件锁
        lock_path = self._get_lock_path(key)
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 尝试获取文件锁
                handle = open(lock_path, 'w')
                
                if os.name == 'nt':
                    # Windows
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    # Unix
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                self._handles[key] = handle
                logger.debug(f"🔒 文件锁获取成功: {key}")
                return True
                
            except (IOError, OSError):
                # 锁被占用，等待后重试
                if 'handle' in dir() and handle:
                    handle.close()
                await asyncio.sleep(0.1)
        
        # 超时，释放内存锁
        mem_lock.release()
        logger.warning(f"⏰ 文件锁获取超时: {key}")
        return False
    
    async def release(self, key: str) -> None:
        if key in self._handles:
            handle = self._handles.pop(key)
            try:
                if os.name == 'nt':
                    import msvcrt
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except:
                        pass
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            except:
                pass
            logger.debug(f"🔓 文件锁已释放: {key}")
        
        # 释放内存锁
        if key in self._memory_locks:
            lock = self._memory_locks[key]
            if lock.locked():
                lock.release()
    
    async def is_locked(self, key: str) -> bool:
        lock_path = self._get_lock_path(key)
        if not lock_path.exists():
            return False
        
        try:
            handle = open(lock_path, 'w')
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            return False
        except (IOError, OSError):
            return True


# ============================================================
# Redis 锁 (分布式，多节点)
# ============================================================

class RedisLockBackend(LockBackend):
    """
    Redis 分布式锁后端
    
    适用于: 多节点部署 (K8s + Redis)
    
    依赖: redis[hiredis]
    """
    
    def __init__(self, redis_url: str, lock_timeout: float):
        self._redis_url = redis_url
        self._lock_timeout = lock_timeout
        self._client = None
        self._locks: Dict[str, object] = {}
    
    async def _get_client(self):
        if self._client is None:
            try:
                import redis.asyncio as aioredis
                self._client = await aioredis.from_url(self._redis_url)
            except ImportError:
                raise RuntimeError(
                    "Redis 锁需要安装 redis 包: pip install redis[hiredis]"
                )
        return self._client
    
    async def acquire(self, key: str, timeout: float) -> bool:
        client = await self._get_client()
        lock_key = f"repo_lock:{key}"
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            # 尝试设置锁
            acquired = await client.set(
                lock_key,
                "locked",
                nx=True,
                ex=int(self._lock_timeout)
            )
            if acquired:
                logger.debug(f"🔒 Redis 锁获取成功: {key}")
                return True
            await asyncio.sleep(0.1)
        
        logger.warning(f"⏰ Redis 锁获取超时: {key}")
        return False
    
    async def release(self, key: str) -> None:
        client = await self._get_client()
        lock_key = f"repo_lock:{key}"
        await client.delete(lock_key)
        logger.debug(f"🔓 Redis 锁已释放: {key}")
    
    async def is_locked(self, key: str) -> bool:
        client = await self._get_client()
        lock_key = f"repo_lock:{key}"
        return await client.exists(lock_key) > 0


# ============================================================
# 统一锁接口
# ============================================================

class RepoLock:
    """
    仓库级锁 - 统一接口
    
    自动根据配置选择后端:
    - memory: 单进程内存锁 (开发)
    - file: 文件锁 (多进程单节点)
    - redis: 分布式锁 (多节点)
    
    使用:
    ```python
    async with RepoLock.acquire(session_id):
        # 独占写操作
        await store.reset()
    ```
    """
    
    _backend: Optional[LockBackend] = None
    _config: Optional[LockConfig] = None
    
    @classmethod
    def _get_backend(cls) -> LockBackend:
        if cls._backend is None:
            cls._config = LockConfig()
            
            if cls._config.backend == "redis":
                cls._backend = RedisLockBackend(
                    cls._config.redis_url,
                    cls._config.lock_timeout
                )
                logger.info("🔐 使用 Redis 分布式锁")
            elif cls._config.backend == "file":
                cls._backend = FileLockBackend(cls._config.lock_dir)
                logger.info(f"🔐 使用文件锁: {cls._config.lock_dir}")
            else:
                cls._backend = MemoryLockBackend()
                logger.info("🔐 使用内存锁 (单进程)")
        
        return cls._backend
    
    @classmethod
    @asynccontextmanager
    async def acquire(cls, session_id: str, timeout: float = None):
        """
        获取仓库写锁
        
        Args:
            session_id: 仓库的 session ID
            timeout: 获取锁的超时时间 (默认从配置读取)
        
        Raises:
            TimeoutError: 获取锁超时
        """
        backend = cls._get_backend()
        config = cls._config or LockConfig()
        wait_timeout = timeout or config.acquire_timeout
        
        acquired = await backend.acquire(session_id, wait_timeout)
        if not acquired:
            raise TimeoutError(f"无法获取仓库锁: {session_id} (等待 {wait_timeout}s)")
        
        try:
            yield
        finally:
            await backend.release(session_id)
    
    @classmethod
    async def is_locked(cls, session_id: str) -> bool:
        """检查仓库是否被锁定"""
        backend = cls._get_backend()
        return await backend.is_locked(session_id)
    
    @classmethod
    async def try_acquire(cls, session_id: str, timeout: float = 0.1):
        """
        尝试获取锁 (非阻塞)
        
        用于检测是否有其他用户正在分析同一仓库
        """
        backend = cls._get_backend()
        return await backend.acquire(session_id, timeout)
