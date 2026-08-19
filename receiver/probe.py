"""应用 B（Webhook Probe）业务：事件记录 + 故障注入控制。

- 记录所有到达事件，供面板与报告统计
- 故障注入端点（仅测试用）：
  - /control/{endpoint}/500   → 后续请求返回 500（触发重试）
  - /control/{endpoint}/slow  → 后续请求延迟 >10s（触发超时重试）
  - /control/{endpoint}/reset → 恢复正常
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .db import Database

logger = logging.getLogger(__name__)


class Probe:
    """处理应用 B 的 webhook 事件，并支持故障注入。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()
        # 故障注入状态：{endpoint_name: "ok" | "500" | "slow"}
        self._faults: dict[str, str] = {}

    def handle(self, event_id: str, event_type: str, delivery_id: str,
               data: dict[str, Any], received_at_ms: int) -> None:
        self._db.record_probe_event(event_id, event_type, delivery_id, data, received_at_ms)

    # ---------- 故障注入 ----------

    def set_fault(self, endpoint: str, mode: str) -> None:
        with self._lock:
            self._faults[endpoint] = mode
            logger.info("Probe: fault mode %s for endpoint %s", mode, endpoint)

    def reset_fault(self, endpoint: str) -> None:
        with self._lock:
            self._faults.pop(endpoint, None)
            logger.info("Probe: fault reset for endpoint %s", endpoint)

    def fault_mode(self, endpoint: str) -> str:
        with self._lock:
            return self._faults.get(endpoint, "ok")

    def apply_fault(self, endpoint: str) -> tuple[int, float] | None:
        """返回 (status_code, delay_seconds)；None 表示正常。"""
        mode = self.fault_mode(endpoint)
        if mode == "500":
            return 500, 0.0
        if mode == "slow":
            return None, 11.0  # 超过 worker 10s 超时
        return None

    def status(self) -> dict[str, Any]:
        return {
            "faults": dict(self._faults),
            "events_by_type": self._db.probe_stats(),
        }
