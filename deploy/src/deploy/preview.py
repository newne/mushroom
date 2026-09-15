"""实时预览服务（ADR-0017）：把相机的 RTSP 拉成 MJPEG 广播给 console。

现场为什么需要它：手动接管时看不见画面，点动/定位只能靠坐标猜；而采图服务单次抓图 ≈5.2 s，
"边挪边看"用它没有意义。相机自己有 RTSP（实测 554 可用、凭据就是 `stations.yaml` 里那份、
并且允许 ≥3 路并发），所以这里只做一件很薄的事：

    ffmpeg 拉 RTSP → 输出 MJPEG 字节流 → 按 JPEG 边界切帧 → 广播给所有观看者

## 几条边界

* **不碰控制器、不碰厂商 SDK**：这一路与采图服务（XCloudSDK，8000 端口）是两条独立会话，
  互不干扰；预览也**不留存任何图片**（照片只有「抓拍」会落索引与 MinIO）。
* **一次转码、多人观看**：只有一个 ffmpeg 进程；观看者各自排队，慢客户端**丢帧**而不是
  把所有人拖住（队满即丢最旧的一帧）。
* **ffmpeg 挂了要自愈**：RTSP 断流/相机重启都会让它退出，监督循环按退避重启，并把
  最近一次错误与重启次数放进 `/healthz`，页面据此显示"画面断开"。
* **口令不外泄**：RTSP URL 里有相机口令，日志里一律打码。

跑法（容器里的 `preview` 角色）：``python3 -m deploy.preview --rtsp rtsp://… --port 8090``
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_PORT = 8090
DEFAULT_SCALE_WIDTH = 640      # 够看清"对着哪儿"；再大只是白烧 CPU 与带宽
DEFAULT_FPS = 5
DEFAULT_QUALITY = 7            # ffmpeg 的 -q:v（2 最好 31 最差）；7 在 640 宽下约 30–60 KB/帧
RESTART_BACKOFF_S = (1.0, 2.0, 5.0, 10.0)
SOI = b"\xff\xd8\xff"          # JPEG 起始
EOI = b"\xff\xd9"              # JPEG 结束

#: 每个观看者的待发队列长度：满了就丢最旧的一帧（宁可掉帧，也不让一个人拖住全场）
QUEUE_MAX = 3


def redact(url: str) -> str:
    """把文本里 ``scheme://user:pwd@host`` 的口令打码——日志与页面里绝不出现相机口令。

    作用对象是**任意文本**，不只是一条干净的 URL：ffmpeg 出错时会把输入地址原样写进
    stderr（例如 `rtsp://admin:pw@…: Server returned 401`），而 `last_error` 会经
    `/api/preview/status` **进到浏览器**。2026-09-15 上机时这里只处理"整串就是一个 URL"
    的情形，夹在句子里的口令会漏出去。
    """
    return _CREDS_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:***@", url)


#: `scheme://user:pwd@`（口令里可能有别的字符，但不含 `/`、空白与 `@`）
_CREDS_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]*)@")


def build_ffmpeg_cmd(rtsp_url: str, *, ffmpeg: str = "ffmpeg", scale: int = DEFAULT_SCALE_WIDTH,
                     fps: int = DEFAULT_FPS, quality: int = DEFAULT_QUALITY) -> list[str]:
    """构造转码命令：拉 RTSP、缩放、按固定帧率输出 MJPEG 到 stdout。

    参数都是"够用就好"的取舍：`-rtsp_transport tcp`（这台 DVR 的 UDP 不稳）、
    `-an`（不要音频）、`-f image2pipe -c:v mjpeg -`（把 JPEG 帧连续写到 stdout）。
    """
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-an",
        "-vf", f"scale={scale}:-2",
        "-r", str(fps),
        "-q:v", str(quality),
        "-f", "image2pipe", "-c:v", "mjpeg", "-",
    ]


def split_jpeg_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """从字节流里切出完整的 JPEG 帧，返回 `(帧列表, 剩余未完成字节)`。

    ffmpeg 的 `image2pipe` 就是**连续拼接的 JPEG**，所以按 SOI/EOI 切即可——
    比引入任何容器解析都简单，且对每个 MJPEG 帧都成立。
    """
    frames: list[bytes] = []
    pos = 0
    while True:
        start = buffer.find(SOI, pos)
        if start < 0:
            # 起点之前没有完整帧的字节一律丢掉：`pos` 已经越过上一帧的 EOI，
            # 所以这里剩下的只可能是噪声（真正的半截帧走下面那条分支留着）。
            return frames, buffer[pos:]
        end = buffer.find(EOI, start + len(SOI))
        if end < 0:
            return frames, buffer[start:]      # 半截帧：留着等下一批，别丢（丢了画面会闪）
        frames.append(buffer[start:end + len(EOI)])
        pos = end + len(EOI)


def mjpeg_part(frame: bytes) -> bytes:
    """把一帧包成 ``multipart/x-mixed-replace`` 的一段。

    抽成纯函数是为了可测：`<img>` 能不能播，全看这里的边界与长度写对没有。
    """
    return (b"--frame\r\nContent-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")


@dataclass
class Broadcaster:
    """把帧发给所有观看者；不保存历史（只留最后一帧给"迟到的"客户端立刻看到画面）。"""

    queue_max: int = QUEUE_MAX
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    frames: int = 0
    last_frame: bytes | None = None
    last_frame_at: float | None = None
    started_at: float = field(default_factory=time.monotonic)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.queue_max)
        self.subscribers.add(q)
        if self.last_frame is not None:          # 新观众立刻看到当前画面，而不是等下一帧
            q.put_nowait(self.last_frame)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, frame: bytes) -> None:
        self.frames += 1
        self.last_frame = frame
        self.last_frame_at = time.monotonic()
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()               # 丢最旧的一帧
                except asyncio.QueueEmpty:       # pragma: no cover - 竞态窗口
                    pass
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:            # pragma: no cover - 同上
                pass

    @property
    def viewers(self) -> int:
        return len(self.subscribers)

    def status(self, *, now: float | None = None) -> dict:
        """给 `/healthz` 与 console 的状态：画面是否新鲜（超过 3 秒没有新帧就算断）。"""
        now = time.monotonic() if now is None else now
        age = None if self.last_frame_at is None else round(now - self.last_frame_at, 2)
        return {
            "ok": bool(self.last_frame is not None and (age or 0) < 3.0),
            "frames": self.frames,
            "viewers": self.viewers,
            "last_frame_age_s": age,
            "uptime_s": round(now - self.started_at, 1),
        }


async def read_frames(stream: asyncio.StreamReader,
                      sink: Callable[[bytes], None]) -> None:
    """从 ffmpeg 的 stdout 连续读字节、切帧、交给 sink。读不到就返回（由监督循环重启）。"""
    buffer = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        frames, buffer = split_jpeg_frames(buffer + chunk)
        for frame in frames:
            sink(frame)


class PreviewSupervisor:
    """管住那一个 ffmpeg 进程：起、读、退出后按退避重启，并记录最近一次错误。"""

    def __init__(self, cmd: list[str], broadcaster: Broadcaster,
                 *, log: Callable[[str], None] = print,
                 backoff: Iterable[float] = RESTART_BACKOFF_S,
                 sleep: Callable[[float], object] = asyncio.sleep) -> None:
        self.cmd = cmd
        self.broadcaster = broadcaster
        self.log = log
        self.backoff = list(backoff)
        self._sleep = sleep
        self.restarts = 0
        self.last_error: str | None = None
        self._stop = asyncio.Event()
        self.proc: asyncio.subprocess.Process | None = None

    async def run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            # 打码后打**完整**命令：现场排障时"实际用的 scale/fps/transport"就在这一行里。
            # （2026-09-15 上机第一版只打了末尾 6 个参数，日志成了"7 -f image2pipe …"，
            # 看着像打码把命令吃掉了，实际上什么信息都没有。）
            self.log(f"启动转码：{redact(' '.join(self.cmd))}")
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *self.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            except OSError as e:                  # ffmpeg 不在（镜像没装）——明确报出来
                self.last_error = f"无法启动 ffmpeg: {e}"
                self.log(f"! {self.last_error}")
                await self._sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                attempt += 1
                continue

            stderr_task = asyncio.create_task(self._drain_stderr(self.proc))
            try:
                assert self.proc.stdout is not None
                await read_frames(self.proc.stdout, self.broadcaster.publish)
            finally:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task
                if self.proc.returncode is None:
                    self.proc.terminate()
                    try:
                        await asyncio.wait_for(self.proc.wait(), timeout=5)
                    except TimeoutError:           # pragma: no cover - 不常见
                        self.proc.kill()
            if self._stop.is_set():
                break
            self.restarts += 1
            delay = self.backoff[min(max(self.restarts - 1, 0), len(self.backoff) - 1)]
            self.log(f"转码退出（第 {self.restarts} 次），{delay:.0f} 秒后重试"
                     f"{'：' + self.last_error if self.last_error else ''}")
            await self._sleep(delay)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if text:
                # ffmpeg 会把输入地址（含口令）原样写进 stderr，而 last_error 会经
                # /api/preview/status 进浏览器：这一行必须打码（ADR-0017 §5）。
                text = redact(text)
                self.last_error = text[:300]
                self.log(f"ffmpeg: {text[:300]}")

    def stop(self) -> None:
        self._stop.set()


def create_app(*, broadcaster: Broadcaster, supervisor: PreviewSupervisor | None = None):
    """HTTP 面：`/healthz`（画面新不新鲜）、`/stream.mjpg`（广播流）、`/frame.jpg`（单帧）。"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    app = FastAPI(title="mushroom-patrol-preview")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        body = broadcaster.status()
        if supervisor is not None:
            body.update({"restarts": supervisor.restarts, "last_error": supervisor.last_error})
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    @app.get("/frame.jpg")
    def frame() -> Response:
        """最新一帧（离线排障与测试用；页面走流）。"""
        if broadcaster.last_frame is None:
            return Response(status_code=503, content=b"no frame yet")
        return Response(content=broadcaster.last_frame, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/stream.mjpg")
    async def stream() -> StreamingResponse:
        queue = broadcaster.subscribe()

        async def gen() -> AsyncIterator[bytes]:
            try:
                while True:
                    frame = await queue.get()
                    yield mjpeg_part(frame)
            finally:
                broadcaster.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    return app


def main(argv: list[str] | None = None) -> int:
    """CLI 入口（容器里的 `preview` 角色）。"""
    import argparse

    ap = argparse.ArgumentParser(prog="deploy.preview", description="相机实时预览（RTSP → MJPEG）")
    ap.add_argument("--rtsp", default=os.environ.get("PREVIEW_RTSP", ""),
                    help="RTSP 地址（含凭据）。不给就从 --stations 里第一条站位拼")
    ap.add_argument("--stations", default=os.environ.get("PATROL_STATIONS",
                                                         "/app/configs/stations.yaml"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PREVIEW_PORT", DEFAULT_PORT)))
    ap.add_argument("--scale", type=int,
                    default=int(os.environ.get("PREVIEW_SCALE", DEFAULT_SCALE_WIDTH)))
    ap.add_argument("--fps", type=int, default=int(os.environ.get("PREVIEW_FPS", DEFAULT_FPS)))
    ap.add_argument("--ffmpeg", default=os.environ.get("PREVIEW_FFMPEG", "ffmpeg"))
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

    rtsp = args.rtsp or rtsp_from_stations(args.stations, log=log)
    if not rtsp:
        log("! 拿不到 RTSP 地址：给 --rtsp，或让 --stations 里至少有一个站位带 camera_ip")
        return 2

    broadcaster = Broadcaster()
    supervisor = PreviewSupervisor(
        build_ffmpeg_cmd(rtsp, ffmpeg=args.ffmpeg, scale=args.scale, fps=args.fps),
        broadcaster, log=log,
    )
    app = create_app(broadcaster=broadcaster, supervisor=supervisor)

    async def serve() -> None:
        import uvicorn

        config = uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="warning")
        server = uvicorn.Server(config)

        def _stop(*_a):                       # 收到信号：先停转码，再停 HTTP
            supervisor.stop()
            server.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except ValueError:                # pragma: no cover - 非主线程
                pass
        log(f"预览服务就绪：{redact(rtsp)} → http://0.0.0.0:{args.port}/stream.mjpg"
            f"（{args.scale} 宽 @ {args.fps} fps）")
        await asyncio.gather(supervisor.run_forever(), server.serve())

    asyncio.run(serve())
    return 0


def rtsp_from_stations(path: str, *, log: Callable[[str], None] = print,
                       channel: str = "Streaming/Channels/101") -> str:
    """从站位表第一条站位拼 RTSP 地址——**凭据只有一个来源**（与抓拍共用同一份）。

    这台 DVR 实测不挑路径（13 条常见路径都给同一路流），所以默认用最通用的那条。
    """
    from patrol.stations import load_stations  # 局部导入：本模块其余部分不依赖 patrol

    try:
        stations = load_stations(path)
    except Exception as e:                    # noqa: BLE001 - 读不到就明确说，不猜
        log(f"! 读站位表失败（{path}）：{e}")
        return ""
    if not stations:
        return ""
    st = stations[0]
    cred = f"{st.camera_user}:{st.camera_pwd}@" if st.camera_user else ""
    return f"rtsp://{cred}{st.camera_ip}:554/{channel}"


if __name__ == "__main__":      # pragma: no cover
    import sys

    sys.exit(main())
