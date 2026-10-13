"""重锚（`stations.reanchor_in_file` + `patrol-migrate reanchor`）。

为什么要有这条路：改 `GRID_*` 常量**只影响新生成的表**，库房里那份
`configs/stations.yaml` 会继续按旧锚点跑——"改了没生效"就是这么来的
（`station-retarget.md` §2 记着这个坑）。所以用户一定锚点（2026-10-10 层心
−40/−90/−145/−195；2026-10-12 第 8 列右移 40mm）必须有一条"刷已有表"的固定动作：
先看 diff、再落盘、落盘前备份。

这些测试盯五件事：`(y, z)` 按当前锚点重算、**只清零锚点真动了的那条轴的 trim**、
单角度档下归一角度标签、id/相机字段不被牵连、缺层号框号的表被拒（而不是悄悄少改几个站）。
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
    col_y,
    load_stations,
    reanchor_in_file,
    save_stations,
)

# 2026-10-10 之前的层心（Z 框架迁移当天的值）
OLD_ANCHOR_Z = {1: -57.0, 2: -80.0, 3: -152.0, 4: -202.0}


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


def test_reanchor_rewrites_both_axes_to_the_current_anchors(tmp_path):
    path = write(tmp_path / "stations.yaml", stale_table())
    moved = reanchor_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].z == pytest.approx(-40.0)
    assert got["S401"].z == pytest.approx(-195.0)
    assert got["S101"].y == pytest.approx(col_y(1, layer=1))
    assert got["S202"].y == pytest.approx(col_y(2, layer=2))
    assert moved == GRID_LAYERS * 2


def test_reanchored_table_matches_a_freshly_built_grid(tmp_path):
    """刷完之后必须与"重新生成一张表"逐点一致——代码与数据重新说同一件事。"""
    path = write(tmp_path / "stations.yaml", stale_table())
    reanchor_in_file(path, log=lambda _m: None)

    got = {(s.layer, s.col): s for s in load_stations(path)}
    for fresh in build_grid():
        if (fresh.layer, fresh.col) in got:
            assert got[(fresh.layer, fresh.col)].y == pytest.approx(fresh.y)
            assert got[(fresh.layer, fresh.col)].z == pytest.approx(fresh.z)


def test_reanchor_keeps_ids_camera_and_exposure(tmp_path):
    """id / 相机 / 曝光是示教与现场调出来的，重锚不许碰。"""
    table = stale_table()
    table[0] = replace(table[0], camera_ip="192.168.1.231", camera_user="ops",
                       exposure_note="locked", imaging_profile="p20261013a")
    path = write(tmp_path / "stations.yaml", table)
    reanchor_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].id == "S101" and got["S101"].box_id == "B101"
    assert got["S101"].camera_ip == "192.168.1.231"
    assert got["S101"].camera_user == "ops" and got["S101"].exposure_note == "locked"
    # 成像参数快照 id 同理：重锚只动坐标与 trim，不许碰取证字段（票 11）
    assert got["S101"].imaging_profile == "p20261013a"


def test_only_the_axis_that_moved_gets_its_trim_cleared(tmp_path):
    """**这条是本次改动的核心**：trim 是相对旧锚点学到的，锚点没动的轴上它仍然有效。

    真实场景：只把第 8 列右移 40mm。其余 47 站的 `trim_y` 是现场一点一点挪出来的
    有效标定，没理由被一次列偏移调整清零。
    """
    table = build_grid()                      # 已经是当前锚点
    table = [replace(s, y=s.y - 40.0) if s.col == 8 else s for s in table]   # 第 8 列退回旧位置
    table = [replace(s, trim_y=2.0) for s in table]           # 每站都学过一点横向微调
    path = write(tmp_path / "stations.yaml", table)
    logs: list[str] = []
    moved = reanchor_in_file(path, log=logs.append)

    got = {(s.layer, s.col): s for s in load_stations(path)}
    # 第 8 列：y 动了 ⇒ trim_y 作废
    assert got[(1, 8)].y == pytest.approx(col_y(8, layer=1))
    assert got[(1, 8)].trim_y == 0.0
    assert any("S108" in ln and "trim_y" in ln for ln in logs)
    # 其余列：y 没动 ⇒ trim_y 原样保留
    assert got[(1, 7)].trim_y == pytest.approx(2.0)
    assert got[(4, 12)].trim_y == pytest.approx(2.0)
    assert moved == 4, "第 8 列只有 4 个站位（每层一个），只有它们该被改"


def test_trim_survives_when_its_own_anchor_did_not_move(tmp_path):
    """反方向：只动 z 时，`trim_y` 与 `y` 都不该被碰。"""
    table = build_grid()
    table = [replace(s, z=s.z + 5.0) for s in table]          # 全部 z 偏离当前锚点
    table = [replace(s, trim_y=1.5, trim_z=-2.0) for s in table]
    path = write(tmp_path / "stations.yaml", table)
    reanchor_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].z == pytest.approx(-40.0)
    assert got["S101"].trim_z == 0.0, "z 锚点动了 ⇒ trim_z 作废"
    assert got["S101"].trim_y == pytest.approx(1.5), "y 锚点没动 ⇒ trim_y 保留"
    assert got["S101"].y == pytest.approx(col_y(1, layer=1))


def test_reanchor_zeroes_trim_z_learned_against_the_old_anchor(tmp_path):
    """`trim_z` 的语义是"叠在锚点上的小量"——锚点一换它就失去意义，留着会推偏新锚点。"""
    path = write(tmp_path / "stations.yaml", stale_table())
    logs: list[str] = []
    reanchor_in_file(path, log=logs.append)

    got = {s.id: s for s in load_stations(path)}
    assert got["S202"].trim_z == 0.0            # c==2 的站位才学过 trim
    assert got["S202"].trim_note == ""           # 两个 trim 都归零 ⇒ 备注没了指向对象
    assert any("S202" in line and "trim_z" in line for line in logs)


def test_note_survives_while_a_trim_it_explains_is_still_alive(tmp_path):
    """trim_note 还说明着一个活着的 trim 时不能清——那就把来历弄丢了。

    构造：表已经在**当前 y 锚点**上（只有 z 是旧的），于是重锚只动 z ——
    `trim_y` 该活着，那条说明它来历的 note 也该活着。
    """
    table = build_grid()
    table = [replace(s, z=s.z + 5.0) for s in table]          # 只有 z 偏离当前锚点
    table[1] = replace(table[1], trim_y=2.0, trim_z=-3.0, trim_note="2026-10-08 framing")
    path = write(tmp_path / "stations.yaml", table)
    reanchor_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S102"].trim_z == 0.0, "z 锚点动了 ⇒ trim_z 作废"
    assert got["S102"].trim_y == pytest.approx(2.0), "y 锚点没动 ⇒ trim_y 保留"
    assert got["S102"].trim_note == "2026-10-08 framing"


def test_reanchor_is_idempotent(tmp_path):
    """第二次跑应当一个站位都不改——否则会让人以为"上一次没生效"。"""
    path = write(tmp_path / "stations.yaml", stale_table())
    assert reanchor_in_file(path, log=lambda _m: None) == GRID_LAYERS * 2
    assert reanchor_in_file(path, log=lambda _m: None) == 0


def test_reanchor_refuses_tables_without_layer_or_col(tmp_path):
    """没有层号/框号就无从按锚点重算：报错并列出来，而不是跳过（跳过＝悄悄少改几个站）。"""
    table = stale_table() + [Station(id="SX", box_id="BX", y=0.0, z=-57.0, layer=1, col=0)]
    path = write(tmp_path / "stations.yaml", table)
    with pytest.raises(ValueError, match="没有层号/框号"):
        reanchor_in_file(path, log=lambda _m: None)
    assert load_stations(path)[-1].id == "SX"      # 没落盘


def test_reanchor_refuses_an_empty_table(tmp_path):
    path = write(tmp_path / "empty.yaml", [])
    with pytest.raises(ValueError, match="没有站位"):
        reanchor_in_file(path, log=lambda _m: None)


def test_reanchor_normalises_a_stale_angle_label(tmp_path):
    """角度档是**标签**，必须和现场支架一致。"""
    table = stale_table()
    table[0] = replace(table[0], angle_profile="top45")
    path = write(tmp_path / "stations.yaml", table)
    logs: list[str] = []
    reanchor_in_file(path, log=logs.append)

    got = {s.id: s for s in load_stations(path)}
    assert {s.angle_profile for s in got.values()} == {DEFAULT_ANGLE}
    assert any("S101" in line and "角度档" in line for line in logs)


# ---------- CLI ----------


def test_cli_dry_run_touches_nothing(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", stale_table())
    assert main(["reanchor", "--stations", path, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "原文件未改动" in out
    assert "y=" in out and "z=" in out
    assert load_stations(path)[0].z == pytest.approx(OLD_ANCHOR_Z[1]), "预演绝不落盘"


def test_cli_apply_makes_a_backup(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", stale_table())
    assert main(["reanchor", "--stations", path]) == 0
    out = capsys.readouterr().out
    assert "已备份" in out and "不需要重新回零" in out
    backups = list(tmp_path.glob("stations.yaml.bak-reanchor-*"))
    assert len(backups) == 1
    assert load_stations(str(backups[0]))[0].z == pytest.approx(OLD_ANCHOR_Z[1])
    assert load_stations(path)[0].z == pytest.approx(-40.0)


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main(["reanchor", "--stations", str(tmp_path / "nope.yaml")]) == 2
    assert "找不到站位表" in capsys.readouterr().err
