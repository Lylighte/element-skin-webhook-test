#!/usr/bin/env python3
"""创建两个测试应用（A: PlayerWall, B: Webhook Probe）并完成审核激活。

流程：
1. 管理员登录获取 cookie；
2. POST /v2/oauth/apps 创建应用 A（public，profile.read.owned/texture.read.owned/oauth_grant.read.owned，
   endpoint 订阅 profile.*/texture.*/oauth_grant.revoked）；
3. POST /v2/oauth/apps 创建应用 B（confidential，permission.read.any/account.read.any/
   official_whitelist.read.any/oauth_grant.read.owned，endpoint 订阅 permission.updated/account.*/
   official_whitelist.*/oauth_grant.*）；
4. 对每个应用：POST /v2/oauth/apps/{id}/review-submission 提交审核；
5. 管理员 PATCH /v2/admin/oauth/apps/{id}/review {status:"active"} 激活；
6. 保存 client_id / client_secret / signing_secret 到 scripts/state/apps.json
   （signing_secret 只显示一次）。

用法：
    python create_apps.py --base https://skin.your-domain.com/skinapi \
        --admin-email admin@example.com --admin-password '...' \
        --hooks-base https://hooks.your-domain.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from site_client import SiteClient, load_json, save_json

STATE_DIR = Path(__file__).parent / "state"
STATE_FILE = STATE_DIR / "apps.json"

APP_A = {
    "name": "PlayerWall 皮肤墙",
    "description": "社区官网玩家皮肤墙：角色与皮肤实时展示，撤销授权即下架",
    "client_type": "public",
    "redirect_uri": "https://union-test.gpa.ac.cn/wh/callback",
    "permissions": ["profile.read.owned", "texture.read.owned", "oauth_grant.read.owned"],
}

APP_B = {
    "name": "Webhook Probe",
    "description": "Webhook 测试探针：覆盖管理型事件与定向投递边界",
    "client_type": "confidential",
    "redirect_uri": "",
    "permissions": [
        "permission.read.any",
        "account.read.any",
        "official_whitelist.read.any",
        "oauth_grant.read.owned",
    ],
}


def build_app_body(app: dict, hooks_base: str, endpoint_path: str, events: list[str]) -> dict:
    return {
        **app,
        "webhook_endpoints": [
            {
                "url": f"{hooks_base}{endpoint_path}",
                "enabled": True,
                "events": events,
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="创建并激活测试应用")
    parser.add_argument("--base", required=True, help="站点 API base URL")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--hooks-base", required=True, help="接收端公网 base URL（如 https://union-test.gpa.ac.cn）")
    parser.add_argument("--route-prefix", default="/wh", help="接收端路由前缀（默认 /wh，独立域名可传空）")
    args = parser.parse_args()

    # 接收端统一挂在 route_prefix 下（与 element-skin 共用域名时）
    hooks_base = args.hooks_base.rstrip("/") + args.route_prefix.rstrip("/")

    with SiteClient(args.base) as client:
        client.login(args.admin_email, args.admin_password)

        # 应用 A：PlayerWall
        body_a = build_app_body(
            APP_A,
            hooks_base,
            "/webhooks/element-skin",
            ["profile.created", "profile.updated", "profile.deleted",
             "texture.created", "texture.updated", "texture.deleted",
             "oauth_grant.revoked"],
        )
        app_a = client.create_app(body_a)
        client.submit_review(app_a["client_id"])
        client.admin_review_app(app_a["client_id"], "active")

        # 应用 B：Webhook Probe
        body_b = build_app_body(
            APP_B,
            hooks_base,
            "/webhooks/probe",
            ["permission.updated", "account.created", "account.deleted",
             "official_whitelist.added", "official_whitelist.removed",
             "oauth_grant.created", "oauth_grant.updated", "oauth_grant.revoked"],
        )
        app_b = client.create_app(body_b)
        client.submit_review(app_b["client_id"])
        client.admin_review_app(app_b["client_id"], "active")

    # 提取一次性密钥
    def extract(app: dict, redirect_uri: str) -> dict:
        endpoint = app.get("webhook_endpoints", [{}])[0]
        return {
            "client_id": app["client_id"],
            "client_secret": app.get("client_secret", ""),
            "signing_secret": endpoint.get("signing_secret", ""),
            "endpoint_id": endpoint.get("id", ""),
            "redirect_uri": redirect_uri,
        }

    state = {
        "base": args.base,
        "hooks_base": hooks_base,
        "app_a": extract(app_a, APP_A["redirect_uri"]),
        "app_b": extract(app_b, APP_B["redirect_uri"]),
    }
    save_json(str(STATE_FILE), state)
    print(f"应用已创建并激活，状态保存到 {STATE_FILE}")
    print(f"  A: client_id={state['app_a']['client_id']}")
    print(f"  B: client_id={state['app_b']['client_id']}")
    print("注意：signing_secret 只显示一次，已保存到 state/apps.json，请勿提交到版本库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
