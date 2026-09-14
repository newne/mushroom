"""新增测试：覆盖 M0 调试台修复项（P2/P5/P6/P9/P1/timeout/P11）。"""
from __future__ import annotations

import io

import pytest
from fmc_fakes import FakeFmcLib
from patrol.debug import DebugConsole, ParseError, parse_command
from patrol.fmc import Fmc4030, FmcError, MotionTimeoutError


def make_console():
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()
    return DebugConsole(client), lib


def test_motion_timeout_is_fmc_error_subclass():
    # P2: REPL 的 except FmcError 应能捕获超时，不再击穿
    assert issubclass(MotionTimeoutError, FmcError)


def test_parse_rejects_infinite():
    # P6: 非法数值（inf/nan）必须被拒
    for cmd in ("speed inf", "speed nan", "acc inf", "jog Y nan"):
        with pytest.raises(ParseError):
            parse_command(cmd)


def test_parse_rejects_extra_args():
    # P9: 多余参数应报错而非静默吞掉
    for cmd in ("goto 1 2 3", "speed 30 40", "jog Y 10 20", "lamp on nope"):
        with pytest.raises(ParseError):
            parse_command(cmd)


def test_parse_timeout_command():
    # timeout 命令新增
    cmd = parse_command("timeout 50")
    assert cmd.value == 50.0


def test_console_timeout_updates():
    con, _ = make_console()
    con.execute(parse_command("timeout 120"))
    assert con.timeout == 120.0


def test_console_negative_speed_rejected():
    # P6: 速度/加/减速必须为正
    con, _ = make_console()
    with pytest.raises(ParseError):
        con.execute(parse_command("speed -5"))


def test_jog_refused_when_axis_busy():
    # P5: 轴在运动中时拒绝再次点动
    con, lib = make_console()
    lib.busy_axes.add(1)
    assert con.execute(parse_command("jog Y 15")) is True
    assert lib.calls_of("jog") == []


def test_jog_allowed_when_axis_free():
    con, lib = make_console()
    con.execute(parse_command("jog Y 15"))
    assert len(lib.calls_of("jog")) == 1


def test_stop_calls_axis_stop_then_all():
    # P1: stop 先停接线两轴（Y/Z，立即）再停插补
    con, lib = make_console()
    con.execute(parse_command("stop"))
    assert lib.calls_of("stop_single") == [(1, 1, 2), (1, 2, 2)]
    assert lib.calls_of("stop_run") == [(1,)]


def test_run_handles_keyboardinterrupt(monkeypatch):
    # P12: Ctrl+C 停止轴且不退出 REPL
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()
    con = DebugConsole(client, stdin=io.StringIO("status\n"), stdout=io.StringIO())
    def boom(cmd):
        raise KeyboardInterrupt()
    monkeypatch.setattr(con, "execute", boom)
    con.run()
    assert lib.calls_of("stop_run") == [(1,)]

