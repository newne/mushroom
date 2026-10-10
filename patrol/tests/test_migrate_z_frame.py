"""Z 坐标框架迁移：原点搬到顶端、第 1 层改到最上（2026-10-10）。

这是**动生产配置**的操作，测试要盯住两件事：换算对不对（第 1 层 ↔ 最上面那层、
坐标与层号一起翻），以及"重复执行会不会把整张表搬出行程"——后者是这类脚本最典型的
翻车方式。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from patrol.migrate import main
from patrol.stations import (
    GRID_LAYERS,
    Station,
    build_grid,
    layer_z,
    load_stations,
    retarget_z_in_file,
    save_stations,
)

# 真实旧表：底端原点、向上为正，层心 10 / 60 / 132 / 155（第 1 层在最下面）。
OLD_Z = {1: 10.0, 2: 60.0, 3: 132.0, 4: 155.0}


def old_table():
    return [
        Station(id=f"S{L}{c:02d}", box_id=f"B{L}{c:02d}", y=100.0 * c, z=OLD_Z[L],
                layer=L, col=c)
        for L in range(1, GRID_LAYERS + 1) for c in (1, 2)
    ]


def write(path, stations):
    save_stations(stations, str(path))
    return str(path)


def test_migration_flips_layer_one_to_the_top(tmp_path):
    """旧 10（底层层心）→ layer_z(1)（新：最上层心 −57）；第 4 层落到最下 −202。"""
    path = write(tmp_path / "stations.yaml", old_table())
    retarget_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].z == pytest.approx(-57.0)
    assert got["S201"].z == pytest.approx(-80.0)
    assert got["S301"].z == pytest.approx(-152.0)
    assert got["S401"].z == pytest.approx(-202.0)
    # 层号越大 ⇒ 离顶端原点越远 ⇒ z 越小（更负）
    assert layer_z(1) > layer_z(2) > layer_z(GRID_LAYERS)
    for s in got.values():
        assert -212.0 <= s.z <= 0.0


def test_migrated_table_matches_the_fresh_grid(tmp_path):
    """迁移后的坐标必须与 `build_grid()` 逐点相同——代码与数据在迁移后说同一件事。"""
    path = write(tmp_path / "stations.yaml", old_table())
    retarget_z_in_file(path, log=lambda _m: None)

    got = {s.id: s.z for s in load_stations(path)}
    fresh = {s.id: s.z for s in build_grid() if s.id in got}
    assert got == pytest.approx(fresh)


def test_migration_keeps_y_ids_and_trims(tmp_path):
    table = old_table()
    table[0] = replace(table[0], trim_y=1.5, trim_z=-2.0)
    path = write(tmp_path / "stations.yaml", table)
    retarget_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert set(got) == {s.id for s in table}
    assert got["S101"].y == pytest.approx(100.0)         # Y 不动
    assert got["S101"].trim_y == pytest.approx(1.5)      # Y 方向 trim 原样保留
    assert got["S101"].trim_z == pytest.approx(-2.0), "迁移只改 z 的框架，不重算 trim"


def test_running_twice_is_refused(tmp_path):
    """第二次执行必须**报错**而不是再算一遍——那会让人以为"上一次没生效"。"""
    path = write(tmp_path / "stations.yaml", old_table())
    retarget_z_in_file(path, log=lambda _m: None)
    with pytest.raises(ValueError, match="已经是新框架"):
        retarget_z_in_file(path, log=lambda _m: None)


def test_a_table_in_the_wrong_range_is_refused(tmp_path):
    bad = old_table() + [Station(id="SX", box_id="BX", y=0.0, z=999.0, layer=1, col=1)]
    path = write(tmp_path / "stations.yaml", bad)
    with pytest.raises(ValueError, match="超出旧行程"):
        retarget_z_in_file(path, log=lambda _m: None)


def test_empty_table_is_refused(tmp_path):
    path = write(tmp_path / "empty.yaml", [])
    with pytest.raises(ValueError, match="没有站位"):
        retarget_z_in_file(path, log=lambda _m: None)


# ---------- CLI ----------


def test_cli_dry_run_touches_nothing(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", old_table())
    assert main(["z-frame", "--stations", path, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "未改动" in out
    assert load_stations(path)[0].z == pytest.approx(10.0), "预演绝不落盘"


def test_cli_apply_makes_a_backup(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", old_table())
    assert main(["z-frame", "--stations", path]) == 0
    out = capsys.readouterr().out
    assert "已备份" in out and "必须先回零" in out
    backups = list(tmp_path.glob("stations.yaml.bak-z-frame-*"))
    assert len(backups) == 1
    assert load_stations(str(backups[0]))[0].z == pytest.approx(10.0)
    assert load_stations(path)[0].z == pytest.approx(-57.0)


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main(["z-frame", "--stations", str(tmp_path / "nope.yaml")]) == 2
    assert "找不到站位表" in capsys.readouterr().err
