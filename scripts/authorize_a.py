#!/usr/bin/env python3
"""应用 A（PlayerWall）授权码 + PKCE 全流程。

流程：
1. 用户登录（SiteClient 拿 cookie）；
2. 用 SDK OAuthClient 生成授权 URL（含 code_challenge）；
3. 通过 SDK 的 authorization_info / approve_authorization 完成授权确认
   （需要已登录用户的 access token，这里用 SiteClient 的 cookie 会话）；
4. 交换 code → access + refresh token；
5. 保存 refresh token 到 scripts/state/apps.json（供 drive_events.py / perf.py 回查 /v2 用）。

用法：
    python authorize_a.py --base https://skin.your-domain.com/skinapi \
        --user-email user@example.com --user-password '...'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from element_skin_sdk import OAuthClient
from element_skin_sdk.permissions import OAuthScopes, ProfileScopes, TextureScopes

from site_client import SiteClient, load_json, save_json

STATE_FILE = Path(__file__).parent / "state" / "apps.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="应用 A 授权码 + PKCE 流程")
    parser.add_argument("--base", required=True)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--user-password", required=True)
    args = parser.parse_args()

    state = load_json(str(STATE_FILE))
    app_a = state["app_a"]
    client_id = app_a["client_id"]
    redirect_uri = "http://localhost:8080/callback"

    # 1. 用户登录
    with SiteClient(args.base) as site:
        site.login(args.user_email, args.user_password)

        # 2. 生成授权 URL
        oauth = OAuthClient(args.base, client_id, redirect_uri=redirect_uri)
        session = oauth.authorization_url(
            [ProfileScopes.READ_OWNED, TextureScopes.READ_OWNED, OAuthScopes.GRANT_READ_OWNED],
            state="playerwall-test",
        )

        # 3. 授权确认（SDK 需要已登录用户的 access token；这里用 cookie 会话直接调授权接口）
        request_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(session.scopes),
            "state": session.state,
            "code_challenge": session.code_challenge,
            "code_challenge_method": "S256",
        }
        info = oauth.authorization_info(request_params)
        print("授权信息:", info)
        decision = oauth.approve_authorization(request_params)
        print("授权确认:", decision)

        # 4. 从授权确认响应中提取 code（redirect 到 redirect_uri 的 code 参数）
        code = None
        if isinstance(decision, dict):
            code = decision.get("code")
        if not code:
            print("错误：未能从授权确认响应中获取 code")
            return 1

        # 5. 交换 token
        tokens = oauth.exchange_code(code=code, code_verifier=session.code_verifier)
        print("token 获取成功，refresh_token 已保存")

    # 6. 保存 refresh token
    app_a["refresh_token"] = tokens.refresh_token
    state["app_a"] = app_a
    save_json(str(STATE_FILE), state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
