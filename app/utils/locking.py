# -*- coding: utf-8 -*-
"""
通用锁工具

目标:
1. 统一 keyed asyncio 锁逻辑，避免多处重复实现
2. 提供带文件锁 + 原子写的 JSON 状态更新器
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from filelock import FileLock, Timeout as FileLockTimeout


class KeyedAsyncLocks:
    """按 key 维度管理 asyncio 锁。"""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def acquire(self, key: str, timeout: float) -> bool:
        lock = await self._get_lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def release(self, key: str) -> None:
        lock = self._locks.get(key)
        if lock and lock.locked():
            lock.release()

    async def is_locked(self, key: str) -> bool:
        lock = self._locks.get(key)
        return bool(lock and lock.locked())


class AtomicJsonFileStore:
    """
    JSON 文件原子更新器。

    特性:
    - 跨进程: FileLock
    - 同进程线程安全: RLock
    - 原子落盘: tempfile + fsync + os.replace
    """

    def __init__(
        self,
        file_path: str,
        lock_path: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.file_path = file_path
        self.lock_path = lock_path or f"{file_path}.lock"
        self.timeout_seconds = timeout_seconds
        self._thread_lock = threading.RLock()

    def read(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            return {}
        with open(self.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def update(
        self,
        updater: Callable[[Dict[str, Any]], None],
        *,
        op_name: str,
        logger=None,
    ) -> bool:
        lock = FileLock(self.lock_path, timeout=self.timeout_seconds)
        try:
            with self._thread_lock:
                with lock:
                    payload = self.read()
                    updater(payload)
                    self._write_atomic(payload)
                    return True
        except FileLockTimeout:
            if logger:
                logger.error("%s失败: 获取文件锁超时 (%s)", op_name, self.file_path)
        except Exception as e:
            if logger:
                logger.error("%s失败: %s", op_name, e)
        return False

    def clear(
        self,
        extra_paths: Optional[Iterable[str]] = None,
        *,
        op_name: str = "清理状态",
        logger=None,
    ) -> bool:
        targets = [self.file_path]
        if extra_paths:
            targets.extend(extra_paths)

        lock = FileLock(self.lock_path, timeout=self.timeout_seconds)
        try:
            with self._thread_lock:
                with lock:
                    for path in targets:
                        if path and os.path.exists(path):
                            os.remove(path)
                    return True
        except FileLockTimeout:
            if logger:
                logger.warning("%s超时: %s", op_name, self.file_path)
        except Exception as e:
            if logger:
                logger.warning("%s失败: %s", op_name, e)
        return False

    def _write_atomic(self, payload: Dict[str, Any]) -> None:
        target_dir = os.path.dirname(self.file_path) or "."
        os.makedirs(target_dir, exist_ok=True)
        prefix = f"{Path(self.file_path).name}."
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=prefix, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.file_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
