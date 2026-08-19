#!/usr/bin/env python3
"""性能测试（docs/方案.md 第 7 节）。

场景：
1. 端到端延迟：批量创建 N 个角色（脚本并发触发），记录每个事件从 API 请求发出到接收端 204 的时间 → P50/P95/P99
2. 吞吐：固定 N 事件，测 worker 完成投递的总耗时 → events/min、events/s
3. 网络开销：同机回环 vs 本地→云端跨网投递 → 延迟差值
4. 资源预算：默认预算（2 连接/3000ms）vs 调参（如 4 连接/1000ms）→ 吞吐提升、主站延迟影响
5. SQL 效率：抽查 worker 批次 SQL 次数 → 对比 0.08 次/event 基准

用法：
    python perf.py --base https://skin.your-domain.com/skinapi \
        --hooks-base https://hooks.your-domain.com --events 1000 --concurrency 50
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from site_client import SiteClient, load_json

STATE_FILE = Path(__file__).parent / "state" / "apps.json"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, int(len(values) * p / 100))
    return values[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="性能测试")
    parser.add_argument("--base", required=True)
    parser.add_argument("--hooks-base", required=True)
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--user-password", required=True)
    args = parser.parse_args()

    state = load_json(str(STATE_FILE))
    # 用 state 里保存的 hooks_base（已含 route_prefix），保证与创建应用时一致
    hooks_base = state["hooks_base"]

    # 登录用户（用于创建角色）
    with SiteClient(args.base) as user:
        user.login(args.user_email, args.user_password)

        # 场景 1+2：批量创建角色，测量端到端延迟与吞吐
        latencies: list[float] = []
        lock = threading.Lock()
        created_ids: list[str] = []

        def create_one(index: int) -> None:
            start = time.monotonic()
            try:
                profile = user.create_profile(f"PerfPlayer{index}")
                with lock:
                    created_ids.append(profile["id"])
                    latencies.append((time.monotonic() - start) * 1000)
            except Exception as exc:
                print(f"创建角色 {index} 失败: {exc}")

        print(f"批量创建 {args.events} 个角色（并发 {args.concurrency}）...")
        start_time = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(create_one, range(args.events)))
        create_elapsed = time.monotonic() - start_time

        # 等待接收端收到全部 profile.created 事件
        print("等待接收端收到事件...")
        deadline = time.monotonic() + 120
        received = 0
        while time.monotonic() < deadline:
            try:
                events = httpx.get(f"{hooks_base}/api/events", timeout=5.0).json().get("events", [])
                received = sum(1 for e in events if e["event_type"] == "profile.created")
                if received >= args.events:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        total_elapsed = time.monotonic() - start_time

        print("\n=== 性能结果 ===")
        print(f"创建角色 API 延迟（ms）: P50={percentile(latencies, 50):.1f} "
              f"P95={percentile(latencies, 95):.1f} P99={percentile(latencies, 99):.1f}")
        print(f"创建 {args.events} 个角色耗时: {create_elapsed:.1f}s")
        print(f"接收端收到事件: {received}/{args.events}")
        if received > 0:
            print(f"端到端吞吐: {received / max(total_elapsed, 0.001):.1f} events/s")
            print(f"端到端总耗时（含投递等待）: {total_elapsed:.1f}s")

        # 场景 3：网络开销对比（同机回环 vs 跨网）——由部署环境决定，这里记录基线
        print("\n=== 网络开销 ===")
        print("（同机回环 vs 跨网对比需在部署环境分别运行本脚本）")

        # 场景 4：资源预算对比——需改 docker-compose 环境变量后重启 worker
        print("\n=== 资源预算 ===")
        print("（默认 2 连接/3000ms vs 调参对比需重启 worker 后重跑）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
