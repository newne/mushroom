"""站位网格（12 框 × 5 层）的几何与遍历序。"""

from __future__ import annotations

import pytest
from patrol.motion_profile import M1
from patrol.stations import (
    CAMERA_IP,
    GRID_COLS,
    GRID_LAYERS,
    Station,
    build_grid,
    col_y,
    fill_camera_ip,
    grid_geometry,
    layer_z,
)


def test_grid_size_is_cols_times_layers_times_angles():
    st = build_grid()
    assert len(st) == GRID_COLS * GRID_LAYERS == 60
    assert len({s.id for s in st}) == 60
    assert len({s.box_id for s in st}) == 60      # 每框 1 个站位
    assert {s.angle_profile for s in st} == {"top45"}


def test_station_ids_read_as_layer_and_col():
    st = {(s.layer, s.col): s for s in build_grid()}
    assert st[(1, 1)].id == "S101" and st[(1, 1)].box_id == "B101"
    assert st[(5, 12)].id == "S512" and st[(5, 12)].box_id == "B512"
    assert st[(5, 12)].cell == "5-12"


def test_layer_one_is_at_the_top_and_layer_five_at_the_bottom():
    """Z 原点在顶端、Z 向下为负：第 1 层必须最靠近 0。"""
    assert layer_z(1) == pytest.approx(-42.4 / 2)
    assert layer_z(GRID_LAYERS) == pytest.approx(-212 + 42.4 / 2)
    assert layer_z(1) > layer_z(2) > layer_z(GRID_LAYERS)


def test_layers_evenly_split_the_whole_z_travel():
    zs = [layer_z(i) for i in range(1, GRID_LAYERS + 1)]
    gaps = [zs[i] - zs[i + 1] for i in range(len(zs) - 1)]
    assert all(g == pytest.approx(212 / GRID_LAYERS) for g in gaps)
    # 首末层各留半个层距的余量，既不贴顶也不贴底
    assert M1.z.travel_min < zs[-1] and zs[0] < M1.z.travel_max


def test_cols_evenly_split_the_whole_y_travel():
    ys = [col_y(c) for c in range(1, GRID_COLS + 1)]
    assert ys[0] > M1.y.travel_min and ys[-1] < M1.y.travel_max
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    assert all(g == pytest.approx(4492 / GRID_COLS) for g in gaps)


@pytest.mark.parametrize("bad", [0, -1, GRID_LAYERS + 1])
def test_layer_out_of_range_rejected(bad):
    with pytest.raises(ValueError, match="层号越界"):
        layer_z(bad)


@pytest.mark.parametrize("bad", [0, -1, GRID_COLS + 1])
def test_col_out_of_range_rejected(bad):
    with pytest.raises(ValueError, match="框序号越界"):
        col_y(bad)


def test_visit_order_is_serpentine_so_the_return_trip_is_free():
    """蛇形遍历：同层内单调走，相邻两层方向相反——换层不产生 Y 空程。"""
    st = build_grid()
    ys = [s.y for s in st]
    layer1 = [s for s in st if s.layer == 1]
    layer2 = [s for s in st if s.layer == 2]
    assert [s.col for s in layer1] == list(range(1, GRID_COLS + 1))
    assert [s.col for s in layer2] == list(range(GRID_COLS, 0, -1))
    # 换层处 Y 几乎不动（只有 Z 的 42.4mm），而不是从最右折回最左
    assert layer2[0].y == pytest.approx(layer1[-1].y)
    assert max(ys) - min(ys) == pytest.approx(4492 * (GRID_COLS - 1) / GRID_COLS)


def test_every_station_is_inside_the_travel_envelope():
    for s in build_grid():
        assert M1.y.contains(s.y)
        assert M1.z.contains(s.z)


def test_grid_geometry_matches_the_builder():
    geom = grid_geometry()
    assert geom["cols"] == GRID_COLS and geom["layers"] == GRID_LAYERS
    assert geom["y_pitch"] == pytest.approx(4492 / GRID_COLS)
    assert geom["z_pitch"] == pytest.approx(212 / GRID_LAYERS)
    assert (geom["y_min"], geom["y_max"]) == (0.0, 4492.0)
    assert (geom["z_min"], geom["z_max"]) == (-212.0, 0.0)


def test_angles_dimension_can_be_reopened_without_touching_coordinates():
    """把双档加回来只是多一个角度档，坐标与框号不变。"""
    two = build_grid(angles=("top0", "top45"))
    assert len(two) == GRID_COLS * GRID_LAYERS * 2
    assert len({s.box_id for s in two}) == 60
    assert {s.id for s in two if s.layer == 1 and s.col == 1} == {"S101-top0", "S101-top45"}


def test_camera_ip_hook_is_optional():
    st = build_grid(camera_ip_of=lambda layer, col: f"192.168.1.{layer * 100 + col}")
    assert st[0].camera_ip == "192.168.1.101"
    # 不传 hook 时填**全局唯一相机**（J4：全场只有一台，不再是空串）
    assert build_grid()[0].camera_ip == CAMERA_IP


# ---------- 相机的全局默认（示教产物与手写表共用一条通路） ----------


def test_fill_camera_ip_only_touches_empty_rows():
    """只补空的：显式写了 IP 的站位保留，分机位的能力不被剥夺。"""
    stations = [
        Station(id="S101", box_id="B101", y=0.0, z=0.0, camera_ip=""),
        Station(id="S102", box_id="B102", y=1.0, z=0.0, camera_ip="192.168.1.231"),
    ]
    filled, n = fill_camera_ip(stations, "192.168.1.238")
    assert n == 1
    assert [s.camera_ip for s in filled] == ["192.168.1.238", "192.168.1.231"]


def test_fill_camera_ip_leaves_complete_table_untouched():
    stations = build_grid()
    filled, n = fill_camera_ip(stations)
    assert n == 0
    assert filled == stations

