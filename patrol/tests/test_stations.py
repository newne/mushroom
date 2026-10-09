"""站位网格（12 框 × 4 层）的几何与遍历序。"""

from __future__ import annotations

from dataclasses import replace

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
    nearest_station,
    retarget,
)


def test_grid_size_is_cols_times_layers_times_angles():
    st = build_grid()
    assert len(st) == GRID_COLS * GRID_LAYERS == 48
    assert len({s.id for s in st}) == 48
    assert len({s.box_id for s in st}) == 48      # 每框 1 个站位
    assert {s.angle_profile for s in st} == {"top45"}


def test_station_ids_read_as_layer_and_col():
    st = {(s.layer, s.col): s for s in build_grid()}
    assert st[(1, 1)].id == "S101" and st[(1, 1)].box_id == "B101"
    assert st[(4, 12)].id == "S412" and st[(4, 12)].box_id == "B412"
    assert st[(4, 12)].cell == "4-12"


def test_layer_one_is_at_the_top_and_layer_four_at_the_bottom():
    """Z 原点在顶端、Z **向下为正**（ADR-0018）：第 1 层最靠近 0，层号越大 z 越大。"""
    assert [layer_z(layer) for layer in range(1, GRID_LAYERS + 1)] == pytest.approx(
        [10.0, 60.0, 132.0, 155.0]
    )
    assert layer_z(1) < layer_z(2) < layer_z(GRID_LAYERS)


def test_calibrated_layers_stay_inside_the_z_travel():
    zs = [layer_z(i) for i in range(1, GRID_LAYERS + 1)]
    assert M1.z.travel_min < zs[0] and zs[-1] < M1.z.travel_max


def test_layer_z_refuses_to_leave_the_travel_range():
    """层是从**原点那一侧**排下去的：若有人把原点配到行程的另一端（正限位回零），
    这里要当场喊出来，而不是算出一串越界坐标——ADR-0018 的镜像错就是这么来的。"""
    from dataclasses import replace

    from patrol.motion_profile import HOME_DIR_POSITIVE

    flipped = replace(M1, z=replace(M1.z, home_dir=HOME_DIR_POSITIVE))   # 原点跑到 212
    with pytest.raises(ValueError, match="靠近电机"):
        layer_z(1, flipped)


def test_columns_keep_the_nominal_pitch_with_calibrated_c08_offset():
    ys = [col_y(c) for c in range(1, GRID_COLS + 1)]
    nominal = [
        M1.y.travel_min + (col - 0.5) * (M1.y.travel_span / GRID_COLS)
        for col in range(1, GRID_COLS + 1)
    ]
    expected = [y - 80.0 if col == 8 else y for col, y in enumerate(nominal, start=1)]
    assert ys == pytest.approx(expected)
    assert all(M1.y.contains(y) for y in ys)


def test_c08_layer_two_offset_is_used_by_grid_and_retarget():
    nominal_c08 = M1.y.travel_min + 7.5 * (M1.y.travel_span / GRID_COLS)
    stations = {(s.layer, s.col): s for s in build_grid()}
    assert stations[(1, 8)].y == pytest.approx(nominal_c08 - 80.0)
    assert stations[(2, 8)].y == pytest.approx(nominal_c08 - 40.0)
    assert stations[(4, 8)].y == pytest.approx(nominal_c08 - 80.0)

    stale = [replace(stations[key], y=0.0) for key in ((2, 8), (4, 8))]
    updated = {(s.layer, s.col): s for s in retarget(stale)}
    assert updated[(2, 8)].y == pytest.approx(nominal_c08 - 40.0)
    assert updated[(4, 8)].y == pytest.approx(nominal_c08 - 80.0)


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
    # 换层处 Y 几乎不动，而不是从最右折回最左
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
    assert geom["z_pitch"] == pytest.approx(145 / 3)
    assert (geom["y_min"], geom["y_max"]) == (0.0, 4492.0)
    assert (geom["z_min"], geom["z_max"]) == (0.0, 212.0)


def test_angles_dimension_can_be_reopened_without_touching_coordinates():
    """把双档加回来只是多一个角度档，坐标与框号不变。"""
    two = build_grid(angles=("top0", "top45"))
    assert len(two) == GRID_COLS * GRID_LAYERS * 2
    assert len({s.box_id for s in two}) == 48
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



# ---------- 最近站位（非格点抓拍的归属） ----------


def test_nearest_is_the_station_itself_when_exactly_on_it():
    st = build_grid()
    assert nearest_station(st, col_y(1), layer_z(1)).id == "S101"
    assert nearest_station(st, col_y(12), layer_z(4)).id == "S412"


def test_nearest_is_none_on_an_empty_table():
    """站位表空 ⇒ 没有归属可言。调用方要自己决定是拒绝还是报错，不能给个假的。"""
    assert nearest_station([], 0.0, 0.0) is None


def test_nearest_switches_layer_at_half_a_layer():
    """层号边界落在相邻实测层心的中点：这是"这个点属于哪一层"的那条线。

    Z 的平均格距约 50mm，而 Y 的框距有 374mm —— 判错一层的代价是拍到隔壁层的菇，
    所以边界必须钉死，不能"大概往上靠"。
    """
    st = build_grid()
    z1, z2 = layer_z(1), layer_z(2)
    assert nearest_station(st, col_y(1), z1 + 0.4 * (z2 - z1)).id == "S101"   # 半层以内
    assert nearest_station(st, col_y(1), z1 + 0.6 * (z2 - z1)).id == "S201"   # 过半即换层


def test_nearest_compares_in_grid_units_not_raw_millimetres():
    """两个轴的偏差要**各自按格距归一化**后再比，不能直接比毫米。

    构造一对必然分歧的候选：A 同 Y、偏 Z 50mm；B 同 Z、偏 Y 60mm。
    - 比毫米：A(50) < B(60) ⇒ 选 A
    - 比格数：A 偏了 50/50 = 1 层，B 只偏 60/374.3 ≈ 0.16 框 ⇒ 选 B

    B 才对：偏了整整一层意味着镜头压根不在那一层，而 Y 上偏 60mm 连半个框都不到。
    直接比毫米的话，Y 的 374mm 格距会把 Z 的平均 50mm 格距完全盖住（见 nearest_station 注释）。
    """
    py = grid_geometry()["y_pitch"]
    pz = grid_geometry()["z_pitch"]
    dy_b, dz_a = 60.0, 50.0
    probe_y, probe_z = 1000.0, 100.0
    same_y = Station(id="A", box_id="BA", y=probe_y, z=probe_z - dz_a)     # dy=0, dz=dz_a
    same_z = Station(id="B", box_id="BB", y=probe_y - dy_b, z=probe_z)     # dy=dy_b, dz=0
    assert dz_a < dy_b and dy_b / py < dz_a / pz        # 前提：两种口径确实分歧
    assert nearest_station([same_y, same_z], probe_y, probe_z).id == "B"


def test_nearest_covers_the_far_corner():
    """整场任意合法位置都能找到归属：这是"非格点也能拍"的前提（没有"够不着"的点）。"""
    st = build_grid()
    assert nearest_station(st, col_y(12) + 100.0, layer_z(3)).id == "S312"
    assert nearest_station(st, 0.0, 0.0).id == "S101"        # 原点角


