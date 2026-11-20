"""Single orchestrator per run; the usage ledger additionally locks each reservation."""
import fcntl
import inspect
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


@contextmanager
def run_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another generation/judge process owns this run") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def exclusive_run(function):
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def call(directory, *args, **kwargs):
            with run_lock(directory):
                return await function(directory, *args, **kwargs)
    else:
        @wraps(function)
        def call(directory, *args, **kwargs):
            with run_lock(directory):
                return function(directory, *args, **kwargs)
    return call
