"""接收端自测。

实现要点（M1 阶段填充）：
- 用 python-sdk 的签名工具构造合法请求，验证验签通过
- 篡改 body / 伪造签名 / 过期时间戳 → 验证 400
- 重放同 Webhook-Id → 验证 204 且 inbox 无重复
- PlayerWall 业务规则单测（卡片 CRUD、撤销下架、幂等）
"""
from __future__ import annotations

# TODO(M1): 实现接收端自测
