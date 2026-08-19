"""FastAPI Webhook 接收端主应用。

两个 endpoint：
- /webhooks/element-skin → 应用 A（PlayerWall）
- /webhooks/probe       → 应用 B（Webhook Probe）

流程：验签（python-sdk WebhookVerifier）→ 原子 claim（SQLite ReplayGuard）→ 2xx 立即返回
→ 后台 worker 幂等处理业务。
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from element_skin_sdk import WebhookError, WebhookReplayError
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import Config, load_config
from .db import Database
from .playerwall import PlayerWall
from .probe import Probe
from .replay_guard import SqliteReplayGuard
from .verifier import VerifierRegistry
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class Receiver:
    """组装配置、数据库、验签、重放防护与业务模块。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.replay_guard = SqliteReplayGuard(config.db_path)
        self.verifiers = VerifierRegistry(config)
        self.playerwall = PlayerWall(self.db)
        self.probe = Probe(self.db)
        self.worker = Worker(config, self.db, self.playerwall, self.probe)

    def start(self) -> None:
        self.worker.start()

    def stop(self) -> None:
        self.worker.stop()
        self.replay_guard.close()
        self.db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    receiver = app.state.receiver
    receiver.start()
    yield
    receiver.stop()


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    receiver = Receiver(config)
    app = FastAPI(title="Element Skin Webhook Receiver", lifespan=lifespan)
    app.state.receiver = receiver
    router = APIRouter(prefix=config.route_prefix)

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @router.post("/webhooks/{endpoint_path:path}")
    async def receive_webhook(endpoint_path: str, request: Request) -> Response:
        receiver = request.app.state.receiver
        endpoint = receiver.config.endpoint_by_path("/webhooks/" + endpoint_path)
        if endpoint is None:
            return JSONResponse(status_code=404, content={"error": "unknown endpoint"})

        # 故障注入（仅测试用）
        fault = receiver.probe.apply_fault(endpoint.name)
        if fault is not None:
            status_code, delay = fault
            if delay > 0:
                time.sleep(delay)
            return Response(status_code=status_code)

        raw_body = await request.body()
        received_at_ms = time.time_ns() // 1_000_000
        try:
            verifier = receiver.verifiers.get(endpoint)
            event = verifier.verify_and_claim(
                raw_body,
                request.headers,
                receiver.replay_guard,
            )
        except WebhookReplayError:
            # 重放：已处理过，返回 204
            return Response(status_code=204)
        except WebhookError as error:
            receiver.db.log_event(
                event_id="",
                delivery_id="",
                endpoint=endpoint.name,
                event_type="",
                created_at_ms=0,
                received_at_ms=received_at_ms,
                data={},
                verified=False,
                error=str(error),
            )
            return Response(status_code=400, content=str(error))

        # 已认证事件：写入 inbox（幂等），立即返回 2xx
        receiver.db.enqueue(
            event_id=event.id,
            delivery_id=event.delivery_id,
            endpoint=endpoint.name,
            event_type=event.type,
            created_at_ms=event.created_at,
            received_at_ms=received_at_ms,
            data=event.data,
        )
        receiver.db.log_event(
            event_id=event.id,
            delivery_id=event.delivery_id,
            endpoint=endpoint.name,
            event_type=event.type,
            created_at_ms=event.created_at,
            received_at_ms=received_at_ms,
            data=event.data,
            verified=True,
        )
        return Response(status_code=204)

    # ---------- 面板与公开 API ----------

    @router.get("/admin/status")
    def admin_status() -> dict:
        return {
            "inbox": receiver.db.inbox_stats(),
            "events_by_type": receiver.db.events_by_type(),
            "playerwall": receiver.playerwall.status(),
            "probe": receiver.probe.status(),
            "replay_keys": receiver.replay_guard.count(),
        }

    @router.get("/api/events")
    def api_events(limit: int = 50) -> dict:
        return {"events": receiver.db.recent_events(limit)}

    @router.get("/api/players")
    def api_players() -> dict:
        return {"players": receiver.playerwall.public_players()}

    @router.get("/api/players/{profile_id}")
    def api_player(profile_id: str) -> dict:
        player = receiver.playerwall.public_player(profile_id)
        if player is None:
            return JSONResponse(status_code=404, content={"error": "not found"})
        return {"player": player}

    @router.post("/control/{endpoint_name}/{mode}")
    def control(endpoint_name: str, mode: str) -> dict:
        if mode == "reset":
            receiver.probe.reset_fault(endpoint_name)
        elif mode in ("500", "slow"):
            receiver.probe.set_fault(endpoint_name, mode)
        else:
            return JSONResponse(status_code=400, content={"error": "invalid mode"})
        return {"endpoint": endpoint_name, "mode": mode}

    app.include_router(router)
    return app


app = create_app()
