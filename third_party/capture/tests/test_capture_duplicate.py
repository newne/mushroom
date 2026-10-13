"""帧级可信度：同一份字节被写给多个站位时必须被发现（只标记，不静默丢弃）。

背景是 2026-10-09 的采集事故：18:22:05–18:27:16 一整轮里 35 帧字节完全相同
（sha256 41685c09…），而轮次标记 ok=1 / 0 失败。见 `capture.CaptureService._note_frame`
的 docstring——那里有代码路径与"为什么只有服务侧能算"的说明。

这些用例钉三件事：指纹算得对（就是入库那份字节）、连续重复会被数出来、
**抓图本身不因为重复而失败**（标记归调用方处置）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from xcloudsdk_py import capture as capture_module
from xcloudsdk_py.capture import (
    FIELD_DUPLICATE_STREAK,
    FIELD_SAME_AS_PREVIOUS,
    FIELD_SHA256,
    CaptureService,
)


class FakeSDK:
    """按调用顺序吐出预置的图片字节；`None` 表示这一次不写文件（抓拍失败）。"""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self._sequence = 0

    def next_seq(self):
        self._sequence += 1
        return self._sequence

    def set_device_credentials(self, device_id, user, pwd):
        return None

    def dev_login(self, device_id, *, seq, timeout_s):
        return 1, 0

    def get_play_window_handle(self):
        return 0

    def media_realplay(self, device_id, *, channel, stream_type, hwnd, seq, timeout_s):
        return 2, 0

    def make_keyframe(self, device_id, *, channel, stream_type):
        return 0

    def wait_play_data(self, play_handle, *, timeout_s):
        # 真机流冻结时这条也常常返回 True（周期性的 PLAY_INFO 就能点亮 waiter）——
        # 这正是"只看它就判新鲜会漏"的原因，用例里显式两种都覆盖。
        return True, 1, 0, 0

    def snap_image_ex(self, play_handle, file_path, *, channel_index, param):
        payload = self.payloads.pop(0) if self.payloads else b"default"
        if payload is None:
            return -1                      # SDK 报失败：不写文件
        Path(file_path).write_bytes(payload)
        return 0

    def snap_image(self, play_handle, file_path):
        return -1

    def stop_media_play(self, play_handle):
        return None

    def dev_logout(self, device_id):
        return None


def _service(tmp_path, payloads, *, use_pool=False):
    service = CaptureService(sdk=FakeSDK(payloads), picture_dir=tmp_path,
                             minio=None, enable_pool=use_pool)
    return service, (service.pool_capture if use_pool else service.dynamic_capture)


def _shoot(capture, name):
    return capture(ip="192.168.1.238", storage="local", filename=f"20261009/{name}")


@pytest.fixture(autouse=True)
def _no_levels_patch(monkeypatch):
    """电平修正会重编码、改变字节数——本文件只关心"同一份字节"，把它摘掉。"""
    monkeypatch.setattr(capture_module, "_normalize_levels", lambda path: {})
    monkeypatch.setattr(capture_module, "_wait_for_file_ready", lambda *a, **k: True)


def test_distinct_frames_are_not_flagged(tmp_path):
    service, capture = _service(tmp_path, [b"frame-A", b"frame-B", b"frame-C"])
    shas = [_shoot(capture, f"B10{i}")[FIELD_SHA256] for i in (1, 2, 3)]
    assert len(set(shas)) == 3
    assert all(s is not None for s in shas)
    assert service._duplicate_streak == 1


def test_identical_frames_are_flagged_with_a_streak(tmp_path):
    """同一份字节反复出现 ⇒ same_as_previous + 递增的 streak（事故的形状）。"""
    service, capture = _service(tmp_path, [b"same"] * 4)
    first = _shoot(capture, "B101")
    assert first[FIELD_SAME_AS_PREVIOUS] is False
    assert first[FIELD_DUPLICATE_STREAK] == 1

    for i in (2, 3, 4):
        got = _shoot(capture, f"B10{i}")
        assert got[FIELD_SAME_AS_PREVIOUS] is True
        assert got[FIELD_DUPLICATE_STREAK] == i
        assert got[FIELD_SHA256] == first[FIELD_SHA256]
    assert service._duplicate_streak == 4


def test_streak_breaks_when_the_stream_recovers(tmp_path):
    """坏一段、好一段、再坏一段：streak 必须断，不能把两次事故接起来算。

    注意第 4 帧（又变黑）streak 回到 **1**：它只和**紧邻上一帧**比，而上一帧是好的。
    真要等到 streak=2，得再坏一帧——这就是"连续重复"这个词的字面含义。
    """
    service, capture = _service(tmp_path, [b"black", b"black", b"good", b"black", b"black"])
    _shoot(capture, "B101")
    assert _shoot(capture, "B102")[FIELD_DUPLICATE_STREAK] == 2     # 连坏
    assert _shoot(capture, "B103")[FIELD_DUPLICATE_STREAK] == 1     # 恢复
    assert _shoot(capture, "B104")[FIELD_DUPLICATE_STREAK] == 1     # 又坏，从 1 重数
    assert _shoot(capture, "B105")[FIELD_DUPLICATE_STREAK] == 2     # 连坏两帧才算数


def test_duplicate_does_not_fail_the_capture(tmp_path):
    """重复是**标记**，不是失败：这一帧照旧入库，处置归调用方。

    反过来（因为重复就判失败）会把一次相机抽风放大成整轮中止，而现场需要的是
    "这些帧别进统计"——那是轮次/索引语义，不在抓图服务里。
    """
    _, capture = _service(tmp_path, [b"same", b"same"])
    for i in (1, 2):
        got = _shoot(capture, f"B10{i}")
        assert got["success"] is True
        assert got["file_exists"] is True
        assert Path(got["file_path"]).exists()


def test_digest_describes_the_bytes_that_go_into_storage(tmp_path):
    """指纹必须算在**上传前**（电平修正之后）——否则算的不是入库那份。"""
    payload = b"payload-with-known-bytes"
    service, capture = _service(tmp_path, [payload])

    # 让电平修正在落盘后再改一次字节，指纹仍应是修正后的
    real_normalize = capture_module._normalize_levels

    def rewriting_normalize(path):
        Path(path).write_bytes(payload + b"-rewritten")
        return {"levels_normalized": True}

    capture_module._normalize_levels = rewriting_normalize
    try:
        got = _shoot(capture, "B101")
    finally:
        capture_module._normalize_levels = real_normalize
    assert got[FIELD_SHA256] == hashlib.sha256(payload + b"-rewritten").hexdigest()


def test_pool_path_reports_the_same_fields(tmp_path):
    """巡检走的是 `/pool_capture`：三条字段在连接池路径上必须同样有。"""
    _, capture = _service(tmp_path, [b"x", b"x"], use_pool=True)
    first = _shoot(capture, "B101")
    second = _shoot(capture, "B102")
    for got in (first, second):
        assert FIELD_SHA256 in got and FIELD_SAME_AS_PREVIOUS in got
        assert FIELD_DUPLICATE_STREAK in got
        assert "play_data_ready" in got, "这条以前被丢掉了，现在是诊断线索"
    assert second[FIELD_SAME_AS_PREVIOUS] is True


def test_unreadable_file_does_not_break_the_capture(tmp_path, monkeypatch):
    """算不出指纹时不猜"是"也不猜"不是"：抓图照旧成功，streak 断掉。"""
    service, capture = _service(tmp_path, [b"a", b"b"])
    monkeypatch.setattr(capture_module, "_sha256_of", lambda path: None)
    got = _shoot(capture, "B101")
    assert got["success"] is True
    assert got[FIELD_SHA256] is None
    assert got[FIELD_SAME_AS_PREVIOUS] is False
    assert got[FIELD_DUPLICATE_STREAK] == 0
    assert service._duplicate_streak == 0


def test_sha256_helper_returns_none_for_a_missing_file(tmp_path):
    """真的读不到文件时，helper 自己吞掉 OSError 返回 None（不让抓图失败）。"""
    assert capture_module._sha256_of(tmp_path / "nope.jpg") is None
