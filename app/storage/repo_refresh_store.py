"""Durable, coalesced repository refresh requests for multiple app workers."""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class RepoRefreshStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("REPO_REFRESH_DB_PATH", "data/repo_refresh.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, received REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS refreshes (
                    repo TEXT PRIMARY KEY, desired TEXT NOT NULL, generation INTEGER NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL, available REAL NOT NULL,
                    lease TEXT, lease_until REAL, published TEXT, error TEXT
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, delivery: str, repo: str, revision: str) -> bool:
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM deliveries WHERE received < ?", (now - 7 * 86400,))
            if not db.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?)", (delivery, now)).rowcount:
                return False
            return bool(db.execute("""
                INSERT INTO refreshes (repo, desired, generation, status, attempts, available)
                VALUES (?, ?, 1, 'queued', 0, ?)
                ON CONFLICT(repo) DO UPDATE SET desired=excluded.desired, generation=generation+1,
                    status=CASE WHEN status='running' THEN status ELSE 'queued' END,
                    attempts=0,
                    available=excluded.available, error=NULL
                WHERE desired != excluded.desired OR status='failed'
            """, (repo, revision, now)).rowcount)

    def claim(self) -> dict | None:
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # A crashed worker may have run its last attempt before it recorded the outcome.
            db.execute("UPDATE refreshes SET status='failed', error='lease_expired', lease=NULL "
                       "WHERE status='running' AND lease_until<=? AND attempts>=3", (now,))
            row = db.execute("SELECT * FROM refreshes WHERE (status='queued' AND available<=?) "
                             "OR (status='running' AND lease_until<=?) ORDER BY available LIMIT 1", (now, now)).fetchone()
            if not row:
                return None
            lease = uuid4().hex
            db.execute("UPDATE refreshes SET lease=?, lease_until=?, status='running', attempts=attempts+1 WHERE repo=?",
                       (lease, now + 300, row["repo"]))
            return dict(row, lease=lease, attempts=row["attempts"] + 1)

    def finish(self, job: dict, *, published: str | None = None, error: str | None = None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT generation FROM refreshes WHERE repo=? AND lease=?",
                                 (job["repo"], job["lease"])).fetchone()
            if not current:
                return
            superseded = current["generation"] != job["generation"]
            attempts = 0 if superseded else job["attempts"]
            if superseded:
                status, delay = "queued", 0
            elif published:
                status, delay = "completed", 0
            else:
                status, delay = ("queued", 15 * attempts) if attempts < 3 else ("failed", 0)
            db.execute("UPDATE refreshes SET status=?, available=?, lease=NULL, lease_until=NULL, attempts=?, "
                       "published=COALESCE(?,published), error=? WHERE repo=? AND lease=?",
                       (status, time.time()+delay, attempts, published, None if superseded else error,
                        job["repo"], job["lease"]))

    def status(self, repo: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT desired, status, published, error FROM refreshes WHERE repo=?", (repo,)).fetchone()
        return dict(row) if row else None
