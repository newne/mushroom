"""手动指令执行方（`deploy.manual_exec`）的行为。

用**假控制器**而不是厂商 SDK：这里要测的是"哪些指令会被放行、拒绝的理由是什么、
结果怎么写回"，真控制器的行为另有一整套（`patrol/tests/test_fmc_client.py`）。
"""

from datetime import datetime, timedelta

import pytest
from deploy.manual import Command, ManualChannel
from deploy.manual_exec import ManualExecutor, axis_of
from patrol.fmc import MotionAborted, TravelLimitError
from patrol.motion_profile import M1
from patrol.stations import Station

NOW = datetime(2026, 9, 14, 10, 0, 0)
STATIONS = [
    Station(id="S101", box_id="B101", y=100.0, z=-10.0, angle_profile="top45",
            camera_ip="192.168.1.238"),
    Station(id="S105", box_id="B105", y=500.0, z=-50.0, angle_profile="top45",
            camera_ip="192.168.1.238"),
]


class FakeFmc:
    """只实现执行方用到的那几个方法，并如实记录调用。"""

    def __init__(self, *, stop_fails=(), never_stops=False):
        self.calls: list[tuple] = []
        self.pos = (0.0, 0.0)
        self.stop_fails = list(stop_fails)
        self.never_stops = never_stops

    # --- 被测代码用到的 API ---

    def current_yz(self):
        return self.pos

    def check_travel(self, y, z):
        for spec, value in zip(M1.axes, (y, z), strict=True):
            if not spec.contains(value):
                raise TravelLimitError(spec.name, value, spec.travel_min, spec.travel_max)

    def check_axis_travel(self, axis, pos):
        spec = M1.by_index(axis)
        if not spec.contains(pos):
            raise TravelLimitError(spec.name, pos, spec.travel_min, spec.travel_max)

    def home_all(self, *, timeout_s=None, abort=None):
        if abort is not None and abort():
            raise MotionAborted("回零前已急停")
        self.calls.append(("home", timeout_s))
        self.pos = (0.0, 0.0)

    def jog(self, axis, dist, **kw):
        self.calls.append(("jog", axis, dist))
        y, z = self.pos
        self.pos = (y + dist, z) if axis == M1.axes[0].index else (y, z + dist)

    def wait_stop(self, axes=None, *, timeout_s=None, abort=None):
        if abort is not None and abort():
            raise MotionAborted("等待到位时按了急停")
        self.calls.append(("wait_stop", tuple(axes or ())))
        return not self.never_stops

    def goto(self, y, z, *, timeout_s=None, abort=None, **kw):
        if abort is not None and abort():
            raise MotionAborted("下发前已急停")
        self.calls.append(("goto", y, z))
        self.pos = (y, z)

    def lamp(self, on, *, io=0):
        self.calls.append(("lamp", on))

    def stop_everything(self):
        self.calls.append(("stop_everything",))
        return list(self.stop_fails)

    def close(self):
        self.calls.append(("close",))

    # --- 断言辅助 ---

    def called(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]


class FakeCapture:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.shots: list[dict] = []

    def capture(self, *, ip, user=None, pwd=None, filename):
        if self.fail:
            from patrol.capture_client import CaptureError

            raise CaptureError("这台相机没拍成")
        self.shots.append({"ip": ip, "filename": filename})
        return {"ok": True, "object_name": filename}


def make(tmp_path, *, fmc=None, capture=None, stations=STATIONS, **kw):
    channel = ManualChannel(str(tmp_path), now=lambda: NOW)
    fmc = fmc if fmc is not None else FakeFmc()
    rows: list[dict] = []
    logs: list[str] = []
    executor = ManualExecutor(
        channel,
        connect=lambda: fmc,
        stations=list(stations),
        capture=capture,
        append_index=rows.append,
        log=logs.append,
        sleep=lambda _s: None,
        now=lambda: NOW,
        **kw,
    )
    return channel, fmc, executor, rows, logs


def run(channel, executor, kind, args=None):
    """提交一条指令并执行它——走的是执行方真实的那条路（claim → execute → complete）。"""
    cmd, _why = channel.submit(kind, args=args or {})
    assert executor.service_once() is not None
    return cmd, channel.result()


# ---------- 放行的那些 ----------


def test_home_runs_and_reports_position(tmp_path):
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")            # 会动机构的指令都要先接管（ADR-0004 的会话）
    _cmd, res = run(channel, executor, "home")
    assert res.ok is True
    assert "回零完成" in res.detail and "Y=0.00 Z=0.00" in res.detail
    assert fmc.called("home") and fmc.called("close")


def test_goto_reaches_target(tmp_path):
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    _cmd, res = run(channel, executor, "goto", {"y": 1200.0, "z": 100.0})
    assert res.ok is True
    assert fmc.called("goto") == [("goto", 1200.0, 100.0)]


def test_jog_relative_and_axis_by_name(tmp_path):
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    fmc.pos = (300.0, 20.0)
    _cmd, res = run(channel, executor, "jog", {"axis": "Y", "mm": 50.0})
    assert res.ok is True
    assert fmc.called("jog") == [("jog", 1, 50.0)]
    assert "Y=350.00" in res.detail


def test_lamp_on_off(tmp_path):
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    assert run(channel, executor, "lamp", {"on": True})[1].ok
    assert run(channel, executor, "lamp", {"on": False})[1].ok
    assert fmc.called("lamp") == [("lamp", True), ("lamp", False)]


def test_stop_is_available_without_session_and_reports_shortfall(tmp_path):
    """急停指令不需要会话（它是"停下"，不是"移动"），且未确认的项必须报出来。"""
    channel, _fmc, executor, _rows, _logs = make(tmp_path, fmc=FakeFmc(stop_fails=["stop_axis(2)"]))
    _cmd, res = run(channel, executor, "stop")
    assert res.ok is False
    assert "未确认成功" in res.detail and "stop_axis(2)" in res.detail


# ---------- 拒绝的那些 ----------


def test_motion_needs_session(tmp_path):
    """没接管就动不了：这是**通道**层面的拒绝（`submit` 抛 PermissionError）。"""
    channel, _fmc, executor, _rows, _logs = make(tmp_path)
    with pytest.raises(PermissionError):
        channel.submit("goto", args={"y": 10.0, "z": 0.0})
    assert executor.service_once() is None      # 连一条待执行都没有


def test_goto_out_of_travel_is_rejected_before_issuing(tmp_path):
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    _cmd, res = run(channel, executor, "goto", {"y": 99_999.0, "z": 0.0})
    assert res.ok is False
    assert "超出行程" in res.detail
    assert fmc.called("goto") == []             # 越界绝不落到控制器上


def test_jog_landing_out_of_travel_is_rejected(tmp_path):
    """相对运动的落点要现读当前位置再算：Y 已经到顶时再 +5mm 必须拒绝。"""
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    fmc.pos = (M1.axes[0].travel_max, 0.0)
    _cmd, res = run(channel, executor, "jog", {"axis": "Y", "mm": 5.0})
    assert res.ok is False
    assert fmc.called("jog") == []


def test_unknown_axis_and_odd_args_are_rejected(tmp_path):
    channel, _fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    assert run(channel, executor, "jog", {"axis": "X", "mm": 1.0})[1].ok is False
    assert run(channel, executor, "jog", {"axis": "Y"})[1].ok is False
    assert run(channel, executor, "jog", {"axis": "Y", "mm": "5"})[1].ok is False
    assert run(channel, executor, "goto", {"y": 1.0})[1].ok is False
    assert run(channel, executor, "goto", {"y": float("nan"), "z": 0.0})[1].ok is False
    assert run(channel, executor, "lamp", {})[1].ok is False


def test_estop_latches_and_refuses_motion(tmp_path):
    """急停是个闩锁：置位期间只放行"关灯"，其余一律拒绝并说明怎么复位。"""
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    channel.raise_estop(reason="测试")

    _cmd, res = run(channel, executor, "goto", {"y": 10.0, "z": 0.0})
    assert res.ok is False and "急停已置位" in res.detail
    assert fmc.called("goto") == []
    assert fmc.called("home") == []
    assert fmc.calls == []                       # 连控制器都没连

    assert run(channel, executor, "lamp", {"on": False})[1].ok is True   # 关灯放行

    channel.clear_estop()
    assert run(channel, executor, "goto", {"y": 10.0, "z": 0.0})[1].ok is True


def test_stale_command_is_not_executed(tmp_path):
    """提交后超过上限才被领走 ⇒ 不执行（十分钟前的"点动"现在动是最坏的惊喜）。"""
    channel, fmc, executor, _rows, logs = make(tmp_path)
    channel.open_session("t")
    late = NOW + timedelta(seconds=120)
    channel.now = lambda: NOW
    _cmd, _why = channel.submit("goto", args={"y": 10.0, "z": 0.0})
    executor.now = lambda: late
    assert executor.service_once() is not None
    res = channel.result()
    assert res.ok is False and "已过期" in res.detail
    assert fmc.calls == []
    assert any("失败" in line for line in logs)


def test_connect_failure_is_reported_not_swallowed(tmp_path):
    channel, _fmc, _executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    channel.submit("home")

    def boom():
        from patrol.fmc import FmcError

        raise FmcError(-1, "open")

    executor = ManualExecutor(channel, connect=boom, log=lambda _m: None,
                              now=lambda: NOW, sleep=lambda _s: None)
    executor.service_once()
    res = channel.result()
    assert res.ok is False and "连接控制器失败" in res.detail


def test_estop_during_motion_aborts_and_stops(tmp_path):
    """运动中途按急停：抛 MotionAborted ⇒ 立刻 stop_everything，并如实上报。"""
    channel, fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")

    def goto(y, z, *, timeout_s=None, abort=None, **kw):
        channel.raise_estop(reason="运动中按下")
        raise MotionAborted("等待到位时按了急停")

    fmc.goto = goto
    _cmd, res = run(channel, executor, "goto", {"y": 10.0, "z": 0.0})
    assert res.ok is False
    assert "已急停" in res.detail
    assert fmc.called("stop_everything")


def test_wait_stop_timeout_is_reported(tmp_path):
    channel, _fmc, executor, _rows, _logs = make(tmp_path, fmc=FakeFmc(never_stops=True))
    channel.open_session("t")
    _cmd, res = run(channel, executor, "jog", {"axis": "Z", "mm": 5.0})
    assert res.ok is False
    assert "未在" in res.detail and "停稳" in res.detail


# ---------- 手动抓拍 ----------


def test_capture_writes_index_with_actual_position(tmp_path):
    capture = FakeCapture()
    channel, fmc, executor, rows, _logs = make(tmp_path, capture=capture)
    fmc.pos = (505.0, -50.0)                     # 操作者自己挪到这一站附近
    _cmd, res = run(channel, executor, "capture", {"station_id": "s105"})
    assert res.ok is True
    assert capture.shots and capture.shots[0]["filename"].endswith("B105_S105_top45_100000")
    assert fmc.called("lamp") == [("lamp", True), ("lamp", False)]   # 拍完必须灭灯
    (row,) = rows
    assert row["kind"] == "image_index" and row["manual"] is True
    assert row["station_id"] == "S105" and row["box_id"] == "B105"
    assert row["yz"] == [505.0, -50.0]           # 记的是**实际位置**，不是站位坐标


def test_capture_needs_a_known_station(tmp_path):
    capture = FakeCapture()
    channel, _fmc, executor, rows, _logs = make(tmp_path, capture=capture)
    channel.open_session("t")
    assert run(channel, executor, "capture", {})[1].ok is False
    res = run(channel, executor, "capture", {"station_id": "S999"})[1]
    assert res.ok is False and "不在站位表里" in res.detail
    assert rows == [] and capture.shots == []


def test_capture_without_service_says_so(tmp_path):
    """没接采图服务时明确拒绝，而不是静默"成功"——现场最怕这种假成功。"""
    channel, _fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    res = run(channel, executor, "capture", {"station_id": "S101"})[1]
    assert res.ok is False and "未配置" in res.detail


def test_capture_sync_failure_does_not_lose_the_shot(tmp_path):
    capture = FakeCapture()

    def boom():
        raise RuntimeError("prod 不可达")

    channel, _fmc, executor, rows, logs = make(tmp_path, capture=capture, flush=boom)
    channel.open_session("t")
    res = run(channel, executor, "capture", {"station_id": "S101"})[1]
    assert res.ok is True                    # 拍到了就是拍到了
    assert rows and any("索引同步失败" in line for line in logs)


# ---------- 小工具 ----------


def test_axis_of_accepts_names_and_indices():
    assert axis_of({"axis": "y"}, where="jog") == M1.axes[0].index
    assert axis_of({"axis": " Z "}, where="jog") == M1.axes[1].index
    assert axis_of({"axis": 2}, where="jog") == 2
    for bad in ({}, {"axis": "X"}, {"axis": 3}, {"axis": True}, {"axis": None}):
        with pytest.raises(ValueError):
            axis_of(bad, where="jog")


def test_service_once_returns_none_when_nothing_pending(tmp_path):
    _channel, _fmc, executor, _rows, _logs = make(tmp_path)
    assert executor.service_once() is None


def test_result_is_written_for_every_claim(tmp_path):
    channel, _fmc, executor, _rows, _logs = make(tmp_path)
    channel.open_session("t")
    cmd = Command(id="x1", kind="lamp", args={"on": True}, created_at=NOW.isoformat())
    channel._write(channel.cmd_path, {"id": "x1", "kind": "lamp", "args": {"on": True},
                                      "by": "op", "session": "", "created_at": cmd.created_at,
                                      "started_at": None})
    assert executor.service_once() is not None
    assert channel.result().ok is True
    assert channel.inflight() is None        # 结果写回后不再"在飞"
