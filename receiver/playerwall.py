"""应用 A（PlayerWall）业务：皮肤墙卡片 CRUD + 公开 API。

- profile.created   → 回查 /v2/users/me/profiles → 建卡片
- profile.updated   → 回查最新 → 更新卡片
- profile.deleted   → 下架卡片（回查 404 也正确处理）
- texture.created/updated → 更新卡片纹理 URL（引用，不下载文件）
- texture.deleted   → 清空纹理引用
- oauth_grant.revoked → 下架该用户全部卡片
- 公开 API：GET /api/players、GET /api/players/{uuid}
- 面板：GET /admin/status
"""
from __future__ import annotations

import logging
from typing import Any

from .db import Database

logger = logging.getLogger(__name__)


class PlayerWall:
    """处理应用 A 的 webhook 事件，维护皮肤墙卡片。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def handle(self, event_type: str, data: dict[str, Any]) -> None:
        """按事件类型处理。所有操作以 event.id 幂等（由 inbox 保证不重复处理）。"""
        if event_type == "profile.created":
            self._on_profile_created(data)
        elif event_type == "profile.updated":
            self._on_profile_updated(data)
        elif event_type == "profile.deleted":
            self._on_profile_deleted(data)
        elif event_type == "texture.created":
            self._on_texture_created(data)
        elif event_type == "texture.updated":
            self._on_texture_updated(data)
        elif event_type == "texture.deleted":
            self._on_texture_deleted(data)
        elif event_type == "oauth_grant.revoked":
            self._on_grant_revoked(data)
        else:
            logger.warning("PlayerWall: unhandled event type %s", event_type)

    # ---------- 事件处理 ----------

    def _on_profile_created(self, data: dict[str, Any]) -> None:
        user_id = data.get("user_id")
        profile_id = data.get("profile_id")
        if not user_id or not profile_id:
            logger.warning("profile.created missing user_id/profile_id: %r", data)
            return
        # 回查 /v2 拉取角色详情由 worker 层完成（需要 access token），
        # 这里只做本地卡片占位；worker 拿到详情后调用 upsert_card。
        self._db.upsert_player(user_id, None)

    def _on_profile_updated(self, data: dict[str, Any]) -> None:
        user_id = data.get("user_id")
        profile_id = data.get("profile_id")
        if not user_id or not profile_id:
            logger.warning("profile.updated missing user_id/profile_id: %r", data)
            return
        self._db.upsert_player(user_id, None)

    def _on_profile_deleted(self, data: dict[str, Any]) -> None:
        profile_id = data.get("profile_id")
        if not profile_id:
            logger.warning("profile.deleted missing profile_id: %r", data)
            return
        # 资源可能已不存在，直接下架卡片（契约：data 不保证资源仍存在）
        self._db.remove_card(profile_id)

    def _on_texture_created(self, data: dict[str, Any]) -> None:
        # 纹理事件只携带 hash/type/user_id，具体应用到哪个角色由 worker 回查决定
        user_id = data.get("user_id")
        if user_id:
            self._db.upsert_player(user_id, None)

    def _on_texture_updated(self, data: dict[str, Any]) -> None:
        user_id = data.get("user_id")
        if user_id:
            self._db.upsert_player(user_id, None)

    def _on_texture_deleted(self, data: dict[str, Any]) -> None:
        # 纹理删除后，由 worker 回查该用户当前角色并刷新纹理引用
        user_id = data.get("user_id")
        if user_id:
            self._db.upsert_player(user_id, None)

    def _on_grant_revoked(self, data: dict[str, Any]) -> None:
        user_id = data.get("user_id")
        if not user_id:
            logger.warning("oauth_grant.revoked missing user_id: %r", data)
            return
        # 授权终止 = 数据移除：下架该用户全部角色卡片
        self._db.remove_player(user_id)

    # ---------- 公开 API ----------

    def public_players(self) -> list[dict[str, Any]]:
        return self._db.list_cards("active")

    def public_player(self, profile_id: str) -> dict[str, Any] | None:
        card = self._db.get_card(profile_id)
        if card is None or card["status"] != "active":
            return None
        return card

    def status(self) -> dict[str, Any]:
        return self._db.player_stats()
