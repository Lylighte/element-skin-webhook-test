#!/usr/bin/env python3
"""共享 API 客户端：登录、注册、应用管理、事件触发。

封装 element-skin /v2 API 的常用操作，供各测试脚本复用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx


class SiteClient:
    """element-skin 站点 API 客户端（基于 cookie 会话）。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SiteClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ---------- 认证 ----------

    def login(self, email: str, password: str) -> dict[str, Any]:
        resp = self._client.post("/v2/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        return resp.json()

    def register(self, email: str, password: str, username: str, invite: str = "") -> dict[str, Any]:
        resp = self._client.post(
            "/v2/auth/register",
            json={"email": email, "password": password, "username": username, "invite": invite},
        )
        resp.raise_for_status()
        return resp.json()

    # ---------- 应用管理 ----------

    def create_app(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/v2/oauth/apps", json=body)
        resp.raise_for_status()
        return resp.json()

    def get_app(self, client_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/v2/oauth/apps/{client_id}")
        resp.raise_for_status()
        return resp.json()

    def update_app(self, client_id: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(f"/v2/oauth/apps/{client_id}", json=body)
        resp.raise_for_status()
        return resp.json()

    def submit_review(self, client_id: str) -> dict[str, Any]:
        resp = self._client.post(f"/v2/oauth/apps/{client_id}/review-submission")
        resp.raise_for_status()
        return resp.json()

    def admin_review_app(self, client_id: str, status: str, reason: str = "") -> dict[str, Any]:
        resp = self._client.patch(
            f"/v2/admin/oauth/apps/{client_id}/review",
            json={"status": status, "reason": reason},
        )
        resp.raise_for_status()
        return resp.json()

    def list_grants(self) -> dict[str, Any]:
        resp = self._client.get("/v2/oauth/grants")
        resp.raise_for_status()
        return resp.json()

    def revoke_grant(self, grant_id: str) -> None:
        resp = self._client.delete(f"/v2/oauth/grants/{grant_id}")
        resp.raise_for_status()

    # ---------- 用户资源 ----------

    def create_profile(self, name: str, model: str = "default") -> dict[str, Any]:
        resp = self._client.post("/v2/users/me/profiles", json={"name": name, "model": model})
        resp.raise_for_status()
        return resp.json()

    def update_profile(self, profile_id: str, **fields: Any) -> None:
        resp = self._client.patch(f"/v2/users/me/profiles/{profile_id}", json=fields)
        resp.raise_for_status()

    def delete_profile(self, profile_id: str) -> None:
        resp = self._client.delete(f"/v2/users/me/profiles/{profile_id}")
        resp.raise_for_status()

    def list_profiles(self) -> dict[str, Any]:
        resp = self._client.get("/v2/users/me/profiles")
        resp.raise_for_status()
        return resp.json()

    def upload_texture(self, file_path: str, texture_type: str = "skin") -> dict[str, Any]:
        with open(file_path, "rb") as handle:
            resp = self._client.post(
                "/v2/users/me/textures",
                files={"file": (Path(file_path).name, handle, "image/png")},
                data={"texture_type": texture_type},
            )
        resp.raise_for_status()
        return resp.json()

    def delete_texture(self, texture_hash: str, texture_type: str) -> None:
        resp = self._client.delete(f"/v2/users/me/textures/{texture_hash}/{texture_type}")
        resp.raise_for_status()

    # ---------- 管理操作 ----------

    def set_user_permission(self, user_id: str, permission_code: str) -> None:
        resp = self._client.put(f"/v2/admin/users/{user_id}/permissions/{permission_code}")
        resp.raise_for_status()

    def clear_user_permission(self, user_id: str, permission_code: str) -> None:
        resp = self._client.delete(f"/v2/admin/users/{user_id}/permissions/{permission_code}")
        resp.raise_for_status()

    def add_official_whitelist(self, username: str) -> None:
        resp = self._client.post("/v2/admin/official-whitelist", json={"username": username})
        resp.raise_for_status()

    def remove_official_whitelist(self, username: str) -> None:
        resp = self._client.delete(f"/v2/admin/official-whitelist/{username}")
        resp.raise_for_status()

    def delete_user(self, user_id: str) -> None:
        resp = self._client.delete(f"/v2/admin/users/{user_id}")
        resp.raise_for_status()

    # ---------- 通用 ----------

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, path, **kwargs)


def save_json(path: str, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def now_ms() -> int:
    return time.time_ns() // 1_000_000
