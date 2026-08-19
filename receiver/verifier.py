"""封装 python-sdk WebhookVerifier，按 endpoint 分密钥。

- 从 config 读取各 endpoint 的 signing_secret
- 使用原始 body 验签（不能先解析再序列化）
- tolerance_seconds=300（5 分钟时钟偏差）、max_body_bytes=65536
- 验签失败抛出的细分异常映射为 HTTP 400
"""
from __future__ import annotations

from element_skin_sdk import WebhookVerifier

from .config import Config, EndpointConfig


class VerifierRegistry:
    """按 endpoint 维护独立的 WebhookVerifier。"""

    def __init__(self, config: Config) -> None:
        self._verifiers: dict[str, WebhookVerifier] = {}
        for endpoint in config.endpoints:
            self._verifiers[endpoint.name] = WebhookVerifier(
                endpoint.signing_secret,
                tolerance_seconds=300,
                max_body_bytes=65_536,
            )

    def get(self, endpoint: EndpointConfig) -> WebhookVerifier:
        return self._verifiers[endpoint.name]
