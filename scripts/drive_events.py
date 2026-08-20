#!/usr/bin/env python3
"""触发各类事件，驱动 Webhook 投递。

覆盖事件（对应 API 见 docs/方案.md 第 5 节）：
- account.created        → POST /v2/auth/register
- profile.created/updated/deleted → POST/PATCH/DELETE /v2/users/me/profiles...
- texture.created/updated/deleted → POST/PATCH/DELETE /v2/users/me/textures...
- oauth_grant.revoked    → DELETE /v2/oauth/grants/{grant_id}
- permission.updated     → PUT/DELETE /v2/admin/users/{id}/permissions/{code} 或角色
- official_whitelist.*   → POST/DELETE /v2/admin/official-whitelist

用法：
    python drive_events.py --base https://skin.your-domain.com/skinapi \
        --admin-email ... --admin-password ... --user-email ... --user-password ...
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

from site_client import SiteClient, load_json, now_ms

STATE_FILE = Path(__file__).parent / "state" / "apps.json"


def wait_for_event(hooks_base: str, event_type: str, timeout: float = 15.0) -> bool:
    """轮询接收端 /api/events 确认事件到达。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{hooks_base}/api/events", timeout=5.0)
            if resp.status_code == 200:
                events = resp.json().get("events", [])
                if any(e["event_type"] == event_type and e["verified"] for e in events):
                    return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="触发 Webhook 事件")
    parser.add_argument("--base", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--user-password", required=True)
    parser.add_argument("--only", default=None, help="只运行指定用例")
    args = parser.parse_args()

    state = load_json(str(STATE_FILE))
    hooks_base = state["hooks_base"]

    with SiteClient(args.base) as admin:
        admin.login(args.admin_email, args.admin_password)
        with SiteClient(args.base) as user:
            user.login(args.user_email, args.user_password)

            # 用例 1：profile.created
            if args.only in (None, "profile.created"):
                profile = user.create_profile("WebhookTestPlayer")
                print(f"创建角色: {profile}")
                assert wait_for_event(hooks_base, "profile.created"), "profile.created 未到达"
                print("✓ profile.created 到达")

            # 用例 2：profile.updated
            if args.only in (None, "profile.updated"):
                profile_id = profile["id"]
                user.update_profile(profile_id, name="WebhookTestRenamed")
                print(f"改名角色: {profile_id}")
                assert wait_for_event(hooks_base, "profile.updated"), "profile.updated 未到达"
                print("✓ profile.updated 到达")

            # 用例 3：texture.created（上传皮肤）
            if args.only in (None, "texture.created"):
                # 需要一个 PNG 文件；用最小合法 PNG
                png = Path(__file__).parent / "fixtures" / "test_skin.png"
                if not png.exists():
                    png.parent.mkdir(parents=True, exist_ok=True)
                    png.write_bytes(_minimal_png())
                texture = user.upload_texture(str(png), "skin")
                print(f"上传皮肤: {texture}")
                assert wait_for_event(hooks_base, "texture.created"), "texture.created 未到达"
                print("✓ texture.created 到达")

            # 用例 4：texture.deleted
            if args.only in (None, "texture.deleted"):
                texture_hash = texture["hash"]
                user.delete_texture(texture_hash, "skin")
                print(f"删除皮肤: {texture_hash}")
                assert wait_for_event(hooks_base, "texture.deleted"), "texture.deleted 未到达"
                print("✓ texture.deleted 到达")

            # 用例 5：profile.deleted
            if args.only in (None, "profile.deleted"):
                user.delete_profile(profile_id)
                print(f"删除角色: {profile_id}")
                assert wait_for_event(hooks_base, "profile.deleted"), "profile.deleted 未到达"
                print("✓ profile.deleted 到达")

            # 用例 6：oauth_grant.revoked（撤销用户对应用 A 的授权）
            if args.only in (None, "oauth_grant.revoked"):
                grants = user.list_grants()
                # ListGrants 返回数组（不是 {"grants": [...]}）
                target = next(
                    (g for g in grants if g.get("client_id") == state["app_a"]["client_id"]),
                    None,
                )
                if target:
                    user.revoke_grant(target["id"])
                    print(f"撤销授权: {target['id']}")
                    assert wait_for_event(hooks_base, "oauth_grant.revoked"), "oauth_grant.revoked 未到达"
                    print("✓ oauth_grant.revoked 到达")
                else:
                    print("跳过：未找到应用 A 的 grant（需先运行 authorize_a.py）")

            # 用例 7：permission.updated（管理员改用户权限）
            if args.only in (None, "permission.updated"):
                me = user.request("GET", "/v2/users/me").json()
                user_id = me["user_id"]
                admin.set_user_permission(user_id, "profile.read.any")
                print(f"设置权限覆盖: {user_id}")
                assert wait_for_event(hooks_base, "permission.updated"), "permission.updated 未到达"
                print("✓ permission.updated 到达")
                admin.clear_user_permission(user_id, "profile.read.any")

            # 用例 8：official_whitelist.added/removed
            if args.only in (None, "official_whitelist"):
                admin.add_official_whitelist("WebhookTestPlayer")
                print("添加官方白名单")
                assert wait_for_event(hooks_base, "official_whitelist.added"), "official_whitelist.added 未到达"
                print("✓ official_whitelist.added 到达")
                admin.remove_official_whitelist("WebhookTestPlayer")
                print("移除官方白名单")
                assert wait_for_event(hooks_base, "official_whitelist.removed"), "official_whitelist.removed 未到达"
                print("✓ official_whitelist.removed 到达")

    print("全部用例完成")
    return 0


def _minimal_png() -> bytes:
    """生成 1x1 透明 PNG（最小合法 PNG）。"""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


if __name__ == "__main__":
    sys.exit(main())
