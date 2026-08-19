"""测试辅助：构造带签名的 webhook 请求。

与 element-skin worker 的签名算法一致：
v1=hex(HMAC-SHA256(signing_secret, timestamp + "." + raw_body))
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


def sign(signing_secret: str, timestamp: str, raw_body: bytes) -> str:
    mac = hmac.new(
        signing_secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    )
    return "v1=" + mac.hexdigest()


def build_event(
    signing_secret: str,
    *,
    event_id: str = "evt_test_001",
    delivery_id: str = "whd_test_001",
    event_type: str = "profile.created",
    created_at: int | None = None,
    data: dict[str, Any] | None = None,
    timestamp: int | None = None,
    body_override: bytes | None = None,
    sign_override_body: bool = False,
) -> tuple[dict[str, str], bytes]:
    """构造 (headers, raw_body)。

    - body_override：替换请求体（用于模拟篡改）。默认签名仍基于原始 payload 计算，
      使签名与篡改后的 body 不匹配（验签失败场景）。
    - sign_override_body=True：签名基于 body_override 计算（用于模拟合法但内容不同的请求）。
    """
    created_at = created_at or (time.time_ns() // 1_000_000 - 1000)
    timestamp = timestamp or (time.time_ns() // 1_000_000)
    payload = {
        "id": event_id,
        "type": event_type,
        "created_at": created_at,
        "data": data or {"user_id": "user-1", "profile_id": "profile-1"},
    }
    raw_body = body_override if body_override is not None else json.dumps(payload).encode("utf-8")
    sign_body = raw_body if sign_override_body else json.dumps(payload).encode("utf-8")
    headers = {
        "Webhook-Id": event_id,
        "Webhook-Delivery": delivery_id,
        "Webhook-Timestamp": str(timestamp),
        "Webhook-Signature": sign(signing_secret, str(timestamp), sign_body),
        "Content-Type": "application/json",
    }
    return headers, raw_body
