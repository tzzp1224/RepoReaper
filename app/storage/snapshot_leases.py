"""Cross-process read leases for published repository index collections."""

import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # POSIX shared locks are required for garbage collection.
    fcntl = None


@asynccontextmanager
async def collection_lease(root: Path, namespace: str, *, exclusive: bool = False, timeout: float = 5):
    if not re.fullmatch(r"snapshot_[0-9a-f]{40}", namespace):
        raise ValueError("Invalid snapshot collection")
    if fcntl is None:
        if exclusive:
            raise RuntimeError("Snapshot collection requires POSIX shared file locks")
        yield
        return

    lock_dir = root / ".snapshot_leases"
    lock_dir.mkdir(parents=True, exist_ok=True)
    operation = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    deadline = time.monotonic() + timeout
    with (lock_dir / namespace).open("a") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), operation)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Snapshot collection has in-flight readers")
                await asyncio.sleep(0.025)
        yield


@asynccontextmanager
async def pin_snapshot(store):
    state = await asyncio.to_thread(store.load_context) or {}
    while (manifest := state.get("snapshot_index")):
        namespace = manifest["namespace"]
        async with collection_lease(Path(store._context_file).parent, namespace):
            state = await asyncio.to_thread(store.load_context) or {}
            if (state.get("snapshot_index") or {}).get("namespace") == namespace:
                yield state
                return
    yield state
