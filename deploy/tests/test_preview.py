"""实时预览服务（`deploy.preview`，ADR-0017）。

这里测的都是"现场会疼"的点：帧切得对不对、慢客户端会不会拖住别人、rtsp 断流后会不会
自愈、以及**相机口令绝不能出现在日志里**。真跑 RTSP 的部分不在这里（那是相机的测试），
用假字节流与假 ffmpeg 覆盖。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from deploy.preview import (
    Broadcaster,
    PreviewSupervisor,
    build_ffmpeg_cmd,
    create_app,
    mjpeg_part,
    redact,
    rtsp_from_stations,
    split_jpeg_frames,
)
from fastapi.testclient import TestClient

JPEG_A = b"\xff\xd8\xff" + b"A" * 20 + b"\xff\xd9"
JPEG_B = b"\xff\xd8\xff" + b"B" * 40 + b"\xff\xd9"


# ---------- 帧切分 ----------


def test_split_jpeg_frames_handles_glued_and_partial_frames():
    """ffmpeg 的 image2pipe 是**连续拼接**的 JPEG：粘包、半截包都要正确处理。"""
    frames, rest = split_jpeg_frames(JPEG_A + JPEG_B)
    assert frames == [JPEG_A, JPEG_B] and rest == b""

    # 半截帧：留着等下一批（不能丢，否则画面会闪）
    frames, rest = split_jpeg_frames(JPEG_A + JPEG_B[:6])
    assert frames == [JPEG_A] and rest == JPEG_B[:6]

    # 前半截 + 后半截拼起来
    frames, rest = split_jpeg_frames(JPEG_B[:5])
    assert frames == [] and rest == JPEG_B[:5]
    frames, rest = split_jpeg_frames(rest + JPEG_B[5:])
    assert frames == [JPEG_B] and rest == b""


def test_split_jpeg_frames_drops_leading_garbage():
    """流开头可能有半截脏数据：丢掉它，不要把它当成帧的一部分。"""
    frames, rest = split_jpeg_frames(b"\x00\x01garbage" + JPEG_A)
    assert frames == [JPEG_A] and rest == b""


# ---------- 广播 ----------


def test_new_viewer_immediately_gets_the_current_frame():
    """迟到的观看者立刻看到当前画面，而不是等下一帧（5 fps 下最多等 200 ms，但体验差）。"""
    b = Broadcaster()
    b.publish(JPEG_A)
    q = b.subscribe()
    assert q.get_nowait() == JPEG_A
    assert b.viewers == 1
    b.unsubscribe(q)
    assert b.viewers == 0


def test_slow_viewer_loses_frames_instead_of_stalling_everyone():
    """慢客户端**丢最旧的一帧**，绝不阻塞广播——否则一个人卡住就全场卡住。"""
    b = Broadcaster(queue_max=2)
    q = b.subscribe()
    for _ in range(5):
        b.publish(JPEG_A)
    assert q.qsize() == 2, "队列已满仍在发（说明丢帧逻辑没生效）"
    assert b.frames == 5, "帧计数照实记，不因丢帧而回退"


def test_status_reports_stale_picture_as_not_ok():
    """超过 3 秒没有新帧就算"画面断了"——页面据此提示，而不是无限期显示旧画面。"""
    b = Broadcaster()
    assert b.status(now=0.0)["ok"] is False          # 一帧都还没有
    b.publish(JPEG_A)
    assert b.status(now=b.last_frame_at)["ok"] is True
    assert b.status(now=b.last_frame_at + 5)["ok"] is False


# ---------- 口令打码 ----------


def test_password_is_redacted_in_logs():
    """相机口令绝不能进日志/页面——它是这一路唯一的秘密。"""
    assert redact("rtsp://admin:secret@192.168.1.238:554/Streaming/Channels/101") == \
        "rtsp://admin:***@192.168.1.238:554/Streaming/Channels/101"
    assert "secret" not in redact("rtsp://admin:secret@10.0.0.1/x")
    assert redact("rtsp://192.168.1.238/x") == "rtsp://192.168.1.238/x"


# ---------- ffmpeg 命令 ----------


def test_ffmpeg_command_keeps_the_picture_small_and_bounded():
    url = "rtsp://admin:pw@192.168.1.238:554/Streaming/Channels/101"
    cmd = build_ffmpeg_cmd(url, scale=640, fps=5)
    joined = " ".join(cmd)
    assert "-rtsp_transport tcp" in joined, "UDP 在这台 DVR 上不稳"
    assert "scale=640:-2" in joined, "宽度限定、高度按比例"
    assert "-an" in joined, "不要音频"
    assert cmd[-2:] == ["mjpeg", "-"], "输出 MJPEG 到 stdout"
    assert url in cmd


# ---------- 监督循环：ffmpeg 挂了要自愈 ----------


class FakeProc:
    def __init__(self, chunks: list[bytes], *, returncode: int = 1):
        self.stdout = _Reader(chunks)
        self.stderr = _Reader([b"rtsp: connection refused\n"])
        self.returncode = returncode
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    async def wait(self):
        return self.returncode


class _Reader:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        await asyncio.sleep(0)
        return self._chunks.pop(0) if self._chunks else b""

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        return b"" if not self._chunks else self._chunks.pop(0)


@pytest.mark.anyio
async def test_supervisor_publishes_frames_then_restarts_with_backoff(monkeypatch):
    """ffmpeg 每次只给一帧就退出：帧要发出去，且**按退避重启**、错误要记下来。"""
    calls: list[list[str]] = []

    async def fake_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        return FakeProc([JPEG_A] if len(calls) == 1 else [JPEG_B])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    b = Broadcaster()
    logs: list[str] = []
    sup = PreviewSupervisor(["ffmpeg", "-i", "rtsp://admin:pw@h/x"], b,
                            log=logs.append, sleep=fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await sup.run_forever()

    assert b.frames == 2, "两轮各发了一帧"
    assert len(calls) == 2, "退出后确实重启了"
    assert sleeps == [1.0, 2.0], "退避递增"
    assert sup.restarts == 2
    assert sup.last_error and "connection refused" in sup.last_error
    assert all("pw" not in line or "***" in line for line in logs if "rtsp" in line), \
        "日志里不能出现相机口令"


# ---------- HTTP 面 ----------


def test_healthz_and_frame_endpoints():
    b = Broadcaster()
    with TestClient(create_app(broadcaster=b)) as c:
        assert c.get("/healthz").status_code == 503          # 还没有帧
        assert c.get("/frame.jpg").status_code == 503
        b.publish(JPEG_A)
        health = c.get("/healthz")
        assert health.status_code == 200 and health.json()["frames"] == 1
        shot = c.get("/frame.jpg")
        assert shot.status_code == 200 and shot.content == JPEG_A
        assert shot.headers["content-type"] == "image/jpeg"


@pytest.mark.anyio
async def test_stream_endpoint_advertises_multipart_mjpeg():
    """`<img>` 能不能直接播，取决于这个 content-type。

    这里**绕开 TestClient 直接调用路由函数**：`/stream.mjpg` 是无限流，用 TestClient 读它
    就是挂住（本文件第一版正是这么超时的，`route.endpoint()` 只拿到响应对象、不读 body）。
    另外顺手验观看者登记：拿到流之后 `viewers` 加一，关掉 body 迭代器后要归零——否则
    断开连接的客户端会永远占着一个队列，慢客户端就能把服务拖垮。
    """
    b = Broadcaster()
    b.publish(JPEG_A)
    app = create_app(broadcaster=b)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/stream.mjpg")

    resp = await route.endpoint()
    assert resp.media_type.startswith("multipart/x-mixed-replace; boundary=frame")
    assert resp.headers["cache-control"] == "no-store"
    assert b.viewers == 1

    body = resp.body_iterator
    first = await anext(body)
    assert first == mjpeg_part(JPEG_A)
    await body.aclose()
    assert b.viewers == 0, "客户端断开后必须注销队列"


def test_mjpeg_part_framing():
    """一段 = 边界 + 类型 + 长度 + 图片字节——长度写错浏览器就只显示第一帧。"""
    part = mjpeg_part(JPEG_A)
    assert part.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    assert f"Content-Length: {len(JPEG_A)}".encode() in part
    assert part.endswith(JPEG_A + b"\r\n")


# ---------- 从站位表取凭据 ----------


def test_rtsp_url_comes_from_the_station_table(tmp_path: Path):
    """凭据只有一个来源：与抓拍共用 `stations.yaml`（不另抄一份口令到 env）。"""
    path = tmp_path / "stations.yaml"
    path.write_text(
        "stations:\n"
        "  - {id: S101, box_id: B101, y: 187.1, z: -21.2, camera_ip: 192.168.1.238,\n"
        "     camera_user: admin, camera_pwd: pw}\n",
        encoding="utf-8",
    )
    url = rtsp_from_stations(str(path), log=lambda _m: None)
    assert url == "rtsp://admin:pw@192.168.1.238:554/Streaming/Channels/101"
    assert redact(url).endswith("@192.168.1.238:554/Streaming/Channels/101")


def test_rtsp_url_is_empty_when_the_table_is_missing(tmp_path: Path):
    logs: list[str] = []
    assert rtsp_from_stations(str(tmp_path / "nope.yaml"), log=logs.append) == ""
    assert any("读站位表失败" in m for m in logs)
