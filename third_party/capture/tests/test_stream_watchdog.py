"""流中断看门狗（票 07 第一版）：只报信号、不做控制动作。

全部用临时目录造日志，**不依赖真机、不依赖真日志**。造日志的约定：
时间戳按 `now + tz_offset_s`（日志帧）反推，毫秒三位——和现场 `mediacodec.log`
的 `notifyPlayInfo` 行同形。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from xcloudsdk_py.capture import CaptureService
from xcloudsdk_py.stream_watchdog import (
    STATE_ALIVE,
    STATE_DEAD,
    STATE_SUSPECT,
    STATE_UNKNOWN,
    StreamWatchdog,
    tail_lines,
)

NOW = datetime(2026, 10, 13, 10, 0, 0)          # 服务时钟
TZ = 28800.0                                    # 日志帧 − 服务时钟 = 8h
T = 30.0                                        # 观察模式阈值


def watchdog(tmp_path, *, now=NOW, tz=TZ, dead_after_s=T, **kw) -> StreamWatchdog:
    return StreamWatchdog(log_dir=str(tmp_path), tz_offset_s=tz,
                          dead_after_s=dead_after_s, now=lambda: now, **kw)


def hb_line(age_s: float, *, pattern="notifyPlayInfo", prefix="") -> str:
    """一条心跳行：时间戳 = 日志帧的现在 − age_s。"""
    t = NOW + timedelta(seconds=TZ - age_s)
    return f"{prefix}{t.strftime('%H:%M:%S.%f')[:12]} {pattern} relativeTime=12345 frame=7\n"


def write_log(tmp_path, name, *lines):
    p = tmp_path / name
    p.write_text("".join(lines), encoding="utf-8")
    return p


# ---------- 正常路径 ----------


def test_fresh_heartbeat_reports_alive(tmp_path):
    write_log(tmp_path, "mediacodec.log", hb_line(2.0))
    wd = watchdog(tmp_path)
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_ALIVE
    assert f["stream_last_heartbeat_age_s"] == pytest.approx(2.0, abs=0.5)
    assert f["stream_outage"] is None


def test_stale_heartbeat_escalates_suspect_then_dead(tmp_path):
    write_log(tmp_path, "mediacodec.log", hb_line(40.0))
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_SUSPECT      # 30 ≤ 40 < 60

    write_log(tmp_path, "mediacodec.log", hb_line(75.0))
    wd2 = watchdog(tmp_path)
    wd2.observe()
    assert wd2.fields()["stream_state"] == STATE_DEAD        # ≥ 60


def test_boundary_is_exactly_the_threshold(tmp_path):
    write_log(tmp_path, "mediacodec.log", hb_line(T - 0.5))
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_ALIVE
    write_log(tmp_path, "mediacodec.log", hb_line(T + 0.5))
    wd2 = watchdog(tmp_path)
    wd2.observe()
    assert wd2.fields()["stream_state"] == STATE_SUSPECT


def test_outage_is_recorded_when_the_heartbeat_resumes(tmp_path):
    """心跳断了一阵又回来 ⇒ 记一次 outage（起/止/时长）。"""
    write_log(tmp_path, "mediacodec.log", hb_line(400.0), hb_line(10.0))
    wd = watchdog(tmp_path)
    wd.observe()
    out = wd.fields()["stream_outage"]
    assert out is not None
    assert out["duration_s"] == pytest.approx(390.0, abs=0.5)
    assert out["start"] < out["end"]


def test_no_outage_while_the_heartbeat_is_regular(tmp_path):
    write_log(tmp_path, "mediacodec.log", hb_line(30.0), hb_line(20.0), hb_line(10.0))
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_outage"] is None


def test_garbage_lines_are_ignored(tmp_path):
    write_log(tmp_path, "mediacodec.log",
              "totally unrelated line\n",
              "notifyPlayInfo but no timestamp at all\n",
              hb_line(3.0))
    wd = watchdog(tmp_path)
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_ALIVE      # 坏行不致命，好心跳照常用
    assert f["stream_last_heartbeat_age_s"] == pytest.approx(3.0, abs=0.5)


# ---------- fail-open：绝不许假报 alive ----------


def test_missing_log_is_unknown_never_alive(tmp_path):
    """日志目录整个不存在 ⇒ unknown，且 age 为 None（从没读到过心跳）。"""
    wd = watchdog(tmp_path / "nope")
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_UNKNOWN
    assert f["stream_last_heartbeat_age_s"] is None
    assert f["stream_outage"] is None


def test_empty_log_is_unknown(tmp_path):
    write_log(tmp_path, "mediacodec.log", "")
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_UNKNOWN


def test_heartbeat_without_a_usable_timestamp_degrades_to_unknown(tmp_path):
    """看到 notifyPlayInfo 但读不出可信时间 ⇒ 不敢用，退 unknown（不是 alive）。"""
    write_log(tmp_path, "mediacodec.log", "notifyPlayInfo relativeTime=12345\n")
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_UNKNOWN


def test_wrong_tz_offset_is_unknown_not_a_bogus_dead(tmp_path):
    """时区偏置配错（这里是 0，实际差 8h）⇒ 心跳看起来在将来 ⇒ unknown。

    这条是安全阀：若没有将来守卫，偏移错 8h 会被算成"心跳在 8 小时后"，
    状态可能假报 dead——比 unknown 危险得多。
    """
    write_log(tmp_path, "mediacodec.log", hb_line(2.0))
    wd = watchdog(tmp_path, tz=0.0)
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_UNKNOWN
    assert f["stream_last_heartbeat_age_s"] is None


def test_kill_switch_reports_unknown(tmp_path):
    write_log(tmp_path, "mediacodec.log", hb_line(1.0))
    wd = watchdog(tmp_path, enabled=False)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_UNKNOWN


# ---------- 时间戳解析 ----------


@pytest.mark.parametrize("line", [
    "2026-10-09 17:22:13.952 notifyPlayInfo relativeTime=1",
    "[17:22:13.952] notifyPlayInfo",
    "17:22:13,952 notifyPlayInfo",
    "17:22:13 notifyPlayInfo",
    "notifyPlayInfo at 17:22:13.95 ok",
])
def test_timestamp_formats(line):
    """各种行内格式都要能取出同一个时刻（日志帧的现在 = 18:00）。"""
    wd = StreamWatchdog(log_dir="/nonexistent", now=lambda: NOW, tz_offset_s=TZ)
    stamp = wd._stamp(line)
    assert stamp is not None
    assert (stamp.hour, stamp.minute, stamp.second) == (17, 22, 13)


def test_future_timestamp_is_rejected_not_rolled_back():
    """时间戳在将来 ⇒ 拒绝。

    故意不做"回退一天"：那会把"时区偏置配错 8 小时"洗成一条 16 小时前的合法心跳，
    从而**假报 dead**——比 unknown 危险得多。代价是跨零点后读到昨晚的行被丢，
    状态短暂退成 unknown（fail-open），下一条心跳到来即恢复。
    """
    wd = StreamWatchdog(log_dir="/nonexistent", now=lambda: NOW, tz_offset_s=TZ)
    assert wd._stamp("23:59:59.900 notifyPlayInfo") is None


def test_line_without_timestamp_returns_none():
    wd = StreamWatchdog(log_dir="/nonexistent", now=lambda: NOW, tz_offset_s=TZ)
    assert wd._stamp("notifyPlayInfo nothing here") is None


# ---------- 轮转 ----------


def test_rename_rotation_keeps_following(tmp_path):
    """.log → .1 且 SDK 另开新 .log：inode 变了必须重开，否则永远读旧文件。"""
    write_log(tmp_path, "mediacodec.log", hb_line(5.0))
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_ALIVE

    os.rename(tmp_path / "mediacodec.log", tmp_path / "mediacodec.log.1")
    write_log(tmp_path, "mediacodec.log", hb_line(1.0))     # SDK 开的新文件
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_ALIVE, "rename 轮转后必须继续跟上新文件"
    assert f["stream_last_heartbeat_age_s"] == pytest.approx(1.0, abs=0.5)


def test_truncate_rotation_resets_the_offset(tmp_path):
    """copy-truncate：同一个 inode 被截断，offset 必须归零，否则会读到文件尾之外。"""
    p = write_log(tmp_path, "mediacodec.log", hb_line(5.0) + ("x" * 2000 + '\n') * 3)
    wd = watchdog(tmp_path)
    wd.observe()
    assert wd.fields()["stream_state"] == STATE_ALIVE
    assert wd._tail._offset > 1000                 # offset 先被推高，截断才可判

    p.write_text(hb_line(1.0), encoding="utf-8")            # 原地截断重写
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_ALIVE
    assert f["stream_last_heartbeat_age_s"] == pytest.approx(1.0, abs=0.5)


def test_bootstrap_finds_the_last_heartbeat_in_a_rotated_file(tmp_path):
    """刚重启的进程：.log 是空的（刚轮转），心跳在 .1 里 ⇒ 回看要能找到。"""
    write_log(tmp_path, "mediacodec.log", "")
    write_log(tmp_path, "mediacodec.log.1", hb_line(8.0), hb_line(400.0))
    wd = watchdog(tmp_path)
    wd.observe()
    f = wd.fields()
    assert f["stream_state"] == STATE_ALIVE
    assert f["stream_last_heartbeat_age_s"] == pytest.approx(8.0, abs=0.5)  # 取更新的那条


def test_tail_lines_drops_the_partial_first_line(tmp_path):
    p = tmp_path / "x.log"
    p.write_bytes(b"partial" + b"a" * 5000 + b"\n18:00:00.000 notifyPlayInfo\n")
    lines = tail_lines(p, 100)
    assert any("notifyPlayInfo" in ln for ln in lines)
    assert not any(ln.startswith("partial") for ln in lines)


def test_tail_lines_on_a_missing_file_is_empty(tmp_path):
    assert tail_lines(tmp_path / "nope.log", 1024) == []


# ---------- 与抓拍响应的对接 ----------


class FakeSDK:
    def __init__(self):
        self._sequence = 0

    def next_seq(self):
        self._sequence += 1
        return self._sequence

    def set_device_credentials(self, *a):
        return None

    def dev_login(self, device_id, *, seq, timeout_s):
        return 1, 0

    def get_play_window_handle(self):
        return 0

    def media_realplay(self, device_id, *, channel, stream_type, hwnd, seq, timeout_s):
        return 2, 0

    def make_keyframe(self, *a, **k):
        return 0

    def wait_play_data(self, play_handle, *, timeout_s):
        return True, 1, 0, 0

    def snap_image_ex(self, play_handle, file_path, *, channel_index, param):
        Path(file_path).write_bytes(b"img")
        return 0

    def snap_image(self, *a, **k):
        return -1

    def stop_media_play(self, *a):
        return None

    def dev_logout(self, *a):
        return None


@pytest.mark.parametrize("use_pool", [False, True], ids=["dynamic", "pool"])
def test_capture_response_carries_the_three_fields(tmp_path, monkeypatch, use_pool):
    """三个字段必须出现在采图响应里（且不影响成功与否）。"""
    monkeypatch.setattr("xcloudsdk_py.capture._normalize_levels", lambda path: {})
    monkeypatch.setattr("xcloudsdk_py.capture._wait_for_file_ready", lambda *a, **k: True)
    write_log(tmp_path, "mediacodec.log", hb_line(1.0))
    wd = StreamWatchdog(log_dir=str(tmp_path), tz_offset_s=TZ,
                        now=lambda: NOW)
    service = CaptureService(sdk=FakeSDK(), picture_dir=tmp_path, minio=None,
                             enable_pool=use_pool, stream_watchdog=wd)
    capture = service.pool_capture if use_pool else service.dynamic_capture
    got = capture(ip="192.168.1.238", storage="local", filename="20261013/B101_S101")

    assert got["success"] is True                     # 控制流没被看门狗碰过
    assert got["stream_state"] == STATE_ALIVE
    assert got["stream_last_heartbeat_age_s"] == pytest.approx(1.0, abs=0.5)
    assert got["stream_outage"] is None


def test_watchdog_state_does_not_change_capture_outcome(tmp_path, monkeypatch):
    """看门狗说 unknown/dead 时，抓图照旧成功——第一版只报信号。"""
    monkeypatch.setattr("xcloudsdk_py.capture._normalize_levels", lambda path: {})
    monkeypatch.setattr("xcloudsdk_py.capture._wait_for_file_ready", lambda *a, **k: True)
    write_log(tmp_path, "mediacodec.log", hb_line(999.0))     # 早就没心跳了
    wd = StreamWatchdog(log_dir=str(tmp_path), tz_offset_s=TZ, now=lambda: NOW)
    service = CaptureService(sdk=FakeSDK(), picture_dir=tmp_path, minio=None,
                             enable_pool=False, stream_watchdog=wd)
    got = service.dynamic_capture(ip="192.168.1.238", storage="local",
                                  filename="20261013/B101_S101")
    assert got["success"] is True
    assert got["stream_state"] == STATE_DEAD
