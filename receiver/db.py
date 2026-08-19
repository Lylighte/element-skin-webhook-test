"""SQLite 数据层：inbox、事件记录、PlayerWall 卡片、Probe 事件。

所有写操作使用单连接 + 锁（SQLite 单写者），保证原子性。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

_SCHEMA = """
-- 已认证事件 inbox（业务处理队列）
CREATE TABLE IF NOT EXISTS inbox (
    event_id      TEXT PRIMARY KEY,
    delivery_id   TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    data_json     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | done | failed
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    processed_at_ms INTEGER
);

-- 全部到达事件（含验签结果，供面板与报告统计）
CREATE TABLE IF NOT EXISTS events_log (
    event_id      TEXT PRIMARY KEY,
    delivery_id   TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    data_json     TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 1,
    error         TEXT
);

-- PlayerWall 玩家（授权过应用 A 的用户）
CREATE TABLE IF NOT EXISTS players (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | removed
    first_seen_ms INTEGER NOT NULL,
    removed_at_ms INTEGER
);

-- PlayerWall 角色卡片
CREATE TABLE IF NOT EXISTS cards (
    profile_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    texture_model TEXT NOT NULL DEFAULT 'default',
    skin_url      TEXT,
    cape_url      TEXT,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | removed
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    removed_at_ms INTEGER
);

-- Probe 事件记录
CREATE TABLE IF NOT EXISTS probe_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    delivery_id   TEXT NOT NULL,
    data_json     TEXT NOT NULL,
    received_at_ms INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- inbox ----------

    def enqueue(self, event_id: str, delivery_id: str, endpoint: str, event_type: str,
                created_at_ms: int, received_at_ms: int, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO inbox "
                "(event_id, delivery_id, endpoint, event_type, created_at_ms, received_at_ms, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, delivery_id, endpoint, event_type, created_at_ms, received_at_ms,
                 json.dumps(data, ensure_ascii=False)),
            )
            self._conn.commit()

    def claim_next(self, limit: int = 10) -> list[dict[str, Any]]:
        """领取待处理事件（单进程内用 status 标记，简单可靠）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, delivery_id, endpoint, event_type, created_at_ms, data_json "
                "FROM inbox WHERE status = 'pending' ORDER BY received_at_ms LIMIT ?",
                (limit,),
            ).fetchall()
            if not rows:
                return []
            ids = [row[0] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"UPDATE inbox SET status = 'processing' WHERE event_id IN ({placeholders})",
                ids,
            )
            self._conn.commit()
            return [
                {
                    "event_id": row[0],
                    "delivery_id": row[1],
                    "endpoint": row[2],
                    "event_type": row[3],
                    "created_at_ms": row[4],
                    "data": json.loads(row[5]),
                }
                for row in rows
            ]

    def mark_done(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE inbox SET status = 'done', processed_at_ms = ? WHERE event_id = ?",
                (time.time_ns() // 1_000_000, event_id),
            )
            self._conn.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE inbox SET status = 'failed', last_error = ?, attempts = attempts + 1 "
                "WHERE event_id = ?",
                (error[:500], event_id),
            )
            self._conn.commit()

    def inbox_stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM inbox GROUP BY status"
            ).fetchall()
            return {row[0]: row[1] for row in rows}

    # ---------- events_log ----------

    def log_event(self, event_id: str, delivery_id: str, endpoint: str, event_type: str,
                  created_at_ms: int, received_at_ms: int, data: dict[str, Any],
                  verified: bool = True, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO events_log "
                "(event_id, delivery_id, endpoint, event_type, created_at_ms, received_at_ms, data_json, verified, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, delivery_id, endpoint, event_type, created_at_ms, received_at_ms,
                 json.dumps(data, ensure_ascii=False), 1 if verified else 0, error),
            )
            self._conn.commit()

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, delivery_id, endpoint, event_type, created_at_ms, received_at_ms, verified, error "
                "FROM events_log ORDER BY received_at_ms DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "event_id": row[0],
                    "delivery_id": row[1],
                    "endpoint": row[2],
                    "event_type": row[3],
                    "created_at_ms": row[4],
                    "received_at_ms": row[5],
                    "verified": bool(row[6]),
                    "error": row[7],
                }
                for row in rows
            ]

    def events_by_type(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, COUNT(*) FROM events_log GROUP BY event_type"
            ).fetchall()
            return {row[0]: row[1] for row in rows}

    # ---------- PlayerWall ----------

    def upsert_player(self, user_id: str, display_name: str | None) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            self._conn.execute(
                "INSERT INTO players (user_id, display_name, status, first_seen_ms) "
                "VALUES (?, ?, 'active', ?) "
                "ON CONFLICT(user_id) DO UPDATE SET display_name = COALESCE(excluded.display_name, players.display_name), "
                "status = 'active'",
                (user_id, display_name, now),
            )
            self._conn.commit()

    def remove_player(self, user_id: str) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            self._conn.execute(
                "UPDATE players SET status = 'removed', removed_at_ms = ? WHERE user_id = ?",
                (now, user_id),
            )
            self._conn.execute(
                "UPDATE cards SET status = 'removed', removed_at_ms = ? WHERE user_id = ? AND status = 'active'",
                (now, user_id),
            )
            self._conn.commit()

    def upsert_card(self, profile_id: str, user_id: str, name: str, texture_model: str,
                    skin_url: str | None, cape_url: str | None) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            self._conn.execute(
                "INSERT INTO cards (profile_id, user_id, name, texture_model, skin_url, cape_url, "
                "status, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(profile_id) DO UPDATE SET "
                "name = excluded.name, texture_model = excluded.texture_model, "
                "skin_url = excluded.skin_url, cape_url = excluded.cape_url, "
                "status = 'active', updated_at_ms = excluded.updated_at_ms",
                (profile_id, user_id, name, texture_model, skin_url, cape_url, now, now),
            )
            self._conn.commit()

    def remove_card(self, profile_id: str) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            self._conn.execute(
                "UPDATE cards SET status = 'removed', removed_at_ms = ? WHERE profile_id = ?",
                (now, profile_id),
            )
            self._conn.commit()

    def update_card_texture(self, profile_id: str, skin_url: str | None, cape_url: str | None) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            self._conn.execute(
                "UPDATE cards SET skin_url = COALESCE(?, skin_url), cape_url = COALESCE(?, cape_url), "
                "updated_at_ms = ? WHERE profile_id = ?",
                (skin_url, cape_url, now, profile_id),
            )
            self._conn.commit()

    def clear_card_texture(self, profile_id: str, texture_type: str) -> None:
        with self._lock:
            now = time.time_ns() // 1_000_000
            if texture_type == "skin":
                self._conn.execute(
                    "UPDATE cards SET skin_url = NULL, updated_at_ms = ? WHERE profile_id = ?",
                    (now, profile_id),
                )
            else:
                self._conn.execute(
                    "UPDATE cards SET cape_url = NULL, updated_at_ms = ? WHERE profile_id = ?",
                    (now, profile_id),
                )
            self._conn.commit()

    def list_cards(self, status: str = "active") -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT profile_id, user_id, name, texture_model, skin_url, cape_url, status, "
                "created_at_ms, updated_at_ms FROM cards WHERE status = ? ORDER BY updated_at_ms DESC",
                (status,),
            ).fetchall()
            return [
                {
                    "profile_id": row[0],
                    "user_id": row[1],
                    "name": row[2],
                    "texture_model": row[3],
                    "skin_url": row[4],
                    "cape_url": row[5],
                    "status": row[6],
                    "created_at_ms": row[7],
                    "updated_at_ms": row[8],
                }
                for row in rows
            ]

    def get_card(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT profile_id, user_id, name, texture_model, skin_url, cape_url, status, "
                "created_at_ms, updated_at_ms FROM cards WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "profile_id": row[0],
                "user_id": row[1],
                "name": row[2],
                "texture_model": row[3],
                "skin_url": row[4],
                "cape_url": row[5],
                "status": row[6],
                "created_at_ms": row[7],
                "updated_at_ms": row[8],
            }

    def player_stats(self) -> dict[str, int]:
        with self._lock:
            players = self._conn.execute(
                "SELECT COUNT(*) FROM players WHERE status = 'active'"
            ).fetchone()[0]
            cards = self._conn.execute(
                "SELECT COUNT(*) FROM cards WHERE status = 'active'"
            ).fetchone()[0]
            return {"active_players": players, "active_cards": cards}

    # ---------- Probe ----------

    def record_probe_event(self, event_id: str, event_type: str, delivery_id: str,
                           data: dict[str, Any], received_at_ms: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO probe_events (event_id, event_type, delivery_id, data_json, received_at_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, event_type, delivery_id, json.dumps(data, ensure_ascii=False), received_at_ms),
            )
            self._conn.commit()

    def probe_stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, COUNT(*) FROM probe_events GROUP BY event_type"
            ).fetchall()
            return {row[0]: row[1] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
