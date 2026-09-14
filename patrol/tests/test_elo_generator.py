from pathlib import Path

from fmc_fakes import FakeFmcLib
from patrol.elo import generate, generate_text
from patrol.fmc import Fmc4030
from patrol.motion_profile import M1
from patrol.stations import Station


def make_stations():
    return [
        Station(id="S01", box_id="B01", y=100.0, z=0.0),
        Station(id="S02", box_id="B02", y=100.0, z=-120.0),
    ]


def test_prologue_homes_y_negative_and_z_positive():
    """回零段用「设置回零运动参数」（含方向）而不是「设置单轴运动参数」。"""
    lines = generate(make_stations())
    assert lines[0] == "设置回零运动参数 | 1 | 90 | 900 | 2"    # Y：反向（负限位）
    assert lines[1] == "启动单轴回零运动 | 1 | 5"
    assert lines[2] == "设置回零运动参数 | 2 | 20 | 200 | 1"    # Z：向上（正限位）
    assert lines[3] == "启动单轴回零运动 | 2 | 5"
    assert lines[4] == "等待回零完成 | 1 | 2 | 1"
    assert lines[5] == "延时等待 | 2000"


def test_epilogue_returns_to_origin_with_yz_mask():
    lines = generate(make_stations())
    assert lines[-1] == "循环 | 1 | 999999"
    assert lines[-3] == "启动两轴直线插补 | 6 | 0 | 0"


def test_station_block_and_approach_chain():
    lines = generate(make_stations())
    # 站位1：从原点出发，接近点 = 距目标 5mm。
    # **空程段用巡检档全速**（纯 Y → 150/1500），**接近段才降速**（纯 Y → 30/300）。
    assert "设置直线插补参数 | 150 | 1500 | 1500" in lines
    assert "启动两轴直线插补 | 6 | 95 | 0" in lines
    assert "设置直线插补参数 | 30 | 300 | 300" in lines
    assert "启动两轴直线插补 | 6 | 100 | 0" in lines
    # 站位2：从 (100,0) 出发向 (100,-120)，接近点 (100,-115)；纯 Z → 用 Z 的档位
    assert "设置直线插补参数 | 20 | 200 | 200" in lines
    assert "启动两轴直线插补 | 6 | 100 | -115" in lines
    assert "设置直线插补参数 | 4 | 40 | 40" in lines
    assert "启动两轴直线插补 | 6 | 100 | -120" in lines
    # 每站 11 行 + 前置 6 + 收尾 3
    assert len(lines) == 6 + 11 * 2 + 3


def test_speed_override_is_written_verbatim():
    """显式覆盖只顶掉速度，加/减速度仍取该段按方向余弦折算的结果（空程段 = 1500）。"""
    lines = generate(make_stations(), travel_speed=42.0)
    assert "设置直线插补参数 | 42 | 1500 | 1500" in lines


def test_m1_profile_without_lamp_window_is_rejected():
    """M1 的灯窗由采图返回决定，脚本没有可依赖的固定灯窗，必须显式报错。"""
    import pytest

    with pytest.raises(ValueError, match="lamp_hold_s"):
        generate(make_stations(), profile=M1)


def test_wait_axis_lines_wait_on_the_wired_axes_not_on_unwired_x():
    """「等待轴运行完成」的参数槽必须填已接线的 Y/Z（1、2）。

    改造前这里写死了 `0 | 1`——脚本会去等根本没接线的 X 轴，属于典型的
    "参数抄错但语法正确"的 bug：编译通过、下载成功、现场空等。
    """
    lines = generate(make_stations())
    waits = [ln for ln in lines if ln.startswith("等待轴运行完成")]
    assert waits == ["等待轴运行完成 | 1 | 2 | 1"] * 5  # 2 站 × 2 段 + 收尾 1
    assert not any(ln.endswith("| 0") or " | 0 | " in ln for ln in waits)


def test_lamp_window_lines():
    lines = generate(make_stations())
    assert lines.count("本地输出口操作 | 0 | 1") == 2
    assert lines.count("本地输出口操作 | 0 | 0") == 2
    assert "延时等待 | 400" in lines  # 振动衰减


def test_generate_text_ends_with_newline():
    text = generate_text(make_stations())
    assert text.endswith("\n")
    assert len(text.splitlines()) == len(generate(make_stations()))


def test_deploy_downloads_and_starts(tmp_path):
    lib = FakeFmcLib()
    client = Fmc4030(lib)
    path = str(tmp_path / "patrol.elo")
    from patrol.elo import deploy

    deploy(make_stations(), file_path=path, client=client, script_name="patrol")
    assert lib.calls_of("download") == [(1, path.encode(), 2)]
    assert lib.calls_of("start_script") == [(1, b"patrol")]
    text = Path(path).read_text(encoding="utf-8")
    assert "启动单轴回零运动 | 1 | 5" in text
