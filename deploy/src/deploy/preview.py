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
* **ffmpeg 挂了要自愈**：**两种"挂了"都要接住**——
  ① 进程退出（RTSP 断流、相机重启、鉴权被拒）：stdout 收到 EOF，监督循环按退避重启；
  ② **进程还活着但再也不出帧**：靠 `FIRST_FRAME_GRACE_S`/`STALL_TIMEOUT_S` 的看门狗
  主动换掉它。
  只做 ① 是不够的，这是 2026-09-29 现场验证过的：`/healthz` 显示最后一帧是 09-29 03:09，
  之后 **5 天零帧**，而 `restarts` 始终是 **0**——ffmpeg 既不写 stdout 也不退出，
  `read_frames` 永远等不到 EOF，恢复逻辑一次都没跑。`docker logs` 里它留的最后一句是
  `[vf#0:0] More than 100000 frames duplicated`。
  ⚠️ **那句是 WARNING，不是死因**（一次性容器已实测：`-loglevel warning` 下照常打完
  3000 帧、`EXIT=0`）。它只说明 `-r` 与源帧率错位、在复制帧多花带宽——是 §14/ADR「补记二」
  记过的那个探针，**别把它当崩溃原因**。真正的死因在这条日志之外（读 RTSP 的 socket
  停住、而 `-loglevel warning` 下没有任何一行错误）；能盖住这一类"静默卡死"的只有看门狗。
  ⚠️ 顺带钉住一条：**别试图用 `-fps_mode passthrough` 去消掉那些重复帧**——它与 `-r`
  同时出现会被 ffmpeg 7.1 直接拒绝（`One of -r/-fpsmax was specified together a non-CFR
  -vsync/-fps_mode. This is contradictory.`），输出文件打不开、预览彻底没有画面。
  最近一次错误与重启次数都放进 `/healthz`，页面据此显示"画面断开"。
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
#: 2026-09-17 现场实测后从 640×5 提到 1280×15，理由见下面这段——**别按直觉把它改小**：
#:
#: * **640 会被放大上屏**。页面里 `#pv` 的容器是 `aspect-ratio:16/9; max-height:44vh`，
#:   1080p 下高度被压到 475px，`object-fit:contain` 反推显示宽度 ≈844px ⇒ 640 宽的帧
#:   要放大 1.3 倍才落到屏幕上。"糊"里最大的一块就是这里，而不是相机。
#: * **帧率要对齐源，不是往大写**。相机 RTSP（`Channels/101`）声明 `r_frame_rate=15/1`，
#:   真解码 10 秒流时间得 151 帧 ⇒ **15 fps**（子码流 102 同为 2880×1616、150 帧）。
#:   现场口口相传的 25 fps 是 DVR 侧的编码设置，我们改不动；写 `-r 25` 只会**复制帧**，
#:   观感一样而带宽涨到 1.7 倍。
#:   ⚠️ **这条要定期复测**：2026-09-21 再量，源流已变成 **12 fps**（`ffprobe` 声明 +
#:   真解码 15 s = 180 帧、两次一致）——DVR 侧被人改过。于是 `-r 15` 变成"把 12 补成 15"，
#:   **1/4 的输出帧是重复帧，而重复帧照样花整帧带宽**（MJPEG 无帧间预测），
#:   上游日志 `More than N frames duplicated` 即此。`.env` 里改成 `PREVIEW_FPS=12` 可省掉。
#:   ⚠️ **2026-09-22 中午复测（PTS 口径，七路信号）：源流又回到 15 fps** ⇒ `-r 15` 已对齐、
#:   零重复帧（生产连跑 67 min 无 `More than N frames duplicated`）。**别再照着上面 09-21 的
#:   旧记录改成 12**——那是丢 3/15 真实帧的降帧，不是对齐。DVR 帧率会变（09-17=15 → 09-21=12
#:   → 09-22=15），**动 `PREVIEW_FPS` 前必须按 PTS 口径复测**：一次性容器跑
#:   `timeout 15 ffmpeg ... -f null -`，看退出行 `frame=/time=` 相除（墙钟数帧会被 RTSP 建连
#:   吞掉的 2~3 s 系统性低估；`grep 'Parsed_showinfo'` 数帧还会把 config 行和每帧的
#:   `color_range:` 行都数进去、帧数翻倍）。完整证据链见 ADR-0017「补记二」。
#: * **分辨率是线性成本**。MJPEG 无帧间压缩，带宽 = 帧大小 × 帧率，实测量级：
#:   640×5@q7 ≈ 0.66 Mbps、1280×12@q7 ≈ 6.0 Mbps、1280×15@q7 ≈ 7.5 Mbps、
#:   **2880（相机原生）@15fps ≈ 32 Mbps**。
#:   ⚠️ **别再往上调**：2026-09-21 试过 `PREVIEW_SCALE=2880`——服务器侧完全够
#:   （生产机本机订阅实测 31.9 Mbps / 15.2 fps、CPU 0.33 核），但**现场 VPN 的持续吞吐
#:   只有 ≈9.8 Mbps** ⇒ 每 3 帧丢 2 帧、画面退化成 4.7 fps，已回退。判据是**持续**吞吐，
#:   不是短文件下载的突发值：同一张 977 KB 图 0.25 s 传完（≈31 Mbps 突发），
#:   持续拉 10~15 s 只剩 9.8 Mbps，同日还量到 0.65 Mbps 的低谷。
#:   要靠原生画质必须换 H.264/HLS（同画质 2~5 Mbps），但那会破坏 ADR-0017 的
#:   "`<img>` 直接播、零 JS 播放器"。
#: * `PREVIEW_SCALE` / `PREVIEW_FPS` 都能从 `.env` 直接调；`q:v` **不能**（见下）。
DEFAULT_SCALE_WIDTH = 1280
DEFAULT_FPS = 15
#: ffmpeg 的 `-q:v`（2 最好 31 最差）。1280 宽下约 60 KB/帧 ≈ 7.5 Mbps @15fps。
#: ⚠️ 它是**硬编码在取命令行里**的，`.env` 改不动它——想调必须改这里并重建镜像。
DEFAULT_QUALITY = 7
RESTART_BACKOFF_S = (1.0, 2.0, 5.0, 10.0)

# ---------- 看门狗：ffmpeg 活着但不出帧，也得有人管 ----------
#
# 2026-09-29 现场：ffmpeg 最后一帧之后既不写 stdout、也不退出，`restarts` 停在 0，
# `/healthz` 一直 503，预览黑到 10-04 有人接管时才发现（5 天 6 小时）。
# 监督循环唯一的恢复触发条件是 stdout EOF（= 进程退出），"进程还在但哑了"完全在它之外。
# 这三个常量就是补那个洞的：**按"有没有新帧"判生死**，而不是按"进程还在不在"。
#: 宽限：刚拉起 ffmpeg 时要连相机 + 出第一帧，给足时间（否则每次重启都自己把自己杀了）。
FIRST_FRAME_GRACE_S = 25.0
#: 稳态下超过这么久没有**新的一帧**就换进程。15 fps 下一帧是 67 ms，10 s ≈ 150 帧的
#: 宽裕量：既能盖住 DVR 的偶发卡顿，又不会让画面停在旧帧上让人以为还活着。
STALL_TIMEOUT_S = 10.0
#: 看门狗的轮询间隔。它只读几个计数器，不必精确。
WATCHDOG_POLL_S = 1.0

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
            # 看门狗与读流**并行**：它是"ffmpeg 活着但不出帧"这条路上唯一的救援。
            # baseline 取此刻的帧数，这样重启后不会拿**上一路**留下的旧帧误判成"还在出帧"。
            watchdog_task = asyncio.create_task(self._watchdog(self.proc, self.broadcaster.frames))
            try:
                assert self.proc.stdout is not None
                await read_frames(self.proc.stdout, self.broadcaster.publish)
            finally:
                for task in (stderr_task, watchdog_task):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                await self._stop_proc(self.proc)
            if self._stop.is_set():
                break
            self.restarts += 1
            delay = self.backoff[min(max(self.restarts - 1, 0), len(self.backoff) - 1)]
            self.log(f"转码退出（第 {self.restarts} 次），{delay:.0f} 秒后重试"
                     f"{'：' + self.last_error if self.last_error else ''}")
            await self._sleep(delay)

    async def _stop_proc(self, proc: asyncio.subprocess.Process) -> None:
        """把一个 ffmpeg 收掉：先礼后兵，5 秒不放手就杀。"""
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:           # pragma: no cover - 不常见
                proc.kill()

    async def _watchdog(self, proc: asyncio.subprocess.Process, baseline: int) -> None:
        """盯住"**这一路** ffmpeg 还有没有在出帧"，停了就把它换掉。

        为什么不能只等 stdout EOF：ffmpeg 还活着却再也不写字节，是真的会发生的——读 RTSP
        的 socket 停住（对端不再发包、TCP 又没断）是这一类里最典型的一种。那时
        `read_frames` 挂着不返回，监督循环唯一的触发条件（进程退出）一次都不发生。
        现场 2026-09-29 就是这样黑了 5 天多，而 `/healthz` 里的 `restarts` 始终是 0——
        "ffmpeg 挂了要自愈"这句话当时是假的。

        ⚠️ 判据用**帧数有没有在涨**，不用 `last_frame_at`：重启之后旧帧还挂在 broadcaster 上，
        拿帧龄判会把刚拉起来、还没出第一帧的新进程立刻误杀。

        两条时限：这一路**一帧都没出过**给 `FIRST_FRAME_GRACE_S`（连相机 + 首帧），
        出过帧之后超过 `STALL_TIMEOUT_S` 没有新帧就换人。
        """
        frames = baseline
        changed_at = time.monotonic()
        while not self._stop.is_set():
            await asyncio.sleep(WATCHDOG_POLL_S)
            now_frames = self.broadcaster.frames
            if now_frames != frames:
                frames, changed_at = now_frames, time.monotonic()
                continue
            limit = STALL_TIMEOUT_S if now_frames > baseline else FIRST_FRAME_GRACE_S
            waited = time.monotonic() - changed_at
            if waited > limit:
                self.last_error = (f"画面停滞 {waited:.0f} 秒"
                                   f"（ffmpeg 进程还在、但没有新帧：{frames} 帧后就没有了）")
                self.log(f"! {self.last_error}，换掉这个 ffmpeg")
                await self._stop_proc(proc)
                return

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
