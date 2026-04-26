# -*- coding: utf-8 -*-
"""
运行追踪关系存储（SQLite）

表:
- runs
- steps
- tool_calls
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class RuntimeTraceStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("RUNTIME_TRACE_DB_PATH", "data/runtime_traces.db")
        self._lock = threading.RLock()
        self._initialized = False

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _json_dumps(value: Any) -> str:
        try:
            return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)
        except Exception:
            return "{}"

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_schema(self) -> None:
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL UNIQUE,
                        session_id TEXT,
                        trace_name TEXT,
                        status TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        step_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        message TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES runs(run_id)
                    );

                    CREATE TABLE IF NOT EXISTS tool_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        parameters_json TEXT NOT NULL DEFAULT '{}',
                        result_preview TEXT,
                        latency_ms REAL,
                        success INTEGER NOT NULL,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES runs(run_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_runs_trace_id ON runs(trace_id);
                    CREATE INDEX IF NOT EXISTS idx_steps_run_id_created_at ON steps(run_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id_created_at ON tool_calls(run_id, created_at);
                    """
                )
                conn.commit()

            self._initialized = True

    def _resolve_run_id(self, conn: sqlite3.Connection, trace_id: str) -> Optional[str]:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE trace_id = ? LIMIT 1",
            (trace_id,),
        ).fetchone()
        return str(row["run_id"]) if row else None

    def _ensure_run_exists(
        self,
        conn: sqlite3.Connection,
        trace_id: str,
        *,
        session_id: Optional[str] = None,
    ) -> str:
        run_id = self._resolve_run_id(conn, trace_id)
        if run_id:
            return run_id

        now = self._now_iso()
        run_id = trace_id
        conn.execute(
            """
            INSERT INTO runs (
                run_id, trace_id, session_id, trace_name, status, metadata_json, started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trace_id,
                session_id,
                "unknown",
                "running",
                "{}",
                now,
                now,
            ),
        )
        return run_id

    def start_run(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str,
        trace_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_schema()
        now = self._now_iso()
        metadata_json = self._json_dumps(metadata)

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, trace_id, session_id, trace_name, status, metadata_json, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        trace_id=excluded.trace_id,
                        session_id=excluded.session_id,
                        trace_name=excluded.trace_name,
                        status='running',
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        run_id,
                        trace_id,
                        session_id,
                        trace_name,
                        "running",
                        metadata_json,
                        now,
                        now,
                    ),
                )
                conn.commit()

    def finish_run(
        self,
        *,
        trace_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_schema()
        now = self._now_iso()

        with self._lock:
            with self._connect() as conn:
                run_id = self._ensure_run_exists(conn, trace_id)
                existing_row = conn.execute(
                    "SELECT metadata_json FROM runs WHERE run_id = ? LIMIT 1",
                    (run_id,),
                ).fetchone()
                existing_metadata: Dict[str, Any] = {}
                if existing_row and existing_row["metadata_json"]:
                    try:
                        parsed = json.loads(existing_row["metadata_json"])
                        if isinstance(parsed, dict):
                            existing_metadata = parsed
                    except Exception:
                        existing_metadata = {}
                if isinstance(metadata, dict):
                    existing_metadata.update(metadata)

                conn.execute(
                    """
                    UPDATE runs
                    SET status = ?, metadata_json = ?, ended_at = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        status,
                        self._json_dumps(existing_metadata),
                        now,
                        now,
                        run_id,
                    ),
                )
                conn.commit()

    def add_step(
        self,
        *,
        trace_id: str,
        step_name: str,
        status: str = "info",
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> None:
        if not trace_id or not step_name:
            return

        self._ensure_schema()
        now = self._now_iso()

        with self._lock:
            with self._connect() as conn:
                run_id = self._ensure_run_exists(conn, trace_id, session_id=session_id)
                conn.execute(
                    """
                    INSERT INTO steps (
                        run_id, trace_id, step_name, status, message, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        trace_id,
                        step_name,
                        status,
                        message,
                        self._json_dumps(payload),
                        now,
                    ),
                )
                conn.commit()

    def add_tool_call(
        self,
        *,
        trace_id: str,
        tool_name: str,
        parameters: Optional[Dict[str, Any]] = None,
        result_preview: Optional[str] = None,
        latency_ms: Optional[float] = None,
        success: bool = True,
        error: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        if not trace_id or not tool_name:
            return

        self._ensure_schema()
        now = self._now_iso()

        with self._lock:
            with self._connect() as conn:
                run_id = self._ensure_run_exists(conn, trace_id, session_id=session_id)
                conn.execute(
                    """
                    INSERT INTO tool_calls (
                        run_id, trace_id, tool_name, parameters_json, result_preview, latency_ms, success, error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        trace_id,
                        tool_name,
                        self._json_dumps(parameters),
                        result_preview,
                        latency_ms,
                        1 if success else 0,
                        error,
                        now,
                    ),
                )
                conn.commit()

    # ===== 查询接口 (用于调试/测试) =====

    def get_run_by_trace_id(self, trace_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_steps(self, trace_id: str) -> List[Dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE trace_id = ? ORDER BY id ASC",
                (trace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_tool_calls(self, trace_id: str) -> List[Dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE trace_id = ? ORDER BY id ASC",
                (trace_id,),
            ).fetchall()
        return [dict(r) for r in rows]


runtime_trace_store = RuntimeTraceStore()
