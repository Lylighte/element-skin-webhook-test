"""后台 worker：从 inbox 取事件 → 幂等业务处理 → 记录结果。

- 独立线程池，不在 HTTP 请求内做慢操作
- 按 event.id 幂等处理（inbox 主键去重）
- 应用 A：回查 /v2 拉取角色/材质 → 更新皮肤墙卡片
- 应用 B：记录事件到 probe 表
- 处理结果（成功/失败/重试）落库，供 /admin/status 与报告统计
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from element_skin_sdk import ElementSkinAPI, OAuthClient

from .config import Config
from .db import Database
from .playerwall import PlayerWall
from .probe import Probe

logger = logging.getLogger(__name__)


class Worker:
    """后台处理循环：轮询 inbox，分发到对应业务模块。"""

    def __init__(self, config: Config, db: Database, playerwall: PlayerWall, probe: Probe) -> None:
        self._config = config
        self._db = db
        self._playerwall = playerwall
        self._probe = probe
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._api: ElementSkinAPI | None = None
        self._api_lock = threading.Lock()

    def start(self) -> None:
        for index in range(self._config.worker_threads):
            thread = threading.Thread(
                target=self._run_loop,
                name=f"webhook-worker-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        logger.info("Worker started with %d threads", self._config.worker_threads)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._db.claim_next(limit=10)
                for event in events:
                    self._process(event)
            except Exception:
                logger.exception("worker batch failed")
            self._stop.wait(self._config.poll_interval_ms / 1000)

    def _process(self, event: dict[str, Any]) -> None:
        event_id = event["event_id"]
        endpoint = event["endpoint"]
        event_type = event["event_type"]
        data = event["data"]
        try:
            if endpoint == "playerwall":
                self._process_playerwall(event_type, data)
            elif endpoint == "probe":
                self._probe.handle(event_id, event_type, event["delivery_id"], data, event["created_at_ms"])
            else:
                logger.warning("unknown endpoint %s", endpoint)
            self._db.mark_done(event_id)
        except Exception as exc:
            logger.exception("process event %s failed", event_id)
            self._db.mark_failed(event_id, str(exc))

    # ---------- 应用 A：回查 /v2 ----------

    def _process_playerwall(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type == "profile.created":
            # 先记录玩家，再尝试回查详情
            self._playerwall.handle(event_type, data)
            self._sync_profile(data.get("user_id"), data.get("profile_id"))
        elif event_type == "profile.updated":
            self._playerwall.handle(event_type, data)
            self._sync_profile(data.get("user_id"), data.get("profile_id"))
        elif event_type == "profile.deleted":
            self._playerwall.handle(event_type, data)
        elif event_type in ("texture.created", "texture.updated", "texture.deleted"):
            # 纹理变化后刷新该用户全部角色卡片的纹理引用
            self._playerwall.handle(event_type, data)
            self._sync_user_textures(data.get("user_id"))
        elif event_type == "oauth_grant.revoked":
            self._playerwall.handle(event_type, data)
        else:
            self._playerwall.handle(event_type, data)

    def _sync_profile(self, user_id: str | None, profile_id: str | None) -> None:
        """回查 /v2 拉取角色详情并更新卡片。"""
        if not user_id or not profile_id:
            return
        api = self._api_for_user(user_id)
        if api is None:
            logger.warning("no API client for user %s", user_id)
            return
        try:
            profiles = api.list_profiles()
        except Exception as exc:
            logger.warning("list_profiles failed for user %s: %s", user_id, exc)
            return
        items = profiles.get("items") or profiles.get("profiles") or []
        target = next((item for item in items if item.get("id") == profile_id), None)
        if target is None:
            # 角色已不存在（profile.deleted 场景），下架卡片
            self._playerwall.handle("profile.deleted", {"profile_id": profile_id})
            return
        self._db.upsert_card(
            profile_id=profile_id,
            user_id=user_id,
            name=target.get("name", ""),
            texture_model=target.get("texture_model", "default"),
            skin_url=target.get("skin_url"),
            cape_url=target.get("cape_url"),
        )

    def _sync_user_textures(self, user_id: str | None) -> None:
        """回查该用户当前角色并刷新纹理引用。"""
        if not user_id:
            return
        api = self._api_for_user(user_id)
        if api is None:
            return
        try:
            profiles = api.list_profiles()
        except Exception as exc:
            logger.warning("list_profiles failed for user %s: %s", user_id, exc)
            return
        items = profiles.get("items") or profiles.get("profiles") or []
        for item in items:
            profile_id = item.get("id")
            if not profile_id:
                continue
            self._db.upsert_card(
                profile_id=profile_id,
                user_id=user_id,
                name=item.get("name", ""),
                texture_model=item.get("texture_model", "default"),
                skin_url=item.get("skin_url"),
                cape_url=item.get("cape_url"),
            )

    # ---------- API 客户端管理 ----------

    def _api_for_user(self, user_id: str) -> ElementSkinAPI | None:
        """为指定用户获取 API 客户端。

        简化实现：应用 A 是用户委托应用，每个用户授权后持有自己的 refresh token。
        测试场景中我们用一个共享的授权账号（config.oauth.refresh_token）回查。
        生产实现应维护 user_id → refresh token 的映射。
        """
        oauth = self._config.oauth
        if oauth is None or not oauth.refresh_token:
            return None
        with self._api_lock:
            if self._api is not None:
                return self._api
            client = OAuthClient(
                self._config.site_api_base,
                oauth.client_id,
                redirect_uri=oauth.redirect_uri,
                client_secret=oauth.client_secret or None,
            )
            tokens = client.refresh(oauth.refresh_token)
            self._api = ElementSkinAPI(self._config.site_api_base, token=tokens)
            return self._api
