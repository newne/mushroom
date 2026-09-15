"""部署侧的真实 HTTP 传输（``patrol.links.Transport`` 形状）。

**为什么不在 patrol 包里**：patrol 有一条安全基线——库内零网络调用（见 ``patrol.links``
与 ``patrol.capture_client`` 的 docstring）。真实 HTTP 只由部署侧注入，这让 patrol 可以
脱离网络被单测、审计与静态扫描。

本模块同时是 **SSRF 的收口处**：目标白名单由调用方显式给出，URL 的 ``host:port`` 不在
名单内直接拒绝。patrol 侧的 URL 全是字面量常量（同机采图服务、prod 分析服务），白名单
把"它是字面量"这个口头约定变成运行时可验证的约束。
"""

from __future__ import annotations

from typing import Self
from urllib.parse import urlsplit

import httpx
from patrol.links import TransportError

# 采图服务实测单次 ``capture_time_ms = 5200 ms``（含等待关键帧、抓图、落盘就绪），
# 池化首次还要建连。超时给足——宁可慢，也不要在**设计内耗时**上误判成链路故障。
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_CONNECT_TIMEOUT_S = 10.0


def host_port(url: str) -> str:
    """从 URL 提取 ``host:port``（缺端口时按协议补默认值）。"""
    parts = urlsplit(url)
    if not parts.hostname:
        raise TransportError(f"URL 缺少主机名: {url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname}:{port}"


def split_host_port(value: str, *, default_port: int | None = None) -> tuple[str, int]:
    """把 ``host:port``（也接受 URL）拆成 ``(host, port)``。

    与 `host_port()` 的区别：那个收的是 URL，这个还接受裸的 ``host:port`` —— compose 里
    ``PATROL_CAPTURE_HOST=172.17.0.1:7003`` 就是这种形状。采图客户端的地址要用它来填，
    否则客户端会退回默认的 ``127.0.0.1:7003``，被传输层白名单拦下（2026-09-15 上机实测：
    `目标不在白名单内: 127.0.0.1:7003（允许: ['172.17.0.1:7003']）`）。
    """
    text = host_port(value) if "://" in value else value
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        if default_port is None:
            raise TransportError(f"需要 host:port 形状（或 URL），收到 {value!r}")
        return text, default_port
    return host, int(port)


class HttpxTransport:
    """``patrol.links.Transport`` 的 httpx 实现，带目标白名单与统一超时。

    调用约定与 patrol 侧两个调用方严格对齐：

    - ``CaptureClient`` 传 ``params``（查询串，未带 body）→ 走 GET；
    - ``SyncClient`` 传 ``body``（待同步行）→ 走 POST JSON。

    只接受 JSON 响应（调用方 ``CaptureClient._interpret`` 期望 dict）。非 2xx、连接
    失败、JSON 解析失败一律折算为 ``TransportError``（**可重试类**），于是
    ``patrol.links.RetryingTransport`` 的重试语义对上游保持一致——本类自身不重试。
    """

    def __init__(
        self,
        *,
        allowed_hosts: object,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
        accept_json_errors: tuple[str, ...] = (),
    ) -> None:
        self.allowed_hosts = {str(h).lower() for h in allowed_hosts}  # type: ignore[union-attr]
        self.timeout_s = timeout_s
        self.accept_json_errors = tuple(accept_json_errors)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=DEFAULT_CONNECT_TIMEOUT_S),
            follow_redirects=False,  # 重定向可能绕过白名单
        )

    def __call__(
        self,
        url: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        target = host_port(url).lower()
        if target not in self.allowed_hosts:
            raise TransportError(
                f"目标不在白名单内: {target}（允许: {sorted(self.allowed_hosts)}）"
            )
        try:
            if body is None:
                resp = self._client.get(url, params=params)
            else:
                resp = self._client.post(url, params=params, json=body)
        except httpx.HTTPError as e:
            raise TransportError(f"{target} 请求失败: {e}") from e

        if not 200 <= resp.status_code < 300:
            # 有的服务（采图 :7003）用非 2xx 表示**业务失败**，body 里仍是完整信封
            # （`{"success": false, "error_code": ..., "message": ...}`）。把它当链路故障
            # 会把"相机这次没拍成"错判成"服务挂了"，还会丢掉重试所需的语义。调用方
            # 给出信封键名（如 ``("success",)``）即表示：带这些键的响应解析后返回，
            # 交由业务层判定；其余非 2xx 仍按链路故障抛错（同步端点就该这样重试）。
            payload = self._json_or_none(resp)
            if (
                self.accept_json_errors
                and isinstance(payload, dict)
                and all(key in payload for key in self.accept_json_errors)
            ):
                return payload
            raise TransportError(f"{target} 返回 {resp.status_code}: {resp.text[:200]}")

        payload = self._json_or_none(resp)
        if payload is None:
            raise TransportError(f"{target} 响应不是 JSON: {resp.text[:200]}")
        return payload

    @staticmethod
    def _json_or_none(resp: httpx.Response) -> object | None:
        try:
            return resp.json()
        except ValueError:
            return None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
