"""Z 重锚（`stations.reanchor_z_in_file` + `patrol-migrate z-anchor`）。

为什么要有这条路：改 `GRID_LAYER_Z_OFFSETS` **只影响新生成的表**，库房里那份
`configs/stations.yaml` 会继续按旧锚点跑——"改了没生效"就是这么来的
（`station-retarget.md` §2 记着这个坑）。所以用户一定锚点（2026-10-10：
−40/−90/−145/−195）必须有一条"刷已有表"的固定动作：先看 diff、再落盘、落盘前备份。

这些测试盯四件事：z 按层号重算、相对旧锚点学到的 `trim_z` 作废、
`y`/`trim_y`/id 不被牵连、没有层号的表被拒（而不是悄悄少改几个站）。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from patrol.migrate import main
from patrol.stations import (
    DEFAULT_ANGLE,
    GRID_LAYERS,
    Station,
    build_grid,
    load_stations,
    reanchor_z_in_file,
    save_stations,
)

# 2026-10-10 之前的锚点（Z 框架迁移当天的值）
OLD_ANCHOR_Z = {1: -57.0, 2: -80.0, 3: -152.0, 4: -202.0}
# 用户重定后的初始锚点
NEW_ANCHOR_Z = {1: -40.0, 2: -90.0, 3: -145.0, 4: -195.0}


def stale_table():
    """一份"旧锚点 + 学过微调"的表：每层 2 框，够看层号与保留字段。"""
    return [
        replace(
            Station(id=f"S{L}{c:02d}", box_id=f"B{L}{c:02d}",
                    y=100.0 * c, z=OLD_ANCHOR_Z[L], layer=L, col=c),
            trim_y=1.5 if c == 2 else 0.0,
            trim_z=-3.0 if c == 2 else 0.0,
            trim_note="2026-10-08 framing" if c == 2 else "",
        )
        for L in range(1, GRID_LAYERS + 1) for c in (1, 2)
    ]


def write(path, stations):
    save_stations(stations, str(path))
    return str(path)


# ---------- 函数本体 ----------


def test_reanchor_rewrites_z_per_layer(tmp_path):
    path = write(tmp_path / "stations.yaml", stale_table())
    moved = reanchor_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    for L, z in NEW_ANCHOR_Z.items():
        assert got[f"S{L}01"].z == pytest.approx(z)
    assert moved == GRID_LAYERS * 2          # 8 个站位的 z 全变了
    # 与"新生成一张表"逐点一致——代码与数据重新说同一件事
    fresh = {s.id: s.z for s in build_grid() if s.id in got}
    assert {k: s.z for k, s in got.items()} == pytest.approx(fresh)


def test_reanchor_keeps_y_ids_and_trim_y(tmp_path):
    """横向装配误差与层锚点无关：`y` / `trim_y` / id / 相机字段原样保留。"""
    table = stale_table()
    table[1] = replace(table[1], camera_ip="192.168.1.231", exposure_note="locked")
    path = write(tmp_path / "stations.yaml", table)
    reanchor_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S102"].y == pytest.approx(200.0)
    assert got["S102"].trim_y == pytest.approx(1.5)
    assert got["S102"].id == "S102" and got["S102"].box_id == "B102"
    assert got["S102"].camera_ip == "192.168.1.231"
    assert got["S102"].exposure_note == "locked"


def test_reanchor_normalises_a_stale_angle_label(tmp_path):
    """角度档是**标签**，必须和现场支架一致：表里还留着 top45，而支架装在平视位。"""
    table = stale_table()
    table[0] = replace(table[0], angle_profile="top45")
    path = write(tmp_path / "stations.yaml", table)
    logs: list[str] = []
    reanchor_z_in_file(path, log=logs.append)

    got = {s.id: s for s in load_stations(path)}
    assert {s.angle_profile for s in got.values()} == {DEFAULT_ANGLE}
    assert any("S101" in line and "角度档" in line for line in logs)


def test_reanchor_zeroes_trim_z_learned_against_the_old_anchor(tmp_path):
    """`trim_z` 的语义是"叠在锚点上的小量"——锚点一换它就失去意义，留着会推偏新锚点。"""
    table = stale_table()
    table[2] = replace(table[2], trim_y=0.0, trim_z=-2.0, trim_note="2026-10-08 framing")
    path = write(tmp_path / "stations.yaml", table)
    logs: list[str] = []
    reanchor_z_in_file(path, log=logs.append)

    got = {s.id: s for s in load_stations(path)}
    assert got["S201"].trim_z == 0.0
    assert got["S201"].trim_note == ""            # 只描述 trim_z 的来历 → 一起清
    assert any("S201" in line and "trim_z" in line for line in logs)


def test_reanchor_keeps_the_note_that_still_explains_trim_y(tmp_path):
    """trim_note 同时说明 trim_y 的来历时不能清——那就把来历弄丢了。"""
    table = stale_table()
    table[1] = replace(table[1], trim_y=2.0, trim_z=-3.0, trim_note="2026-10-08 framing")
    path = write(tmp_path / "stations.yaml", table)
    logs: list[str] = []
    reanchor_z_in_file(path, log=logs.append)

    got = {s.id: s for s in load_stations(path)}
    assert got["S102"].trim_z == 0.0
    assert got["S102"].trim_y == pytest.approx(2.0)
    assert got["S102"].trim_note == "2026-10-08 framing"
    assert any("trim_note 原样保留" in line for line in logs)


def test_reanchor_is_idempotent(tmp_path):
    """第二次跑应当一个站位都不改——否则会让人以为"上一次没生效"。"""
    path = write(tmp_path / "stations.yaml", stale_table())
    assert reanchor_z_in_file(path, log=lambda _m: None) == GRID_LAYERS * 2
    assert reanchor_z_in_file(path, log=lambda _m: None) == 0


def test_reanchor_refuses_tables_without_layer(tmp_path):
    """没有层号就无从按层重算：报错并列出来，而不是跳过（跳过＝悄悄少改几个站）。"""
    table = stale_table() + [Station(id="SX", box_id="BX", y=0.0, z=-57.0, layer=0, col=1)]
    path = write(tmp_path / "stations.yaml", table)
    with pytest.raises(ValueError, match="没有层号"):
        reanchor_z_in_file(path, log=lambda _m: None)
    assert load_stations(path)[-1].id == "SX"      # 没落盘


def test_reanchor_refuses_an_empty_table(tmp_path):
    path = write(tmp_path / "empty.yaml", [])
    with pytest.raises(ValueError, match="没有站位"):
        reanchor_z_in_file(path, log=lambda _m: None)


# ---------- CLI ----------


def test_cli_dry_run_touches_nothing(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", stale_table())
    assert main(["z-anchor", "--stations", path, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "原文件未改动" in out
    assert "第 1 层" in out and "z=-40.0" in out
    assert load_stations(path)[0].z == pytest.approx(OLD_ANCHOR_Z[1]), "预演绝不落盘"


def test_cli_apply_makes_a_backup(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", stale_table())
    assert main(["z-anchor", "--stations", path]) == 0
    out = capsys.readouterr().out
    assert "已备份" in out and "不需要重新回零" in out
    backups = list(tmp_path.glob("stations.yaml.bak-z-anchor-*"))
    assert len(backups) == 1
    assert load_stations(str(backups[0]))[0].z == pytest.approx(OLD_ANCHOR_Z[1])
    assert load_stations(path)[0].z == pytest.approx(NEW_ANCHOR_Z[1])


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main(["z-anchor", "--stations", str(tmp_path / "nope.yaml")]) == 2
    assert "找不到站位表" in capsys.readouterr().err
