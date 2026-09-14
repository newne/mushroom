"""M1 装配入口：时刻表计算、站位表落盘、同步禁用、参数互斥。"""

from __future__ import annotations

from datetime import datetime

import pytest
from deploy.m1 import _NullSync, build_parser, load_station_list, next_run_delay
from patrol.motion_profile import CONTROLLER_IP
from patrol.stations import CAMERA_IP, GRID_COLS, GRID_LAYERS

# ---------- 时刻表 ----------


def test_next_run_delay_targets_this_hour_when_not_yet_passed():
    assert next_run_delay(datetime(2026, 9, 13, 8, 0, 0), 5) == pytest.approx(300.0)


def test_next_run_delay_rolls_over_when_already_passed():
    # 正好落在目标分钟 -> 顺延一小时（不是 0，否则会连跑两轮）
    assert next_run_delay(datetime(2026, 9, 13, 8, 5, 0), 5) == pytest.approx(3600.0)
    assert next_run_delay(datetime(2026, 9, 13, 8, 30, 0), 5) == pytest.approx(35 * 60.0)


def test_next_run_delay_rejects_out_of_range():
    with pytest.raises(ValueError):
        next_run_delay(datetime(2026, 9, 13, 8, 0, 0), 60)
    with pytest.raises(ValueError):
        next_run_delay(datetime(2026, 9, 13, 8, 0, 0), -1)


# ---------- 站位表 ----------


def test_station_list_is_generated_then_reloaded(tmp_path):
    path = tmp_path / "stations.yaml"
    first = load_station_list(str(path), camera_ip="192.168.1.238")
    assert len(first) == GRID_COLS * GRID_LAYERS == 60
    assert path.exists(), "首次应落盘，避免每轮重算"
    assert all(s.camera_ip == "192.168.1.238" for s in first)

    again = load_station_list(str(path), camera_ip="192.168.1.238")
    assert [s.id for s in again] == [s.id for s in first]


def test_station_list_defaults_to_global_camera(tmp_path):
    st = load_station_list(str(tmp_path / "s.yaml"), camera_ip=CAMERA_IP)
    assert st[0].camera_ip == CAMERA_IP


def test_empty_station_file_is_rejected(tmp_path):
    """空表不能当成"没配置就开跑"——那会静默跑一轮零站位的巡检。"""
    path = tmp_path / "stations.yaml"
    path.write_text("stations: []\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_station_list(str(path), camera_ip=CAMERA_IP)


def test_old_table_without_camera_column_still_runs(tmp_path):
    """手写/早期站位表常整列漏掉 camera_ip。本机只有一台相机，它是全局配置——

    漏这一列不该让人上不了机（预检里有"相机 IP 为空即拒绝"的闸门）。省略该列由
    dataclass 默认值兜住；显式写了 ``camera_ip: ""`` 的由 ``fill_camera_ip`` 兜住。
    """
    path = tmp_path / "stations.yaml"
    path.write_text(
        "stations:\n"
        "  - {id: S101, box_id: B101, y: 187.1, z: -21.2}\n"                      # 整列省略
        "  - {id: S102, box_id: B102, y: 561.5, z: -21.2, camera_ip: ''}\n"      # 显式空串
        "  - {id: S103, box_id: B103, y: 935.8, z: -21.2, camera_ip: 10.0.0.9}\n",
        encoding="utf-8",
    )
    stations = load_station_list(str(path), camera_ip="192.168.1.238")
    assert [s.camera_ip for s in stations] == [
        "192.168.1.238",   # 省略 -> dataclass 默认（全局唯一相机）
        "192.168.1.238",   # 空串 -> fill_camera_ip 兜住
        "10.0.0.9",        # 显式值 -> 绝不被覆盖（分机位的能力保留）
    ]
    assert not any(not s.camera_ip for s in stations), "预检闸门不该被这种表触发"




# ---------- 同步禁用 ----------


def test_null_sync_keeps_outbox_untouched():
    class Store:
        def __init__(self):
            self.replaced = False

        def replace(self, _rows):
            self.replaced = True

    store = Store()
    assert _NullSync().flush(store) == 0
    assert store.replaced is False, "禁用同步时不得清空 outbox（要留待补传）"


# ---------- 参数 ----------


def test_once_and_check_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--once", "--check"])


def test_defaults_match_field_conventions():
    args = build_parser().parse_args([])
    assert args.at_minute == 5, "默认避开老系统 :01–:02 的批量采图窗口"
    assert args.camera_ip == CAMERA_IP
    assert args.no_sync is False
    assert args.interval is None
    assert args.log.endswith("m1.log"), "默认就要落盘——上次上机的 stdout 只活在管道里"


# ---------- 日志落盘 ----------


def test_logger_writes_to_file_and_stdout(tmp_path, capsys):
    """人读日志要同时进文件与 stdout：现场出事后得能在机器上查到走过什么。"""
    from deploy.m1 import make_logger

    path = tmp_path / "sub" / "m1.log"     # 目录不存在也要能建
    say = make_logger(str(path))
    say("第一行")
    say("第二行")

    assert capsys.readouterr().out.count("\n") == 2, "stdout 也要有"
    text = path.read_text(encoding="utf-8")
    assert "第一行" in text and "第二行" in text


def test_logger_appends_across_runs(tmp_path):
    """常驻模式一轮一轮追加，不能每轮把上一轮的日志冲掉。"""
    from deploy.m1 import make_logger

    path = tmp_path / "m1.log"
    make_logger(str(path))("第一次运行")
    make_logger(str(path))("第二次运行")
    text = path.read_text(encoding="utf-8")
    assert "第一次运行" in text and "第二次运行" in text


def test_logger_falls_back_to_stdout_only(tmp_path, capsys):
    from deploy.m1 import make_logger

    make_logger(None)("只打 stdout")
    assert "只打 stdout" in capsys.readouterr().out


# ---------- 常驻模式与门禁的交互 ----------


def run_main(tmp_path, monkeypatch, argv):
    """跑一次 `main()`，把日志抓到内存里（同时验证日志落盘）。"""
    from deploy import m1 as m1mod

    logs: list[str] = []
    monkeypatch.setattr(m1mod, "log", logs.append)
    rc = m1mod.main(argv)
    return rc, logs


def common_argv(tmp_path, room, *, flags=(), **extra):
    argv = [
        "--room", str(room),
        "--stations", str(tmp_path / "stations.yaml"),
        "--outbox", str(tmp_path / "outbox.jsonl"),
        "--log", "",
        "--no-sync",
    ]
    argv += [f"--{f.replace('_', '-')}" for f in flags]
    for k, v in extra.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return argv


def closed_room(tmp_path):
    p = tmp_path / "room.yaml"
    # 入库 2026-03-16 ⇒ 今天（真实今天）已远超第 25 天 ⇒ 门禁关闭
    p.write_text('room_id: "611"\nentry_date: "2026-03-16"\n', encoding="utf-8")
    return p


def open_room(tmp_path):
    from datetime import date, timedelta

    p = tmp_path / "room.yaml"
    entry = date.today() - timedelta(days=10)      # 第 10 天 ⇒ 窗口内
    p.write_text(f'room_id: "611"\nentry_date: "{entry.isoformat()}"\n', encoding="utf-8")
    return p


def test_resident_mode_does_not_exit_when_gate_is_closed(tmp_path, monkeypatch):
    """门禁关着时进程必须**留在常驻循环里**。

    曾经这里是 `if not allowed: return 0`：systemd 看到的是"正常退出"（Restart=on-failure
    不会拉起），于是库房空置期一过、新批次进来时，机器上根本没有进程在等——除非有人
    发现并手动启动。判定不过只是"这一轮不动"，不是"这个进程该结束"。
    """
    rc, logs = run_main(tmp_path, monkeypatch,
                        common_argv(tmp_path, closed_room(tmp_path),
                                    interval=0.01, max_rounds=1))
    assert rc == 0
    text = "\n".join(logs)
    assert "进入常驻模式" in text, "门禁关着也要进常驻循环"
    assert "保持常驻" in text
    assert "第 1 轮结束：skipped" in text, "循环里应判定为 skipped（没动机构）"
    assert f"连接控制器 {CONTROLLER_IP}" not in text, "门禁不过时一次都不该连控制器"


def test_resident_mode_reaches_controller_when_gate_is_open(tmp_path, monkeypatch):
    """门禁开着就要真的去连（没有 SDK 库 ⇒ 连接阶段报错，但确实走到了那一步）。"""
    rc, logs = run_main(tmp_path, monkeypatch,
                        common_argv(tmp_path, open_room(tmp_path),
                                    interval=0.01, max_rounds=1))
    text = "\n".join(logs)
    assert "连接控制器" in text or "FMC4030" in text
    assert rc in (0, 2)


def test_once_mode_returns_zero_without_touching_controller(tmp_path, monkeypatch):
    """`--once`：门禁不过就是"这一轮不动"，退 0，不连控制器、不加载 SDK。"""
    rc, logs = run_main(tmp_path, monkeypatch,
                        common_argv(tmp_path, closed_room(tmp_path), flags=("once",)))
    assert rc == 0
    text = "\n".join(logs)
    assert "本轮不巡检" in text
    assert f"连接控制器 {CONTROLLER_IP}" not in text
    # 判定归 daemon（它会现读 room.yaml），所以"开始单轮"这行仍会打出来，
    # 随后紧跟一句"本轮不巡检：<原因>"。要断言的是**结果**与是否碰了控制器。
    assert "本轮不巡检" in text
    assert "单轮结束：skipped" in text


def test_check_still_preflights_when_gate_is_closed(tmp_path, monkeypatch):
    """`--check` 是运维的显式动作，不受门禁影响。

    早先 `--check` 在门禁关闭时直接 `return 0`——运维看到的是"预检通过"，而其实
    **一次都没检查**。这在库里没有批次时最危险：机器到底能不能跑，没人知道。
    现在它会真的去做只读预检（本机没有厂商 SDK ⇒ 在加载库这步就明确报错）。
    """
    rc, logs = run_main(tmp_path, monkeypatch,
                        common_argv(tmp_path, closed_room(tmp_path), flags=("check",)))
    text = "\n".join(logs)
    assert "预检通过" not in text, "不许在没检查的情况下报通过"
    assert rc == 2
    assert "FMC4030" in text or "库" in text


def test_check_requires_the_sdk_library(tmp_path, monkeypatch):
    from deploy import m1 as m1mod

    monkeypatch.setattr(m1mod, "log", lambda _m: None)
    monkeypatch.delenv("FMC4030_LIB_PATH", raising=False)
    rc = m1mod.main(common_argv(tmp_path, open_room(tmp_path), flags=("check",)))
    assert rc == 2, "拿不到 SDK 库就必须失败，而不是假装预检通过"
