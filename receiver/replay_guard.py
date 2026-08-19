"""持久化 ReplayGuard（基于 SQLite）。

- 表：replay_keys(event_id TEXT PRIMARY KEY, delivery_id TEXT, claimed_at_ms INTEGER)
- claim(event, expires_at_ms) 原子插入：INSERT OR IGNORE，返回是否首次
- 与 inbox 写入同一事务（或先 claim 后写 inbox，保证幂等）
- 生产语义：只有事件已可靠入队/已处理时才返回 2xx
"""
from __future__ import annotations

import sqlite3
import threading

from element_skin_sdk import WebhookEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_keys (
    event_id      TEXT PRIMARY KEY,
    delivery_id   TEXT NOT NULL,
    claimed_at_ms INTEGER NOT NULL
);
"""


class SqliteReplayGuard:
    """基于 SQLite 的持久化重放防护。

    实现 element_skin_sdk.ReplayGuard 协议：claim(event, expires_at_ms)。
    以 event_id 为幂等键（契约：Webhook-Id 是业务幂等键）。delivery_id 也记录，
    便于排错。claim 使用 INSERT OR IGNORE 原子语义，跨进程安全（SQLite 文件锁）。
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def claim(self, event: WebhookEvent, expires_at_ms: int) -> bool:
        """返回 True 表示首次接收，False 表示已存在（重放）。

        expires_at_ms 是签名重放键的最短保留边界；业务 inbox 和 event.id 幂等记录
        通常需要保留更久，这里保留全部历史（测试场景数据量小）。
        """
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO replay_keys (event_id, delivery_id, claimed_at_ms) "
                "VALUES (?, ?, ?)",
                (event.id, event.delivery_id, expires_at_ms),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def has(self, event_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM replay_keys WHERE event_id = ?", (event_id,)
            )
            return cursor.fetchone() is not None

    def count(self) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM replay_keys")
            return int(cursor.fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
