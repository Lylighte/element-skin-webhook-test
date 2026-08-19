"""接收端自测：验签、重放、篡改、业务规则。

运行：cd receiver && .venv/Scripts/python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from receiver.app import create_app
from receiver.config import Config, EndpointConfig, OAuthConfig
from receiver.tests.helpers import build_event

SECRET_A = "secret-playerwall"
SECRET_B = "secret-probe"


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.1) -> bool:
    """轮询等待条件成立（worker 后台处理）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        config = Config(
            site_api_base="http://127.0.0.1:8000",
            db_path=db_path,
            route_prefix="/wh",
            endpoints=[
                EndpointConfig(name="playerwall", path="/webhooks/element-skin", signing_secret=SECRET_A),
                EndpointConfig(name="probe", path="/webhooks/probe", signing_secret=SECRET_B),
            ],
            oauth=OAuthConfig(client_id="app-a", redirect_uri="http://localhost/callback"),
        )
        app = create_app(config)
        with TestClient(app) as test_client:
            yield test_client


# ---------- 验签与结构 ----------

def test_valid_event_returns_204(client):
    headers, body = build_event(SECRET_A, event_type="profile.created")
    resp = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert resp.status_code == 204


def test_wrong_secret_returns_400(client):
    headers, body = build_event("wrong-secret")
    resp = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert resp.status_code == 400


def test_tampered_body_returns_400(client):
    headers, body = build_event(SECRET_A, body_override=b'{"id":"evt_test_001","type":"profile.created","created_at":1,"data":{}}')
    resp = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert resp.status_code == 400


def test_expired_timestamp_returns_400(client):
    old = (time.time_ns() // 1_000_000) - 10 * 60 * 1000  # 10 分钟前，超过 5 分钟容差
    headers, body = build_event(SECRET_A, timestamp=old)
    resp = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert resp.status_code == 400


def test_missing_headers_returns_400(client):
    headers, body = build_event(SECRET_A)
    del headers["Webhook-Signature"]
    resp = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert resp.status_code == 400


def test_unknown_endpoint_returns_404(client):
    headers, body = build_event(SECRET_A)
    resp = client.post("/wh/webhooks/unknown", content=body, headers=headers)
    assert resp.status_code == 404


# ---------- 重放与幂等 ----------

def test_replay_same_event_returns_204_and_no_duplicate(client):
    headers, body = build_event(SECRET_A, event_id="evt_replay_1")
    first = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    second = client.post("/wh/webhooks/element-skin", content=body, headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204
    # inbox 中只有一条
    status = client.get("/wh/admin/status").json()
    assert status["inbox"].get("pending", 0) + status["inbox"].get("done", 0) == 1


def test_different_delivery_same_event_id_is_replay(client):
    headers1, body1 = build_event(SECRET_A, event_id="evt_same_id", delivery_id="whd_1")
    headers2, body2 = build_event(SECRET_A, event_id="evt_same_id", delivery_id="whd_2")
    assert client.post("/wh/webhooks/element-skin", content=body1, headers=headers1).status_code == 204
    assert client.post("/wh/webhooks/element-skin", content=body2, headers=headers2).status_code == 204
    status = client.get("/wh/admin/status").json()
    assert status["inbox"].get("pending", 0) + status["inbox"].get("done", 0) == 1


# ---------- 业务规则 ----------

def test_profile_created_creates_card_after_worker(client):
    headers, body = build_event(
        SECRET_A,
        event_id="evt_profile_created",
        event_type="profile.created",
        data={"user_id": "user-1", "profile_id": "profile-1"},
    )
    assert client.post("/wh/webhooks/element-skin", content=body, headers=headers).status_code == 204
    # worker 无 API 客户端（无 refresh token），卡片不会创建，但玩家记录应存在
    assert wait_for(lambda: client.get("/wh/admin/status").json()["playerwall"]["active_players"] == 1)


def test_grant_revoked_removes_player(client):
    # 先创建玩家
    headers, body = build_event(
        SECRET_A,
        event_id="evt_grant_revoke",
        event_type="oauth_grant.revoked",
        data={"user_id": "user-1", "grant_id": "grant-1"},
    )
    assert client.post("/wh/webhooks/element-skin", content=body, headers=headers).status_code == 204
    status = client.get("/wh/admin/status").json()
    assert status["playerwall"]["active_players"] == 0


def test_probe_records_event(client):
    headers, body = build_event(
        SECRET_B,
        event_id="evt_permission_updated",
        event_type="permission.updated",
        data={"user_id": "user-2"},
    )
    assert client.post("/wh/webhooks/probe", content=body, headers=headers).status_code == 204
    assert wait_for(
        lambda: client.get("/wh/admin/status").json()["probe"]["events_by_type"].get("permission.updated") == 1
    )


# ---------- 故障注入 ----------

def test_fault_500_returns_500(client):
    client.post("/wh/control/probe/500")
    headers, body = build_event(SECRET_B, event_id="evt_fault_500")
    resp = client.post("/wh/webhooks/probe", content=body, headers=headers)
    assert resp.status_code == 500
    client.post("/wh/control/probe/reset")
    resp = client.post("/wh/webhooks/probe", content=body, headers=headers)
    assert resp.status_code == 204


# ---------- 公开 API ----------

def test_public_players_empty(client):
    resp = client.get("/wh/api/players")
    assert resp.status_code == 200
    assert resp.json()["players"] == []


def test_public_player_not_found(client):
    resp = client.get("/wh/api/players/nonexistent")
    assert resp.status_code == 404
