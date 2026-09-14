"""运动参数单源的不变式（ADR-0007）。

这里断言的不是"数字抄得对"，而是**现场参数之间的几何一致性**——它们本可以用
几个独立常量表达，但那样就无法互相校验：

- 两轴的原点都落在各自回零方向的行程端点上，且该端点必须是 0；
- 行程从原点**单侧**展开，因此坐标符号自带方向（Y 增大向右、Z 减小向下）；
- 两轴插补掩码由轴号派生（Y+Z=0x06），改接线不会漏改；
- M1/M2 共用同一组轴参数，只差灯窗（ADR-0002）。
"""

from __future__ import annotations

import pytest
from patrol.fmc.geometry import composite_limits
from patrol.motion_profile import (
    CONTROLLER_IP,
    CONTROLLER_PORT,
    HOME_DIR_NEGATIVE,
    HOME_DIR_POSITIVE,
    M1,
    M2,
)

# ---------- 轴接线与寻址 ----------


def test_axes_are_y_and_z_with_controller_indices():
    assert (M1.y.name, M1.y.index) == ("Y", 1)
    assert (M1.z.name, M1.z.index) == ("Z", 2)
    assert M1.axis_indices == (1, 2)


def test_axis_mask_is_y_plus_z():
    """厂商手册：0x03=X+Y、0x05=X+Z、0x06=Y+Z。"""
    assert M1.axis_mask == 0x06


def test_controller_endpoint():
    assert CONTROLLER_IP == "192.168.1.239"
    assert CONTROLLER_PORT == 8088  # 出厂默认端口


# ---------- 行程与原点的几何一致性 ----------


def test_y_travel_is_horizontal_and_origin_at_the_left_end():
    assert (M1.y.travel_min, M1.y.travel_max) == (0.0, 4492.0)
    assert M1.y.home_dir == HOME_DIR_NEGATIVE     # 反向回零
    assert M1.y.home_position == 0.0              # 回零落点即原点


def test_z_travel_is_vertical_and_origin_at_the_top():
    """向上为正：正限位回零落在行程上限，上限即原点 0，向下走到 -212。"""
    assert (M1.z.travel_min, M1.z.travel_max) == (-212.0, 0.0)
    assert M1.z.home_dir == HOME_DIR_POSITIVE     # 向上回零
    assert M1.z.home_position == 0.0


def test_origin_is_the_homing_position_on_both_axes():
    """「Y 反向回零、Z 向上回零的位置为原点」——两轴都不例外。"""
    for spec in M1.axes:
        assert spec.home_position == 0.0
        assert spec.contains(0.0)


def test_direction_semantics_follow_from_the_origin():
    """行程单侧展开 → 符号即方向。"""
    assert M1.y.contains(4492.0) and not M1.y.contains(-1.0)   # Y 只能向正（右）
    assert M1.z.contains(-212.0) and not M1.z.contains(1.0)    # Z 只能向负（下）


@pytest.mark.parametrize(
    ("spec_name", "inside", "outside"),
    [("y", 3000.0, 5000.0), ("z", -100.0, -300.0)],
)
def test_contains_rejects_anything_beyond_travel(spec_name, inside, outside):
    spec = getattr(M1, spec_name)
    assert spec.contains(inside)
    assert not spec.contains(outside)


# ---------- 速度/加减速与派生档 ----------


def test_on_site_speed_and_accel_values():
    """巡检档 2026-09-12 整定：Y 50→150（控制器脉冲上界内 + 改造前现场值），
    Z 保持 20（每轮只有 4 次换层动作，提速收益 <5 s，不值得动竖直轴）。"""
    assert (M1.y.travel_speed, M1.y.travel_acc) == (150.0, 1500.0)
    assert (M1.z.travel_speed, M1.z.travel_acc) == (20.0, 200.0)


def test_homing_values():
    """回零速度 2026-09-12 下调（Y 150→90、Z 50→20），见下面的余量约束测试。"""
    assert (M1.y.home_speed, M1.y.home_acc, M1.y.home_release) == (90.0, 900.0, 5.0)
    assert (M1.z.home_speed, M1.z.home_acc, M1.z.home_release) == (20.0, 200.0, 5.0)


# 现场实测的控制器回零超时（device_para.homeTime，2026-09-12 从 192.168.1.239 读回）
CONTROLLER_HOME_TIME_S = {"Y": 100.0, "Z": 100.0}
HOME_MARGIN = 2.0


def test_home_speed_keeps_margin_under_controller_home_timeout():
    """回零速度有**下界**，不是越慢越好。

    回零是「从行程内任意位置出发去找固定的限位开关」，最坏起点在行程另一端，
    所以寻零时间按**全行程**估。一旦超过控制器 ``homeTime``，控制器会中止回零并置位
    ``MACHINE_HOME_OVERTIME``——后果是**原点不可信**（``HomeTimeoutError``），
    不是"慢一点"。故要求留 ``HOME_MARGIN`` 倍余量，且同样落在主机侧兜底之内。
    """
    for spec in M1.axes:
        worst_case_s = spec.travel_span / spec.home_speed
        budget = CONTROLLER_HOME_TIME_S[spec.name]
        assert worst_case_s * HOME_MARGIN <= budget, (
            f"{spec.name} 轴最坏寻零 {worst_case_s:.1f}s × {HOME_MARGIN} > "
            f"控制器 homeTime {budget:.0f}s——回零会随机超时"
        )
        assert worst_case_s < M1.home_timeout, f"{spec.name} 轴超出主机侧 home_timeout"


def test_derived_speeds_never_exceed_the_axis_rating():
    for spec in M1.axes:
        assert 0 < spec.approach_speed <= spec.travel_speed
        assert 0 < spec.jog_speed <= spec.travel_speed
        assert 0 < spec.approach_acc <= spec.travel_acc
        assert 0 < spec.jog_acc <= spec.travel_acc


def test_derived_ladder_matches_the_pre_migration_tuning():
    """改造前整机的档位阶梯：巡检 150 / 接近 30 / 点动 10（acc 1500 / 300 / 100）。

    巡检档回到 150 之后，接近档按 0.2 比例自然落在 30（= 改造前值）；**点动档不行**——
    0.2 会给到 30 mm/s，示教时肉眼跟不上，所以 `_Y.jog_ratio` 显式取 10/150。
    这个测试就是那条"改巡检档必须同时想想点动"的守卫。
    """
    assert M1.y.travel_speed == pytest.approx(150.0)
    assert M1.y.approach_speed == pytest.approx(30.0)
    assert M1.y.jog_speed == pytest.approx(10.0)
    assert M1.y.jog_acc == pytest.approx(100.0)


# 控制器侧速度上界（与机械无关）：实测脉冲当量 + 说明书 §一 的 200 kHz/轴
PULSES_PER_MM = 100_000 / 95          # 细分/导程，2026-09-12 从 192.168.1.239 读回
CONTROLLER_MAX_HZ = 200_000


def test_travel_speed_stays_under_controller_pulse_ceiling():
    """巡检档不能顶到控制器的脉冲频率上界——顶到了不是"跑不动"，是控制器**发不出**
    这么多脉冲，位置会整体走短（静默失位）。

    留 15% 余量：驱动器最大输入频率至今未知（见 `motor-command-review.md` §2.2），
    控制器能发不等于驱动器收得下，余量是留给这一层的。
    """
    ceiling = CONTROLLER_MAX_HZ / PULSES_PER_MM          # ≈ 190 mm/s
    for spec in M1.axes:
        hz = spec.travel_speed * PULSES_PER_MM
        assert hz <= CONTROLLER_MAX_HZ, (
            f"{spec.name} 巡检档 {spec.travel_speed} mm/s = {hz / 1000:.0f} kHz，"
            f"超过控制器上限 {CONTROLLER_MAX_HZ / 1000:.0f} kHz"
        )
    assert M1.y.travel_speed <= 0.85 * ceiling, (
        f"Y 巡检档 {M1.y.travel_speed} mm/s 离控制器上界 {ceiling:.1f} mm/s 太近"
        f"（需 ≤ {0.85 * ceiling:.1f}）"
    )


# ---------- M1 / M2 的单源关系（ADR-0002） ----------


def test_m1_and_m2_share_axis_profiles_and_differ_only_in_lamp_window():
    assert M1.y is M2.y and M1.z is M2.z
    assert (M1.lamp_hold_s, M2.lamp_hold_s) == (None, 1.0)


# ---------- 查表 ----------


def test_by_index_and_by_name():
    assert M1.by_index(1) is M1.y
    assert M1.by_name("z") is M1.z
    with pytest.raises(KeyError):
        M1.by_index(0)          # X 未接线
    with pytest.raises(KeyError):
        M1.by_name("X")


# ---------- 合成量折算（插补速度是合成量） ----------


def test_composite_degrades_to_the_single_axis_rating():
    (v, a) = composite_limits((300.0, 0.0), M1.travel_limits)
    assert (v, a) == (M1.y.travel_speed, M1.y.travel_acc)     # 纯 Y
    (v, a) = composite_limits((0.0, -100.0), M1.travel_limits)
    assert (v, a) == (M1.z.travel_speed, M1.z.travel_acc)     # 纯 Z


def test_composite_does_not_drag_the_long_axis_down():
    """4500 mm 的 Y 行程掺进 100 mm 的 Z 分量，仍应按 Y 的上限走。"""
    (v, _) = composite_limits((4500.0, -100.0), M1.travel_limits)
    assert v == pytest.approx(M1.y.travel_speed * (4500.0**2 + 100.0**2) ** 0.5 / 4500.0)
    assert v > M1.y.travel_speed


def test_composite_never_lets_any_axis_exceed_its_rating():
    for delta in ((1.0, -1.0), (100.0, -1.0), (1.0, -100.0), (4492.0, -212.0)):
        v, a = composite_limits(delta, M1.travel_limits)
        dist = (delta[0] ** 2 + delta[1] ** 2) ** 0.5
        assert v * abs(delta[0]) / dist <= M1.y.travel_speed + 1e-9
        assert v * abs(delta[1]) / dist <= M1.z.travel_speed + 1e-9
        assert a * abs(delta[0]) / dist <= M1.y.travel_acc + 1e-9
        assert a * abs(delta[1]) / dist <= M1.z.travel_acc + 1e-9


def test_composite_handles_zero_displacement():
    assert composite_limits((0.0, 0.0), M1.travel_limits) == (20.0, 200.0)
