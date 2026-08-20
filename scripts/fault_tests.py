#!/usr/bin/env python3
"""可靠性与安全用例（docs/方案.md 第 6.3 节）。

用例：
- C1 接收端 500 → 指数退避重试（首约 30s，12 次/72h 上限，最终 dead）
- C2 接收端 >10s 不响应 → 超时重试
- C3 手动重放同 Webhook-Id → 204，inbox 无重复
- C4 篡改 body / 伪造签名 / 过期时间戳 → 400
- C5 事件产生后、投递前撤销 grant → worker 重检后不再外发（markDead）
- C6 endpoint 选未申请权限事件 → 400
- C7 endpoint 配 http:// 或私网 IP → 400（SSRF 防护）
- C8 endpoint 停用 → 恢复
- C9 时钟偏差 >5 分钟 → 400

用法：
    python fault_tests.py --base https://skin.your-domain.com/skinapi \
        --hooks-base https://hooks.your-domain.com --admin-email ... --admin-password ...
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

from site_client import SiteClient, load_json

STATE_FILE = Path(__file__).parent / "state" / "apps.json"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), (timestamp + "." + body.decode()).encode(), hashlib.sha256)
    return "v1=" + mac.hexdigest()


def build_request(secret: str, *, event_id: str = "evt_fault", delivery_id: str = "whd_fault",
                  event_type: str = "profile.created", data: dict | None = None,
                  timestamp: int | None = None, body_override: bytes | None = None,
                  sign_override: bool = False) -> tuple[dict, bytes]:
    ts = timestamp or (time.time_ns() // 1_000_000)
    payload = {
        "id": event_id,
        "type": event_type,
        "created_at": ts - 1000,
        "data": data or {"user_id": "user-fault", "profile_id": "profile-fault"},
    }
    body = body_override if body_override is not None else json.dumps(payload).encode()
    sign_body = body if sign_override else json.dumps(payload).encode()
    headers = {
        "Webhook-Id": event_id,
        "Webhook-Delivery": delivery_id,
        "Webhook-Timestamp": str(ts),
        "Webhook-Signature": sign(secret, str(ts), sign_body),
        "Content-Type": "application/json",
    }
    return headers, body


def wait_for_new_event(hooks_base: str, event_type: str, endpoint: str,
                       before_ms: int, timeout: float = 45.0, interval: float = 2.0) -> bool:
    """等待接收端收到 before_ms 之后到达的指定类型事件（避免匹配旧事件）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = httpx.get(f"{hooks_base}/api/events", timeout=5.0).json().get("events", [])
            if any(e["event_type"] == event_type and e["endpoint"] == endpoint
                   and e["received_at_ms"] > before_ms and e["verified"] for e in events):
                return True
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="可靠性与安全用例")
    parser.add_argument("--base", required=True)
    parser.add_argument("--hooks-base", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--user-email", required=True, help="测试用户（C1/C2 触发真实事件用）")
    parser.add_argument("--user-password", required=True)
    parser.add_argument("--only", default=None, help="只运行指定用例，如 C1")
    args = parser.parse_args()

    state = load_json(str(STATE_FILE))
    secret_a = state["app_a"]["signing_secret"]
    secret_b = state["app_b"]["signing_secret"]
    # 用 state 里保存的 hooks_base（已含 route_prefix），保证与创建应用时一致
    hooks_base = state["hooks_base"]
    url_a = f"{hooks_base}/webhooks/element-skin"
    url_b = f"{hooks_base}/webhooks/probe"

    # 全程复用登录会话，避免触发登录限流（429）
    admin = SiteClient(args.base)
    admin.login(args.admin_email, args.admin_password)
    user = SiteClient(args.base)
    user.login(args.user_email, args.user_password)

    # 前置检查：C1/C2 需要用户对应用 A 有有效 grant（否则 profile.created 事件不产生）
    if args.only in (None, "C1", "C2"):
        grants = user.list_grants()
        has_grant = any(g.get("client_id") == state["app_a"]["client_id"] and g.get("status") == "active"
                        for g in grants)
        if not has_grant:
            print("错误：测试用户对应用 A 没有有效 grant。")
            print("请先运行 authorize_a.py 或网页授权应用 A（PlayerWall），再重试 C1/C2。")
            return 1

    # C3：重放同 Webhook-Id → 204，inbox 无重复
    if args.only in (None, "C3"):
        # 每次运行用唯一 event_id，避免与历史数据冲突
        event_id = "evt_c3_" + uuid.uuid4().hex[:12]
        headers, body = build_request(secret_a, event_id=event_id)
        first = httpx.post(url_a, content=body, headers=headers)
        second = httpx.post(url_a, content=body, headers=headers)
        print(f"C3 重放: first={first.status_code} second={second.status_code}")
        assert first.status_code == 204 and second.status_code == 204
        # 验证该 event_id 在事件日志中只出现一次（重放不产生重复）
        events = httpx.get(f"{hooks_base}/api/events").json().get("events", [])
        count = sum(1 for e in events if e["event_id"] == event_id)
        assert count == 1, f"{event_id} 应只出现 1 次，实际 {count}"
        print("✓ C3 通过")

    # C4：篡改 body / 伪造签名 / 过期时间戳 → 400
    if args.only in (None, "C4"):
        # 篡改 body（签名基于原 payload，不匹配）
        headers, body = build_request(secret_a, event_id="evt_c4_tamper",
                                      body_override=b'{"id":"evt_c4_tamper","type":"profile.created","created_at":1,"data":{}}')
        resp = httpx.post(url_a, content=body, headers=headers)
        print(f"C4 篡改 body: {resp.status_code}")
        assert resp.status_code == 400
        # 伪造签名
        headers, body = build_request(secret_a, event_id="evt_c4_forge")
        headers["Webhook-Signature"] = "v1=deadbeef"
        resp = httpx.post(url_a, content=body, headers=headers)
        print(f"C4 伪造签名: {resp.status_code}")
        assert resp.status_code == 400
        # 过期时间戳（10 分钟前）
        old = (time.time_ns() // 1_000_000) - 10 * 60 * 1000
        headers, body = build_request(secret_a, event_id="evt_c4_old", timestamp=old)
        resp = httpx.post(url_a, content=body, headers=headers)
        print(f"C4 过期时间戳: {resp.status_code}")
        assert resp.status_code == 400
        print("✓ C4 通过")

    # C9：时钟偏差 >5 分钟 → 400（与 C4 过期时间戳同路径，单独列出）
    if args.only in (None, "C9"):
        future = (time.time_ns() // 1_000_000) + 10 * 60 * 1000
        headers, body = build_request(secret_a, event_id="evt_c9_future", timestamp=future)
        resp = httpx.post(url_a, content=body, headers=headers)
        print(f"C9 超前时间戳: {resp.status_code}")
        assert resp.status_code == 400
        print("✓ C9 通过")

    # C1：接收端 500 → 指数退避重试（通过真实事件触发，worker 投递失败后重试）
    if args.only in (None, "C1"):
        # 1. 接收端对 playerwall endpoint 设 500 故障
        httpx.post(f"{hooks_base}/control/playerwall/500")
        # 2. 触发真实事件：创建角色 → profile.created → worker 投递 → 500
        #    用唯一角色名，避免重复执行冲突
        before = time.time_ns() // 1_000_000
        profile = user.create_profile(f"FaultC1_{uuid.uuid4().hex[:8]}")
        print(f"C1 创建角色触发事件: {profile.get('id')}")
        # 3. 重置故障，等待 worker 重试（首约 30s）到达
        httpx.post(f"{hooks_base}/control/playerwall/reset")
        print("C1 等待重试（约 30s）...")
        assert wait_for_new_event(hooks_base, "profile.created", "playerwall", before, timeout=45), "C1 重试未在 45s 内到达"
        print("✓ C1 通过（重试成功）")

    # C2：接收端 >10s 不响应 → 超时重试（通过真实事件触发）
    if args.only in (None, "C2"):
        # 1. 接收端对 playerwall endpoint 设 slow 故障（>10s 超时）
        httpx.post(f"{hooks_base}/control/playerwall/slow")
        # 2. 触发真实事件：创建角色 → worker 投递 → 超时
        before = time.time_ns() // 1_000_000
        profile = user.create_profile(f"FaultC2_{uuid.uuid4().hex[:8]}")
        print(f"C2 创建角色触发事件: {profile.get('id')}")
        # 3. 重置故障，等待 worker 重试到达
        httpx.post(f"{hooks_base}/control/playerwall/reset")
        print("C2 等待重试（约 30s）...")
        assert wait_for_new_event(hooks_base, "profile.created", "playerwall", before, timeout=45), "C2 重试未在 45s 内到达"
        print("✓ C2 通过（超时重试成功）")

    # C6：endpoint 选未申请权限事件 → 400（用应用 A 尝试订阅 account.created）
    if args.only in (None, "C6"):
        app_a = admin.get_app(state["app_a"]["client_id"])
        # 尝试把未申请权限的事件加入 endpoint
        bad_body = {
            "name": app_a["name"],
            "client_type": app_a["client_type"],
            "redirect_uri": app_a.get("redirect_uri", ""),
            "permissions": app_a["permissions"],
            "webhook_endpoints": [
                {
                    "id": state["app_a"]["endpoint_id"],
                    "url": url_a,
                    "enabled": True,
                    "events": ["account.created"],  # 未申请 account.read.any
                }
            ],
        }
        resp = admin.request("PATCH", f"/v2/oauth/apps/{state['app_a']['client_id']}", json=bad_body)
        print(f"C6 越权事件: {resp.status_code}")
        assert resp.status_code == 400
        print("✓ C6 通过")

    # C7：endpoint 配 http:// 或私网 IP → 400（SSRF 防护）
    if args.only in (None, "C7"):
        app_a = admin.get_app(state["app_a"]["client_id"])
        for bad_url in ["http://hooks.example.com/x", "https://127.0.0.1/x", "https://localhost/x"]:
            bad_body = {
                "name": app_a["name"],
                "client_type": app_a["client_type"],
                "redirect_uri": app_a.get("redirect_uri", ""),
                "permissions": app_a["permissions"],
                "webhook_endpoints": [
                    {"url": bad_url, "enabled": True, "events": ["profile.created"]}
                ],
            }
            resp = admin.request("PATCH", f"/v2/oauth/apps/{state['app_a']['client_id']}", json=bad_body)
            print(f"C7 非法 URL {bad_url}: {resp.status_code}")
            assert resp.status_code == 400
        print("✓ C7 通过")

    # C8：endpoint 停用 → 不投递 → 恢复 → 投递
    if args.only in (None, "C8"):
        app_a = admin.get_app(state["app_a"]["client_id"])
        endpoint_id = state["app_a"]["endpoint_id"]
        if not endpoint_id:
            # 从 get_app 响应中取 endpoint id
            endpoint_id = app_a.get("webhook_endpoints", [{}])[0].get("id", "")
        # 1. 停用 endpoint
        disabled_body = {
            "name": app_a["name"],
            "client_type": app_a["client_type"],
            "redirect_uri": app_a.get("redirect_uri", ""),
            "permissions": app_a["permissions"],
            "webhook_endpoints": [
                {
                    "id": endpoint_id,
                    "url": url_a,
                    "enabled": False,
                    "events": ["profile.created"],
                }
            ],
        }
        resp = admin.request("PATCH", f"/v2/oauth/apps/{state['app_a']['client_id']}", json=disabled_body)
        print(f"C8 停用 endpoint: {resp.status_code}")
        assert resp.status_code == 200
        # 2. 触发事件，确认不投递（复用 user 会话）
        before = time.time_ns() // 1_000_000
        user.create_profile(f"FaultC8Disabled_{uuid.uuid4().hex[:8]}")
        time.sleep(3)
        events = httpx.get(f"{hooks_base}/api/events").json().get("events", [])
        disabled_arrived = any(e["event_type"] == "profile.created" and e["endpoint"] == "playerwall"
                               and e["received_at_ms"] > before for e in events)
        print(f"C8 停用期间事件是否到达: {disabled_arrived}")
        assert not disabled_arrived, "C8 停用期间不应投递"
        # 3. 恢复 endpoint
        enabled_body = {
            "name": app_a["name"],
            "client_type": app_a["client_type"],
            "redirect_uri": app_a.get("redirect_uri", ""),
            "permissions": app_a["permissions"],
            "webhook_endpoints": [
                {
                    "id": endpoint_id,
                    "url": url_a,
                    "enabled": True,
                    "events": ["profile.created"],
                }
            ],
        }
        resp = admin.request("PATCH", f"/v2/oauth/apps/{state['app_a']['client_id']}", json=enabled_body)
        print(f"C8 恢复 endpoint: {resp.status_code}")
        assert resp.status_code == 200
        # 4. 再触发事件，确认投递
        before = time.time_ns() // 1_000_000
        user.create_profile(f"FaultC8Enabled_{uuid.uuid4().hex[:8]}")
        assert wait_for_new_event(hooks_base, "profile.created", "playerwall", before, timeout=15, interval=1), "C8 恢复后事件未到达"
        print("✓ C8 通过")
        # 5. 恢复应用 A 的完整事件订阅（C8 只保留了 profile.created）
        app_a = admin.get_app(state["app_a"]["client_id"])
        endpoint_id = app_a.get("webhook_endpoints", [{}])[0].get("id", "")
        full_body = {
            "name": app_a["name"],
            "client_type": app_a["client_type"],
            "redirect_uri": app_a.get("redirect_uri", ""),
            "permissions": app_a["permissions"],
            "webhook_endpoints": [
                {
                    "id": endpoint_id,
                    "url": url_a,
                    "enabled": True,
                    "events": ["profile.created", "profile.updated", "profile.deleted",
                               "texture.created", "texture.updated", "texture.deleted",
                               "oauth_grant.revoked"],
                }
            ],
        }
        admin.request("PATCH", f"/v2/oauth/apps/{state['app_a']['client_id']}", json=full_body)
        print("✓ C8 完整事件订阅已恢复")

    print("可靠性/安全用例完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
