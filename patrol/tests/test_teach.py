"""站位示教 CLI：命令解析（纯逻辑）、记录/删除/写盘（假库 seam）、崩溃安全。

示教是把"实际框位与均分不符"这件事写进站位表的唯一途径，所以这里守三件事：
1. 记录的坐标必须来自**控制器当前读数**，不是推导值；
2. 解析层挡住越界/未知档位，别让错误的层框号写进现场表；
3. 写盘要么整份成功、要么原样留下旧表（不能把现场表清成空文件）。
"""

from __future__ import annotations

import io

import pytest
from fmc_fakes import FakeFmcLib
from patrol.fmc import Fmc4030
from patrol.stations import (
    CAMERA_IP,
    DEFAULT_ANGLE,
    GRID_COLS,
    GRID_LAYERS,
    Station,
    build_grid,
    cell_of,
    drop_station,
    load_stations,
)
from patrol.teach import TeachConsole, parse_teach_command

# ---------- 解析：示教专属动词 ----------


def test_parse_blank_and_comment():
    assert parse_teach_command("") is None
    assert parse_teach_command("   ") is None
    assert parse_teach_command("# 记录第 1 层第 1 框") is None


def test_parse_record_full_form():
    cmd = parse_teach_command("record S105 B105 1 5")
    assert cmd.kind == "record"
    assert cmd.station_id == "S105" and cmd.box_id == "B105"
    assert (cmd.layer, cmd.col) == (1, 5)
    assert cmd.angle == DEFAULT_ANGLE  # 省略角度档取现场单档


def test_parse_record_with_angle():
    cmd = parse_teach_command("record S105 B105 1 5 top45")
    assert cmd.angle == "top45"


def test_parse_record_rejects_out_of_range_layer_and_col():
    with pytest.raises(ValueError, match="层号越界"):
        parse_teach_command(f"record S1 B1 {GRID_LAYERS + 1} 1")
    with pytest.raises(ValueError, match="框号越界"):
        parse_teach_command(f"record S1 B1 1 {GRID_COLS + 1}")
    with pytest.raises(ValueError, match="层号越界"):
        parse_teach_command("record S1 B1 0 1")


def test_parse_record_rejects_non_integer_cell_and_unknown_angle():
    with pytest.raises(ValueError, match="整数"):
        parse_teach_command("record S1 B1 一 1")
    with pytest.raises(ValueError, match="角度档"):
        parse_teach_command("record S1 B1 1 1 top0")   # 一期只有 top45


def test_parse_record_arity():
    with pytest.raises(ValueError, match="语法"):
        parse_teach_command("record S105 B105 1")


def test_parse_list_drop_reset_save():
    assert parse_teach_command("list").kind == "list"
    assert parse_teach_command("reset").kind == "reset"
    assert parse_teach_command("pos").kind == "pos"
    assert parse_teach_command("drop S101").station_id == "S101"
    assert parse_teach_command("save").path is None
    assert parse_teach_command("save /opt/x/stations.yaml").path == "/opt/x/stations.yaml"


def test_parse_drop_requires_id():
    with pytest.raises(ValueError, match="语法"):
        parse_teach_command("drop")


def test_motion_verbs_are_delegated_to_m0_parser():
    """示教与 M0 调试台的 jog/abs/goto/home 必须逐字同义（同一份解析器）。"""
    jog = parse_teach_command("jog Y 10.5")
    assert (jog.kind, jog.axis, jog.value) == ("jog", 1, 10.5)
    assert parse_teach_command("home").kind == "home"
    assert parse_teach_command("quit").kind == "quit"
    goto = parse_teach_command("goto 1200 -120")
    assert (goto.kind, goto.x, goto.y) == ("goto", 1200.0, -120.0)


# ---------- 站位表辅助函数 ----------


def test_cell_of_decodes_grid_encoded_ids():
    assert cell_of("S105") == (1, 5)
    assert cell_of("S512") == (5, 12)
    assert cell_of("s101") == (1, 1)          # 大小写不敏感
    assert cell_of("S105-top45") == (1, 5)    # 双角度档后缀


def test_cell_of_returns_none_for_unknown_shapes():
    for weird in ("B105", "S1", "S1x5", "自定义位", "S190"):
        assert cell_of(weird) is None, weird


def test_drop_station_is_by_id_not_by_cell():
    """双角度档时一个格子两个站位，按格子删会多删一个。"""
    stations = [
        Station(id="S101", box_id="B101", y=1.0, z=-1.0, layer=1, col=1),
        Station(id="S101-top0", box_id="B101", y=1.0, z=-1.0, layer=1, col=1),
    ]
    drop_station(stations, "S101")
    assert [s.id for s in stations] == ["S101-top0"]


# ---------- 控制台：记录的是控制器当前坐标 ----------


def make_console(tmp_path, *, yz=(0.0, 0.0), **kw):
    """假库坐标是 3 元组 (X, Y, Z)：轴 0 未接线，示教只关心后两位。"""
    lib = FakeFmcLib()
    lib.pos = (0.0, yz[0], yz[1])
    client = Fmc4030.connect(lib=lib)
    out = io.StringIO()
    console = TeachConsole(
        client, stations_path=str(tmp_path / "stations.yaml"), stdout=out, **kw
    )
    return console, client, out


def test_record_uses_current_position_not_derived_grid(tmp_path):
    """现场框位与均分不符时，记下的必须是读数。"""
    console, _client, out = make_console(tmp_path, yz=(187.148, -99.5))
    console.execute(parse_teach_command("record S101 B101 1 1"))

    assert len(console.stations) == 1
    st = console.stations[0]
    assert (st.y, st.z) == (187.148, -99.5)
    assert (st.layer, st.col) == (1, 1)
    assert st.camera_ip == CAMERA_IP
    assert st.box_id == "B101"
    assert "新增" in out.getvalue() and "save 生效" in out.getvalue()
    assert console.dirty is True


def test_record_same_id_overwrites_in_place(tmp_path):
    console, client, _out = make_console(tmp_path, yz=(100.0, -21.2))
    console.execute(parse_teach_command("record S101 B101 1 1"))
    client._lib.pos = (0.0, 180.0, -21.2)          # 操作者重新对了一次位
    console.execute(parse_teach_command("record S101 B101 1 1"))

    assert len(console.stations) == 1
    assert console.stations[0].y == 180.0


def test_save_writes_yaml_and_clears_dirty(tmp_path):
    console, _client, out = make_console(tmp_path, yz=(187.148, -21.2))
    console.execute(parse_teach_command("record S101 B101 1 1"))
    console.execute(parse_teach_command("record S102 B102 1 2"))
    console.execute(parse_teach_command("save"))

    path = tmp_path / "stations.yaml"
    assert path.exists()
    reloaded = load_stations(str(path))
    assert [s.id for s in reloaded] == ["S101", "S102"]
    assert reloaded[0].y == 187.148
    assert console.dirty is False
    assert "已写盘" in out.getvalue()


def test_save_refuses_empty_table(tmp_path):
    """现场表被清成空文件是最坏结果——宁可拒绝写盘。"""
    console, _client, out = make_console(tmp_path)
    console.execute(parse_teach_command("save"))
    assert not (tmp_path / "stations.yaml").exists()
    assert "拒绝写盘" in out.getvalue()


def test_save_refuses_duplicate_ids(tmp_path):
    console, _client, out = make_console(tmp_path)
    console.stations = [
        Station(id="S101", box_id="B101", y=1.0, z=-1.0),
        Station(id="S101", box_id="B102", y=2.0, z=-1.0),
    ]
    console.execute(parse_teach_command("save"))
    assert not (tmp_path / "stations.yaml").exists()
    assert "重复" in out.getvalue()


def test_save_path_override(tmp_path):
    console, _client, _out = make_console(tmp_path, yz=(10.0, -10.0))
    console.execute(parse_teach_command("record S101 B101 1 1"))
    alt = tmp_path / "sub" / "alt.yaml"          # 目录不存在也要能写
    console.execute(parse_teach_command(f"save {alt}"))
    assert alt.exists()
    assert console.stations_path == str(alt)


def test_reset_seeds_derived_grid_with_configured_camera(tmp_path):
    console, _client, out = make_console(tmp_path, camera_ip="192.168.1.99")
    console.execute(parse_teach_command("reset"))

    assert len(console.stations) == GRID_LAYERS * GRID_COLS
    assert {s.camera_ip for s in console.stations} == {"192.168.1.99"}
    # 推导站位的 layer/col 与 id 自洽 → 现场能按编号逐格覆盖
    assert all(cell_of(s.id) == (s.layer, s.col) for s in console.stations)
    assert "尚未写盘" in out.getvalue()


def test_drop_reports_missing_id(tmp_path):
    console, _client, out = make_console(tmp_path, yz=(1.0, -1.0))
    console.execute(parse_teach_command("record S101 B101 1 1"))
    console.execute(parse_teach_command("drop S999"))
    assert len(console.stations) == 1
    assert "没有站位 S999" in out.getvalue()


def test_list_prints_count_and_dirty_marker(tmp_path):
    console, _client, out = make_console(tmp_path, yz=(187.148, -21.2))
    console.execute(parse_teach_command("list"))
    assert "空表" in out.getvalue()

    console.execute(parse_teach_command("record S101 B101 1 1"))
    console.execute(parse_teach_command("list"))
    text = out.getvalue()
    assert "S101" in text and "1-1" in text and "187.15" in text
    assert "共 1 个站位（有未保存的改动）" in text


def test_pos_prints_current_coordinates(tmp_path):
    console, _client, out = make_console(tmp_path, yz=(187.148, -21.2))
    console.execute(parse_teach_command("pos"))
    assert out.getvalue().strip() == "Y=187.15 Z=-21.20"


# ---------- 交互循环：退出时提醒未保存 ----------


def test_run_warns_about_unsaved_changes(tmp_path):
    console, _client, out = make_console(tmp_path, yz=(10.0, -10.0))
    console.stdin = io.StringIO("record S101 B101 1 1\nquit\n")
    console.run()
    assert "未保存的改动" in out.getvalue()


def test_run_continues_after_bad_command(tmp_path):
    """一条命令出错不该踢掉整个示教会话（现场正对着机器）。"""
    console, _client, out = make_console(tmp_path, yz=(10.0, -10.0))
    console.stdin = io.StringIO("record S1 B1 9 1\nrecord S101 B101 1 1\nquit\n")
    console.run()
    text = out.getvalue()
    assert "层号越界" in text
    assert len(console.stations) == 1


def test_run_help_lists_commands(tmp_path):
    console, _client, out = make_console(tmp_path)
    console.stdin = io.StringIO("help\nquit\n")
    console.run()
    text = out.getvalue()
    assert "record <id> <box_id>" in text and "save [路径]" in text


# ---------- 与推导网格的一致性（示教是覆盖，不是另起一套） ----------


def test_taught_table_is_loadable_by_m1(tmp_path):
    """示教产物必须能被 m1 装配入口直接读走（同一 YAML 形状）。"""
    console, _client, _out = make_console(tmp_path, yz=(187.148, -21.2))
    console.execute(parse_teach_command("record S101 B101 1 1"))
    console.execute(parse_teach_command("save"))

    stations = load_stations(str(tmp_path / "stations.yaml"))
    assert stations[0].camera_ip and stations[0].id == "S101"


def test_derived_grid_and_taught_ids_agree(tmp_path):
    """推导网格的编号就是示教时要输入的那一个（现场按编号逐格覆盖）。"""
    console, _client, _out = make_console(tmp_path)
    console.execute(parse_teach_command("reset"))
    derived = [s.id for s in build_grid()]
    assert [s.id for s in console.stations] == derived
