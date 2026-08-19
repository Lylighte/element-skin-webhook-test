"""接收端配置加载。

从 config.json 或环境变量读取：
- site_api_base：站点 API base（用于回查 /v2）
- endpoints：各 endpoint 的 path 与 signing_secret
- oauth：应用 A 的 client_id / redirect_uri / refresh token（回查 /v2 用）

不记录 signing_secret 到日志。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EndpointConfig:
    """单个 webhook endpoint 的配置。"""

    name: str
    path: str
    signing_secret: str


@dataclass(frozen=True)
class OAuthConfig:
    """应用 A 回查 /v2 所需的 OAuth 配置。"""

    client_id: str
    redirect_uri: str
    refresh_token: str = ""
    client_secret: str = ""


@dataclass(frozen=True)
class Config:
    site_api_base: str
    endpoints: list[EndpointConfig] = field(default_factory=list)
    oauth: OAuthConfig | None = None
    db_path: str = "receiver.db"
    worker_threads: int = 4
    poll_interval_ms: int = 500
    # 接收端统一路由前缀。与 element-skin 共用域名时，Nginx 把该前缀转发到接收端；
    # 独立域名时可为空字符串 ""。
    route_prefix: str = "/wh"

    def endpoint_by_path(self, path: str) -> EndpointConfig | None:
        for endpoint in self.endpoints:
            if endpoint.path == path:
                return endpoint
        return None

    def endpoint_by_name(self, name: str) -> EndpointConfig | None:
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        return None


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """从 config.json 或环境变量加载配置。

    优先级：显式 path > 环境变量 ELEMENT_SKIN_RECEIVER_CONFIG > 模块目录下 config.json。
    """
    if path is None:
        path = os.environ.get("ELEMENT_SKIN_RECEIVER_CONFIG")
    if path is None:
        # 基于模块位置，避免依赖进程工作目录
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    raw: dict[str, Any] = {}
    if Path(path).exists():
        with open(path, "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)

    site_api_base = (
        os.environ.get("ELEMENT_SKIN_SITE_API_BASE")
        or raw.get("site_api_base")
        or "http://127.0.0.1:8000"
    )

    endpoints: list[EndpointConfig] = []
    for item in raw.get("endpoints", []):
        name = item.get("name")
        path = item.get("path")
        secret = item.get("signing_secret")
        if not name or not path or not secret:
            raise ValueError(f"invalid endpoint config: {item!r}")
        endpoints.append(EndpointConfig(name=name, path=path, signing_secret=secret))

    oauth_raw = raw.get("oauth") or {}
    oauth = None
    if oauth_raw.get("client_id"):
        oauth = OAuthConfig(
            client_id=oauth_raw["client_id"],
            redirect_uri=oauth_raw.get("redirect_uri", ""),
            refresh_token=oauth_raw.get("refresh_token", ""),
            client_secret=oauth_raw.get("client_secret", ""),
        )

    db_path = raw.get("db_path", "receiver.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)

    return Config(
        site_api_base=site_api_base.rstrip("/"),
        endpoints=endpoints,
        oauth=oauth,
        db_path=db_path,
        worker_threads=int(raw.get("worker_threads", 4)),
        poll_interval_ms=int(raw.get("poll_interval_ms", 500)),
        route_prefix=raw.get("route_prefix", "/wh"),
    )
