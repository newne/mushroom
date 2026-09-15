"""Z 坐标框架镜像的一次性迁移（ADR-0018）。

这是**动生产配置**的操作，测试要盯住两件事：换算对不对（物理位置不变）、以及
"重复执行会不会把整张表搬出行程"——后者是这类脚本最典型的翻车方式。
"""

from __future__ import annotations

import pytest
from patrol.migrate import main
from patrol.stations import (
    OLD_Z_MIN,
    Station,
    load_stations,
    mirror_z_in_file,
    save_stations,
)

OLD = [
    Station(id="S101", box_id="B101", y=187.1, z=-21.2, layer=1, col=1),
    Station(id="S505", box_id="B505", y=2000.0, z=-190.8, layer=5, col=5,
            trim_z=2.4, trim_y=-1.0),
]

def write(path, stations):
    save_stations(stations, str(path))
    return str(path)


def test_mirror_keeps_the_physical_position(tmp_path):
    """−21.2（旧框架：顶端往下 21.2）→ +21.2（新框架：同一个点）。"""
    path = write(tmp_path / "stations.yaml", OLD)
    mirror_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].z == pytest.approx(21.2)
    assert got["S505"].z == pytest.approx(190.8)         # 旧 −190.8 = 底层层心
    # trim 是同一坐标方向上的小偏移：坐标轴镜像了，偏移量要跟着取反
    assert got["S505"].trim_z == pytest.approx(-2.4)
    assert got["S505"].trim_y == pytest.approx(-1.0), "Y 不动"
    for s in got.values():
        assert 0.0 <= s.z <= 212.0


def test_migrated_table_matches_the_new_grid(tmp_path):
    """迁移后的坐标必须与**新网格推导出来的坐标逐点相同**。

    这条是整件事的验收判据：代码（`layer_z`）与数据（`stations.yaml`）在迁移后必须说
    同一件事——第 1 层在最上（z 最小）、第 5 层在最下。用"加 212"而不是"取反"就会把
    第 1 层送到第 5 层的位置，而这条测试会当场发现。
    """
    from patrol.stations import GRID_LAYERS, build_grid, layer_z

    old_layer_z = lambda n: 0.0 - (n - 0.5) * (212.0 / GRID_LAYERS)
    old_grid = [
        Station(id=f"S1{c:02d}", box_id=f"B1{c:02d}", y=100.0 * c, layer=1, col=c,
                z=old_layer_z(1))
        for c in range(1, 3)
    ] + [
        Station(id=f"S5{c:02d}", box_id=f"B5{c:02d}", y=100.0 * c, layer=5, col=c,
                z=old_layer_z(GRID_LAYERS))
        for c in range(1, 3)
    ]
    assert old_grid[0].z == pytest.approx(-21.2) and old_grid[2].z == pytest.approx(-190.8)

    path = write(tmp_path / "stations.yaml", old_grid)
    mirror_z_in_file(path, log=lambda _m: None)

    got = {s.id: s.z for s in load_stations(path)}
    assert got["S101"] == pytest.approx(layer_z(1))
    assert got["S501"] == pytest.approx(layer_z(GRID_LAYERS))
    new = {s.id: s.z for s in build_grid() if s.id in got}
    assert got == pytest.approx(new)


def test_mirror_is_idempotent_by_refusing_to_run_twice(tmp_path):
    """第二次执行必须**报错**而不是再挪 212mm——那会把整个网格搬出行程。"""
    path = write(tmp_path / "stations.yaml", OLD)
    mirror_z_in_file(path, log=lambda _m: None)
    with pytest.raises(ValueError, match="已经是新框架"):
        mirror_z_in_file(path, log=lambda _m: None)

    got = {s.id: s for s in load_stations(path)}
    assert got["S101"].z == pytest.approx(21.2), "拒绝之后一个字节都不该改"


def test_mirror_can_write_elsewhere_and_leaves_the_source_alone(tmp_path):
    src = write(tmp_path / "stations.yaml", OLD)
    out = str(tmp_path / "new.yaml")
    mirror_z_in_file(src, out=out, log=lambda _m: None)
    assert load_stations(src)[0].z == pytest.approx(-21.2)
    assert load_stations(out)[0].z == pytest.approx(21.2)


def test_mirror_rejects_an_empty_table(tmp_path):
    path = write(tmp_path / "empty.yaml", [])
    with pytest.raises(ValueError, match="没有站位"):
        mirror_z_in_file(path, log=lambda _m: None)


def test_old_frame_span_still_matches_the_profile():
    """迁移的前提是"跨度没变、只是原点换了边"。跨度变了就该重新推导，而不是套这个脚本。"""
    from patrol.motion_profile import M1

    assert abs(OLD_Z_MIN) == M1.z.travel_span == 212.0


# ---------- CLI ----------


def test_cli_dry_run_touches_nothing(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", OLD)
    assert main(["z-frame", "--stations", path, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "没有改动" in out or "未改动" in out
    assert load_stations(path)[0].z == pytest.approx(-21.2), "预演绝不落盘"


def test_cli_apply_makes_a_backup(tmp_path, capsys):
    path = write(tmp_path / "stations.yaml", OLD)
    assert main(["z-frame", "--stations", path]) == 0
    out = capsys.readouterr().out
    assert "已备份" in out and "必须先回零" in out
    backups = list(tmp_path.glob("stations.yaml.bak-z-frame-*"))
    assert len(backups) == 1
    assert load_stations(str(backups[0]))[0].z == pytest.approx(-21.2)
    assert load_stations(path)[0].z == pytest.approx(21.2)


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main(["z-frame", "--stations", str(tmp_path / "nope.yaml")]) == 2
    assert "找不到站位表" in capsys.readouterr().err
