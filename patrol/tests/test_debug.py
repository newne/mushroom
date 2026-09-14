"""M0 手动调试控制台：命令解析（纯逻辑）与对客户端的调度（假库 seam）。"""

from __future__ import annotations

import io

import pytest
from fmc_fakes import FakeFmcLib
from patrol.debug import (
    AXIS_NAME,
    DebugConsole,
    ParseError,
    parse_command,
    warn_soft_limits,
)
from patrol.fmc import Fmc4030, TravelLimitError

# ---------- 命令解析（纯函数，独立已知字面量） ----------

def test_parse_blank_and_comment():
    assert parse_command("") is None
    assert parse_command("   ") is None
    assert parse_command("# 注释") is None


def test_axis_names_are_derived_from_the_profile():
    """轴名 ↔ 控制器轴号取自参数单源：Y=1、Z=2；未接线的 X 不在表里。"""
    assert AXIS_NAME == {"Y": 1, "Z": 2}
    with pytest.raises(ParseError):
        parse_command("jog X 10")


def test_parse_jog_relative():
    cmd = parse_command("jog Y 10.5")
    assert cmd.kind == "jog"
    assert (cmd.axis, cmd.value) == (1, 10.5)


def test_parse_jog_negative():
    cmd = parse_command("jog Z -20")
    assert cmd.kind == "jog"
    assert (cmd.axis, cmd.value) == (2, -20.0)


def test_parse_axis_absolute():
    cmd = parse_command("abs Y 1200")
    assert cmd.kind == "abs"
    assert (cmd.axis, cmd.value) == (1, 1200.0)


def test_parse_goto_two_axis():
    cmd = parse_command("goto 1200 -120")
    assert cmd.kind == "goto"
    assert (cmd.x, cmd.y) == (1200.0, -120.0)


def test_parse_set_param():
    assert parse_command("speed 30").kind == "speed"
    assert parse_command("acc 200").kind == "acc"
    assert parse_command("dec 200").kind == "dec"


def test_parse_simple_kinds():
    assert parse_command("status").kind == "status"
    assert parse_command("home").kind == "home"
    assert parse_command("stop").kind == "stop"
    assert parse_command("lamp on").kind == "lamp"
    assert parse_command("quit").kind == "quit"


def test_parse_unknown_raises():
    with pytest.raises(ParseError):
        parse_command("frobnicate 10")


def test_parse_bad_number_raises():
    with pytest.raises(ParseError):
        parse_command("jog Y abc")


# ---------- 控制台到客户端调度（假库 seam） ----------

def make_console() -> tuple[DebugConsole, FakeFmcLib]:
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()
    return DebugConsole(client), lib


def test_console_jog_forwards_relative():
    con, lib = make_console()
    con.speed = 40.0
    con.acc = 90.0
    con.dec = 90.0
    con.execute(parse_command("jog Y 15"))
    assert lib.calls_of("jog") == [(1, 1, 15.0, 40.0, 90.0, 90.0, 1)]


def test_console_abs_forwards_absolute():
    """未显式设档时，参数按该轴整定值解析（Y 点动档 = 50/500 × 0.2）。"""
    con, lib = make_console()
    con.execute(parse_command("abs Y 500"))
    assert lib.calls_of("jog") == [(1, 1, 500.0, 10.0, 100.0, 100.0, 2)]


def test_console_goto_forwards_line():
    con, lib = make_console()
    con.execute(parse_command("goto 300 0"))
    # 纯 Y 行程：合成量退化为 Y 自己的点动档；掩码是 Y+Z = 0x06
    assert lib.calls_of("line2") == [(1, 0x06, 300.0, 0.0, 10.0, 100.0, 100.0)]


def test_console_rejects_out_of_travel():
    con, lib = make_console()
    before = len(lib.calls)
    with pytest.raises(TravelLimitError, match="超出行程"):
        con.execute(parse_command("goto 300 50"))   # Z 上限是 0
    assert len(lib.calls) == before                  # 一条指令都没发出去
    with pytest.raises(TravelLimitError):
        con.execute(parse_command("abs Z 10"))
    assert lib.calls_of("jog") == []


def test_console_run_survives_out_of_travel():
    """越界是操作者最常见的输入错误，REPL 必须报错后继续而不是崩掉。"""
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()
    out = io.StringIO()
    con = DebugConsole(client, stdin=io.StringIO("goto 300 50\nquit\n"), stdout=out)
    con.run()
    assert "超出行程" in out.getvalue()
    assert "未知错误码" not in out.getvalue()   # 域错误不该套 SDK 错误码模板
    assert lib.calls_of("line2") == []


def test_console_set_param_updates_defaults():
    con, _ = make_console()
    con.execute(parse_command("speed 60"))
    con.execute(parse_command("acc 250"))
    con.execute(parse_command("dec 250"))
    assert (con.speed, con.acc, con.dec) == (60.0, 250.0, 250.0)


def test_console_lamp_toggles():
    con, lib = make_console()
    con.execute(parse_command("lamp on"))
    con.execute(parse_command("lamp off"))
    assert lib.calls_of("set_output") == [(1, 0, 1), (1, 0, 0)]


def test_console_stop_halts():
    con, lib = make_console()
    con.execute(parse_command("stop"))
    assert lib.calls_of("stop_single") == [(1, 1, 2), (1, 2, 2)]
    assert lib.calls_of("stop_run") == [(1,)]


def test_console_home_returns_ok():
    con, lib = make_console()
    assert con.execute(parse_command("home")) is True
    assert [h[1] for h in lib.calls_of("home")] == [1, 2]


def test_console_run_reads_stdin_until_quit():
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    lib.calls.clear()
    con = DebugConsole(client, stdin=io.StringIO("speed 40\njog Y 10\nquit\n"),
                       stdout=io.StringIO())
    con.run()
    # 从 stdin 逐行消费，quit 结束；速度档影响点动参数（加速度仍取该轴整定值）
    assert lib.calls_of("jog") == [(1, 1, 10.0, 40.0, 100.0, 100.0, 1)]


# ---------- 回零：控制器回零超时 vs 主机侧等待超时（两条失败路径必须分开报） ----------

def make_console_with(**lib_kwargs) -> tuple[DebugConsole, FakeFmcLib, io.StringIO]:
    lib = FakeFmcLib(**lib_kwargs)
    client = Fmc4030(lib)
    client.open()
    out = io.StringIO()
    return DebugConsole(client, stdout=out), lib, out


def test_parse_para():
    assert parse_command("para").kind == "para"


def test_console_home_reports_controller_overtime():
    """控制器自行终止回零（限位开关没触发）→ 必须说清「原点不可信」。"""
    con, _lib, out = make_console_with(home_overtime=True)
    assert con.execute(parse_command("home")) is True
    text = out.getvalue()
    assert "回零超时" in text
    assert "原点不可信" in text
    assert "未知错误码" not in text          # 域错误不套 SDK 错误码模板
    assert "回零完成" not in text            # 绝不能报成功


def test_console_home_reports_host_side_timeout():
    """控制器没报错也没报完成 → 主机侧等到超时，提示轴可能仍在动。"""
    con, _lib, out = make_console_with(stop_after_status_calls=10 ** 9)
    con.timeout = 0.01
    con.execute(parse_command("home"))
    text = out.getvalue()
    assert "回零到位确认超时" in text
    assert "仍在运动" in text
    assert "回零完成" not in text


def test_console_home_waits_by_default():
    """home_all 默认阻塞；不再额外调一次 wait_stop（旧实现会重复等待）。"""
    con, lib, _out = make_console_with()
    con.execute(parse_command("home"))
    assert [h[1] for h in lib.calls_of("home")] == [1, 2]
    assert lib.calls_of("check_stop") == []      # 等待走状态位，不走单轴停止查询


# ---------- 急停：stop_everything 收集失败而非抛出 ----------

def test_console_stop_reports_partial_failure():
    con, lib, out = make_console_with()
    lib.stop_single_rc = -5                      # 单轴立即停止下发失败
    assert con.execute(parse_command("stop")) is True
    text = out.getvalue()
    assert "2 项未确认成功" in text
    assert "stop_axis(1)" in text and "stop_axis(2)" in text
    assert "断电" in text                        # 失败时必须提示人工确认


def test_console_stop_ok_message_when_all_delivered():
    con, _lib, out = make_console_with()
    con.execute(parse_command("stop"))
    assert "已停止" in out.getvalue()


# ---------- 控制器整定参数：软限位必须可读、可核对（ADR-0008） ----------

def test_console_para_flags_narrow_soft_limits():
    """出厂默认软限位 ±200mm 窄于 Y 0…4492mm → 必须点名并解释后果。"""
    con, _lib, out = make_console_with()
    con.execute(parse_command("para"))
    text = out.getvalue()
    assert "192.168.1.239" in text
    assert "Y(轴1)" in text and "Z(轴2)" in text
    assert "窄于机械行程" in text
    assert "截断" in text                        # 讲清「下发成功但只走一小段」的根因


def test_console_para_ok_when_limits_cover_travel():
    con, _lib, out = make_console_with(soft_limits=(2000, 5000))
    con.execute(parse_command("para"))
    assert "一致" in out.getvalue()
    assert "截断" not in out.getvalue()


def test_console_para_survives_read_failure():
    """读设备参数失败也只是条报错，不该把 REPL 弄崩。"""
    con, lib, out = make_console_with()
    lib.FMC4030_Get_Device_Para = lambda dev_id, buf: -5
    assert con.execute(parse_command("para")) is True
    assert "读取设备参数失败" in out.getvalue()


# ---------- 连接即体检：警告但不阻断（M0 本就是排障入口） ----------

def test_warn_soft_limits_warns_without_raising():
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    client.open()
    err = io.StringIO()
    warn_soft_limits(client, stream=err)
    text = err.getvalue()
    assert "控制器软限位未整定" in text
    assert "ADR-0008" in text


def test_warn_soft_limits_silent_when_commissioned():
    lib = FakeFmcLib(soft_limits=(2000, 5000))
    client = Fmc4030(lib)
    client.open()
    err = io.StringIO()
    warn_soft_limits(client, stream=err)
    assert err.getvalue() == ""


def test_warn_soft_limits_reports_read_failure():
    lib = FakeFmcLib()
    lib.FMC4030_Get_Device_Para = lambda dev_id, buf: -1
    client = Fmc4030(lib)
    client.open()
    err = io.StringIO()
    warn_soft_limits(client, stream=err)
    assert "读取控制器软限位失败" in err.getvalue()
