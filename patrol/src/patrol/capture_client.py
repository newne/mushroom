"""截图服务（capture/ 目录，本机 7003 端口）的 HTTP 客户端（票 02，评审 #1 重塑）。

架构约束：截图服务与 patrol **同机部署**、端口固定 7003（spec §2 架构图）。
**地址是可配置的**（`host`/`port`），但只由部署侧注入、且传输层的白名单是最终把关——
本库既不读环境变量也不做任意目标请求。

> 2026-09-15 上机教训：这里原先把 URL 写成**字面量常量** `127.0.0.1:7003`，于是容器里
> （采图服务发布在宿主上，要走网桥网关 `172.17.0.1:7003`）每一条采图都被传输层白名单拦下：
> `采图链路失败: 目标不在白名单内: 127.0.0.1:7003（允许: ['172.17.0.1:7003']）`。
> "地址不可配"看起来更安全，实际只是把配置错位变成了运行期失败。
"""

from __future__ import annotations

from dataclasses import dataclass

from patrol.links import TransportError

CAPTURE_HOST = "127.0.0.1"     # 默认：裸机部署（服务与巡检同机）
CAPTURE_PORT = 7003
CAPTURE_PATH = "/pool_capture"


class CaptureError(RuntimeError):
    """采图失败（服务不可达/返回失败），不属于登录或设备离线。"""


class LoginError(CaptureError):
    """相机登录失败（用户名/密码错误等）。"""


class DeviceOfflineError(CaptureError):
    """相机设备不在线。"""


@dataclass(frozen=True)
class CaptureResult:
    object_name: str
    file_path: str | None
    cloud_url: str | None
    raw: dict


class CaptureClient:
    def __init__(self, *, transport=None, host: str | None = None, port: int | None = None):
        # transport：patrol.links.Transport 形状（send(url, *, params, body) -> dict）
        self._transport = transport  # 部署侧注入真实 HTTP
        self.host = host or CAPTURE_HOST
        self.port = int(port or CAPTURE_PORT)

    @property
    def url(self) -> str:
        """采图服务地址。部署侧给的 host/port 就是最终地址（白名单再拦一道）。"""
        return f"http://{self.host}:{self.port}{CAPTURE_PATH}"

    def capture(self, *, ip: str, user: str = "admin", pwd: str = "",
                filename: str, storage: str = "cloud") -> CaptureResult:
        params = {"ip": ip, "user": user, "storage": storage, "filename": filename}
        if pwd:
            params["pwd"] = pwd
        return self._interpret(self._request(params), filename)

    def _request(self, params: dict) -> dict:
        if self._transport is not None:
            try:
                return self._transport(self.url, params=params)
            except TransportError as e:
                # 链路错误**必须**落进 CaptureError 体系：orchestrator 靠它做站位级重试，
                # `PatrolRound` 靠它"跳过该站位"而不是中止整轮。放它原样穿透，等于让一次
                # 网络抖动把整轮 60 站位巡检打断（实测踩过：池连接失效 → 500 → 整轮 exit 1）。
                raise CaptureError(f"采图链路失败: {e}") from e
        raise NotImplementedError(
            "未注入截图服务 transport：库内不内置网络调用（仓库安全基线），"
            "由部署侧提供 links.Transport 形状的 HTTP 实现（见 links 模块 docstring）"
        )

    @staticmethod
    def _interpret(payload: dict, filename: str) -> CaptureResult:
        if payload.get("success"):
            return CaptureResult(
                object_name=payload.get("filename") or filename,
                file_path=payload.get("file_path"),
                cloud_url=payload.get("cloud_url"),
                raw=payload,
            )
        message = str(payload.get("message", ""))
        detail = str(payload.get("detail", ""))
        code = payload.get("error_code")
        if "login" in message.lower():
            if "不在线" in detail:
                raise DeviceOfflineError(f"相机不在线: {detail} (code={code})")
            raise LoginError(f"相机登录失败: {detail or message} (code={code})")
        raise CaptureError(f"采图失败: {message} (code={code})")
