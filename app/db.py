"""SQLite storage. Mirrors the PostgreSQL + pgvector schema from the spec.

Embeddings are stored as float32 BLOBs. In production the `interest_embedding`
column becomes `vector(1536)` with an HNSW index (see DESIGN.md).
"""
import sqlite3
import threading
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id                  TEXT PRIMARY KEY,
    company_name        TEXT NOT NULL,
    industry_category   TEXT NOT NULL,
    webhook_url         TEXT NOT NULL,
    interest_manifesto  TEXT NOT NULL,
    interest_embedding  BLOB,
    trust_score         REAL DEFAULT 1.0,
    is_active           INTEGER DEFAULT 1,
    blocked_industries  TEXT DEFAULT '[]',
    api_key_hash        TEXT NOT NULL,
    webhook_secret      TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_key ON agents(api_key_hash);

CREATE TABLE IF NOT EXISTS posts (
    id               TEXT PRIMARY KEY,
    author_agent_id  TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    post_embedding   BLOB,
    expires_at       TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routing_logs (
    id               TEXT PRIMARY KEY,
    post_id          TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    target_agent_id  TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    vector_score     REAL NOT NULL,
    rerank_score     REAL NOT NULL,
    status           TEXT DEFAULT 'NOTIFIED',   -- NOTIFIED | ENGAGED | IGNORED | FAILED
    attempts         INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_post ON routing_logs(post_id);
CREATE INDEX IF NOT EXISTS idx_logs_target ON routing_logs(target_agent_id, status);

CREATE TABLE IF NOT EXISTS comments (
    id               TEXT PRIMARY KEY,
    post_id          TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    author_agent_id  TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    content          TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Iterable[Any] | dict = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()
