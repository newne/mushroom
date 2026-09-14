"""跨进程链路的统一 seam（架构评审 #1）。

安全基线：库内零网络调用——真实 HTTP 由部署侧注入**唯一形状**的 Transport
adapter。统一前：三个调用方各持一种 transport 形状（params->dict /
rows->None / url->json）；统一后：

    transport(url, *, params=None, body=None) -> dict     （可调用对象）

seam 的位置刻意选为"可调用形状"而非本模块的类型——patrol（库房主机）与
analysis（prod 服务器）是两个部署单元，任何一方 import 对方都会破坏部署边界；
两侧只共享这个调用约定，各自在 docstring 里引用它。本模块为 patrol 侧提供
类型与组合适配器。
"""

from __future__ import annotations

import time
from typing import Protocol


class Transport(Protocol):
    """部署侧实现本协议（如 httpx 包装 + 目标白名单校验）。返回解析后的 JSON dict。"""

    def __call__(self, url: str, *, params: dict | None = None,
                 body: dict | None = None) -> dict: ...


class TransportError(RuntimeError):
    """链路失败（不可达/超时/非 2xx）——可重试类错误。"""


class RetryingTransport:
    """组合适配器：对内层 transport 加重试与退避。

    仅对幂等端点使用（prod 端 /ingest 以 INSERT OR REPLACE 保证幂等，
    见 ADR-0001）。非 TransportError（如编程错误）不重试，直接抛出。
    """

    def __init__(self, inner, *, attempts: int = 3, backoff_s: float = 1.0,
                 sleep=time.sleep):
        self._inner = inner
        self.attempts = attempts
        self.backoff_s = backoff_s
        self._sleep = sleep

    def __call__(self, url: str, *, params: dict | None = None,
                 body: dict | None = None) -> dict:
        last: TransportError | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return self._inner(url, params=params, body=body)
            except TransportError as e:
                last = e
                if attempt < self.attempts:
                    self._sleep(self.backoff_s)
        raise last  # type: ignore[misc]
