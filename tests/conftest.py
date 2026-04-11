# -*- coding: utf-8 -*-
"""
测试全局 conftest

在任何 app.* 模块被 import 之前，先设置环境变量并 stub 掉重型第三方依赖，
使测试不需要 qdrant_client、openai、sse_starlette 等即可离线运行。
"""

import os
import sys
import types

# ---- 1. 环境变量 ----
os.environ.setdefault("DEEPSEEK_API_KEY", "test-mock-key-not-real")
os.environ.setdefault("LLM_PROVIDER", "deepseek")


def _ensure_stub(name, attrs=None):
    """确保 sys.modules 里有一个 stub module，并设置指定属性。"""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    mod = sys.modules[name]
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    return mod


# ---- 2. qdrant_client ----
_dummy_cls = type("_Dummy", (), {"__init__": lambda self, *a, **kw: None})


class _FakeDistance:
    COSINE = "Cosine"
    EUCLID = "Euclid"
    DOT = "Dot"


class _FakePayloadSchemaType:
    KEYWORD = "keyword"
    INTEGER = "integer"
    FLOAT = "float"
    TEXT = "text"


_ensure_stub("qdrant_client", {
    "AsyncQdrantClient": _dummy_cls,
    "models": _ensure_stub("qdrant_client.models", {
        "Distance": _FakeDistance,
        "VectorParams": _dummy_cls,
        "PointStruct": _dummy_cls,
        "Filter": _dummy_cls,
        "FieldCondition": _dummy_cls,
        "MatchValue": _dummy_cls,
        "PayloadSchemaType": _FakePayloadSchemaType,
    }),
})

# ---- 3. openai (被 app.utils.embedding import) ----
_ensure_stub("openai", {
    "AsyncOpenAI": _dummy_cls,
    "OpenAI": _dummy_cls,
})

# ---- 4. sse_starlette ----
_ensure_stub("sse_starlette")
_ensure_stub("sse_starlette.sse", {
    "EventSourceResponse": _dummy_cls,
})
