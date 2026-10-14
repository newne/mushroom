"""观察高度档（票 16）：阶段 → 逐层 Z 偏置、fail-closed 守门、重锚写回。

用户 2026-10-14 口径："菌杆长出菌袋表面后，保持与蘑菇平行的视角"。可做的那一半是
**视轴高度**（纯代码）：层锚点上叠一个逐层 Δz。视轴**方向**是机械量，不在本模块管。

这些测试盯六件事：
1. 默认档 `surface` = 全 0 ⇒ 行为与引入这一维之前**一字不差**；
2. 档名未定义 ⇒ 报错（不许当 0、不许猜值）；
3. 相位窗 ±25mm 与"落点必须在行程内"两条硬门 ⇒ 越界报错；
4. 顶层 `view_stage` 的读/写/往返，以及 `save_stations` 不把档静默还原；
5. `reanchor --view-stage` 换档：z 跟着走、**trim_z 清、trim_y 留**、幂等；
6. `stage_consistency` 能把"表里写着某档、坐标却是别的档"的站点列出来。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml
from patrol.migrate import main
from patrol.stations import (
    DEFAULT_VIEW_STAGE,
    GRID_LAYERS,
    GRID_VIEW_STAGE_Z_OFFSETS,
    MAX_VIEW_STAGE_OFFSET_MM,
    build_grid,
    declared_view_stage,
    layer_z,
    load_station_table,
    load_stations,
    reanchor_in_file,
    save_stations,
    stage_consistency,
    view_stage_z_offsets,
)

#: 一个"测试用"的假档：每层偏置不同，正好能验证**逐层**而不是整体平移。
FAKE_STAGE = "test-stage"
FAKE_OFFSETS = (10.0, 0.0, -10.0, 5.0)


@pytest.fixture
def fake_stage(monkeypatch):
    """把一个假档塞进档表——**只存在于测试进程**，不落进仓库常量。"""
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, FAKE_STAGE, FAKE_OFFSETS)
    return FAKE_STAGE


def write(path, stations, *, view_stage=None):
    save_stations(stations, str(path), view_stage=view_stage)
    return str(path)


# ---------- 1. 默认档 = 现状 ----------


def test_default_stage_is_zero_and_keeps_the_current_anchors():
    """默认档全 0：`layer_z(L)` 与 `layer_z(L, stage=surface)` 必须一模一样。"""
    assert view_stage_z_offsets(None) == (0.0,) * GRID_LAYERS
    assert DEFAULT_VIEW_STAGE in GRID_VIEW_STAGE_Z_OFFSETS
    assert GRID_VIEW_STAGE_Z_OFFSETS[DEFAULT_VIEW_STAGE] == (0.0,) * GRID_LAYERS

    assert [layer_z(1, stage=None), layer_z(2, stage=None),
            layer_z(3, stage=None), layer_z(4, stage=None)] == pytest.approx(
        [-40.0, -90.0, -145.0, -195.0])
    for layer in range(1, GRID_LAYERS + 1):
        assert layer_z(layer, stage=DEFAULT_VIEW_STAGE) == pytest.approx(layer_z(layer))


# ---------- 2/3. fail-closed 守门 ----------


def test_stage_names_that_were_never_measured_are_rejected():
    """`pin`/`stipe`/`cluster` 是**语义名**，现场没量出相位之前不许被当成 0。"""
    for name in ("stipe", "pin", "cluster", "没量过的档"):
        with pytest.raises(ValueError, match="未定义的观察高度档"):
            view_stage_z_offsets(name)
    with pytest.raises(ValueError, match="未定义的观察高度档"):
        layer_z(1, stage="stipe")


def test_alias_of_the_default_stage_is_still_the_default():
    """空串/空白等价于默认档（表里写了 `view_stage:` 空值不该炸）。"""
    assert view_stage_z_offsets("") == (0.0,) * GRID_LAYERS
    assert view_stage_z_offsets("   ") == (0.0,) * GRID_LAYERS


def test_offset_beyond_the_phase_window_is_rejected(monkeypatch):
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, "too-far", (0.0, 0.0, 0.0, 30.0))
    with pytest.raises(ValueError, match="超过相位窗"):
        layer_z(4, stage="too-far")

    # 边界值本身允许（±25mm 是"硬上限"，不是"必须小于"）
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, "at-edge",
                        (0.0, 0.0, 0.0, MAX_VIEW_STAGE_OFFSET_MM))
    assert layer_z(4, stage="at-edge") == pytest.approx(-195.0 + MAX_VIEW_STAGE_OFFSET_MM)


def test_stage_with_a_wrong_number_of_layers_is_rejected(monkeypatch):
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, "short", (1.0, 2.0))
    with pytest.raises(ValueError, match="要 4 个数"):
        view_stage_z_offsets("short")


def test_offset_that_pushes_a_layer_out_of_travel_is_rejected(monkeypatch):
    """第 4 层向下只剩 17mm（票 16 §3.2）——这条就是那道行程门。"""
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, "更靠下", (0.0, 0.0, 0.0, -20.0))
    with pytest.raises(ValueError, match="不在行程"):
        layer_z(4, stage="更靠下")
    # −17mm 正好落在下限上，允许
    monkeypatch.setitem(GRID_VIEW_STAGE_Z_OFFSETS, "贴下限", (0.0, 0.0, 0.0, -17.0))
    assert layer_z(4, stage="贴下限") == pytest.approx(-212.0)


# ---------- 4. 顶层档名的读写 ----------


def test_offsets_are_applied_per_layer(tmp_path, fake_stage):
    assert layer_z(1, stage=fake_stage) == pytest.approx(-40.0 + 10.0)
    assert layer_z(2, stage=fake_stage) == pytest.approx(-90.0)
    assert layer_z(3, stage=fake_stage) == pytest.approx(-145.0 - 10.0)
    assert layer_z(4, stage=fake_stage) == pytest.approx(-195.0 + 5.0)

    stations = build_grid(view_stage=fake_stage)
    by_layer = {s.layer: s.z for s in stations}
    assert by_layer[1] == pytest.approx(-30.0) and by_layer[4] == pytest.approx(-190.0)


def test_table_without_the_key_reads_as_the_default_stage(tmp_path):
    path = write(tmp_path / "stations.yaml", build_grid())
    stations, stage = load_station_table(path)
    assert stage == DEFAULT_VIEW_STAGE
    assert declared_view_stage(path) == DEFAULT_VIEW_STAGE
    assert len(stations) == len(build_grid())


def test_table_declaring_an_undeclared_stage_is_refused(tmp_path):
    """表里写着一个没定义的档 ⇒ 装载就报错，不猜、不静默按默认档跑。"""
    path = tmp_path / "stations.yaml"
    payload = {"view_stage": "stipe",
               "stations": [{"id": "S101", "box_id": "B101", "y": 100.0, "z": -40.0,
                             "layer": 1, "col": 1}]}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="未定义的观察高度档"):
        load_stations(str(path))


def test_save_keeps_the_declared_stage_when_the_caller_does_not_care(tmp_path, fake_stage):
    """`patrol-teach save` 只关心坐标；保存一次不该把档静默还原成默认档。"""
    path = write(tmp_path / "stations.yaml", build_grid(view_stage=fake_stage),
                 view_stage=fake_stage)
    assert declared_view_stage(path) == fake_stage

    save_stations(load_stations(path), path)          # 不传档 ⇒ 沿用文件里的
    assert declared_view_stage(path) == fake_stage


def test_save_refuses_to_write_a_table_with_an_undeclared_stage(tmp_path):
    with pytest.raises(ValueError, match="未定义的观察高度档"):
        save_stations(build_grid(), str(tmp_path / "stations.yaml"), view_stage="stipe")


# ---------- 5. 重锚换档 ----------


def test_reanchor_applies_the_stage_and_writes_it_back(tmp_path, fake_stage):
    table = build_grid()                              # surface 锚点
    path = write(tmp_path / "stations.yaml", table)
    # 第 2 层的 Δz 是 0 ⇒ 那 12 个站位的 z 没动，其余 36 个动（"只清零真动了的轴"同源）
    moved_expected = sum(1 for s in table if FAKE_OFFSETS[s.layer - 1] != 0.0)

    logs: list[str] = []
    moved = reanchor_in_file(path, stage=fake_stage, log=logs.append)
    assert moved == moved_expected

    got = {s.layer: s for s in load_stations(path)}
    assert got[1].z == pytest.approx(-30.0)
    assert got[3].z == pytest.approx(-155.0)
    assert declared_view_stage(path) == fake_stage
    assert any(fake_stage in line for line in logs)

    # 幂等：同一档再刷一次，0 个改动；**档名不许被还原**
    assert reanchor_in_file(path, log=lambda _m: None) == 0
    assert declared_view_stage(path) == fake_stage
    assert load_stations(path)[0].z == pytest.approx(-30.0)


def test_switching_stage_clears_trim_z_and_keeps_trim_y(tmp_path, fake_stage):
    table = [replace(s, trim_y=2.0, trim_z=-3.0, trim_note="2026-10-08 framing")
             for s in build_grid()]
    path = write(tmp_path / "stations.yaml", table)

    reanchor_in_file(path, stage=fake_stage, log=lambda _m: None)
    got = sorted(load_stations(path), key=lambda s: s.layer)
    assert all(s.trim_y == pytest.approx(2.0) for s in got)     # y 锚点没动 ⇒ 留着
    # z 锚点动了的层清 trim_z；本假档第 2 层 Δz=0（z 没动）⇒ 那层的微调仍然有效
    for s in got:
        if FAKE_OFFSETS[s.layer - 1] != 0.0:
            assert s.trim_z == 0.0
        else:
            assert s.trim_z == pytest.approx(-3.0)


def test_reanchor_refuses_an_unknown_stage_without_touching_the_file(tmp_path):
    path = write(tmp_path / "stations.yaml", build_grid())
    with pytest.raises(ValueError, match="未定义的观察高度档"):
        reanchor_in_file(path, stage="stipe", log=lambda _m: None)
    assert load_stations(path)[0].z == pytest.approx(-40.0)


def test_stage_consistency_lists_stations_whose_z_lies_about_the_stage(tmp_path, fake_stage):
    path = write(tmp_path / "stations.yaml", build_grid())      # 坐标还是 surface 的
    assert stage_consistency(load_stations(path), DEFAULT_VIEW_STAGE) == []

    flagged = stage_consistency(load_stations(path), fake_stage)
    # 只有 Δz≠0 的层算"不一致"；本假档第 2 层 Δz=0 ⇒ 那 12 个站位不报
    expected_ids = {s.id for s in build_grid() if FAKE_OFFSETS[s.layer - 1] != 0.0}
    assert {sid for sid, _z, _want in flagged} == expected_ids
    for sid, z, want in flagged:
        station = next(s for s in build_grid() if s.id == sid)
        assert z == pytest.approx(station.z)                     # 表里的 z 还是 surface 的值
        assert want == pytest.approx(station.z + FAKE_OFFSETS[station.layer - 1])


# ---------- 6. CLI ----------


def test_cli_reanchor_dry_run_does_not_touch_the_file(tmp_path, fake_stage, capsys):
    path = write(tmp_path / "stations.yaml", build_grid())
    rc = main(["reanchor", "--stations", path, "--view-stage", fake_stage, "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "原文件未改动" in out and fake_stage in out
    assert load_stations(path)[0].z == pytest.approx(-40.0)
    assert declared_view_stage(path) == DEFAULT_VIEW_STAGE


def test_cli_reanchor_writes_the_stage_and_backs_up(tmp_path, fake_stage, capsys):
    path = write(tmp_path / "stations.yaml", build_grid())
    rc = main(["reanchor", "--stations", path, "--view-stage", fake_stage])
    capsys.readouterr()
    assert rc == 0
    assert load_stations(path)[0].z == pytest.approx(-30.0)
    assert declared_view_stage(path) == fake_stage
    assert list(tmp_path.glob("stations.yaml.bak-reanchor-*"))
