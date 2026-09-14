#!/usr/bin/env python3
"""巡检速度整定：控制器频率上界 + 整轮时长（按 ``Fmc4030.goto`` 真实时序建模）。

    python .scratch/prod-deploy/speed-tune.py            # 默认打印对照表
    python .scratch/prod-deploy/speed-tune.py 150 20 1.0 # 指定 Y/Z 速度与单站采图耗时

**为什么要脚本而不是文档里一行字**——`motor-command-review.md` §2.5 的"一轮时长下界 ≈ 86 s"
只算了**一层**的横向空程（4118 mm），而蛇形遍历 5 层每层都要横着走满一遍：真实 Y 空程是
5 × 4118 = 20.8 m。口径错 5 倍，直接把"提速值不值得"的结论带偏了。模型固化下来可重跑。

另含 ``--buggy`` 复现**改造前 ``goto`` 的两段限值颠倒**（长段喂接近档、最后 5 mm 喂全速档）
对整轮时长的放大倍数，便于回归时对照。
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace

sys.path.insert(0, "patrol/src")

from patrol.fmc.geometry import approach_point, composite_limits, segment_delta  # noqa: E402
from patrol.motion_profile import M1, MotionProfile  # noqa: E402
from patrol.stations import GRID_LAYERS, build_grid  # noqa: E402

# 实测（2026-09-12 prod 读回）：控制器脉冲当量 = 细分 / 导程
PPMM = 100_000 / 95                   # = 1052.632 脉冲/mm（Y/Z 同值）
CONTROLLER_MAX_HZ = 200_000           # 说明书 §一：各轴输出频率高达 200 kHz


def pulses_per_s(v_mm_s: float) -> float:
    return v_mm_s * PPMM


def freq_ceiling() -> float:
    """控制器频率上界折算的速度硬上界（与机械无关）。"""
    return CONTROLLER_MAX_HZ / PPMM


def trapezoid_time(distance: float, speed: float, acc: float) -> float:
    """梯形/三角速度曲线的运动耗时（mm, mm/s, mm/s² → s）。"""
    if distance <= 0:
        return 0.0
    ramp = speed * speed / acc
    if distance >= ramp:
        return distance / speed + speed / acc
    return 2.0 * math.sqrt(distance / acc)


def station_dwell(profile: MotionProfile, capture_s: float) -> float:
    """单站固定开销：机械振动衰减 + 灯稳定 + 采图 + 灯后冗余。"""
    return profile.decay_s + profile.lamp_settle_s + capture_s + profile.lamp_after_s


def _leg(delta, limits, buggy: bool, tag: str) -> tuple[float, float]:
    v, a = composite_limits(delta, limits)
    return trapezoid_time(math.hypot(*delta), v, a), math.hypot(*delta)


def round_time(profile: MotionProfile, capture_s: float = 1.0, buggy: bool = False) -> dict:
    """整轮（原点 → 蛇形走完 60 站位）耗时分解。

    ``buggy=True`` 复现改造前 `goto`：**起点→mid 用接近档，mid→目标 用巡检档**。
    """
    pos = (0.0, 0.0)                       # 回零落点 = 原点
    stats = {"motion": 0.0, "y": 0.0, "z": 0.0, "y_span": 0.0, "z_span": 0.0, "stations": 0}
    for st in build_grid(profile):
        target = (st.y, st.z)
        mid = approach_point(pos, target, profile.approach_offset)
        delta = segment_delta(pos, target)
        stats["y_span"] += abs(delta[0])
        stats["z_span"] += abs(delta[1])
        stats["stations"] += 1
        legs = [
            (segment_delta(pos, mid), profile.approach_limits if buggy else profile.travel_limits),
            (segment_delta(mid, target), profile.travel_limits if buggy else profile.approach_limits),
        ]
        for d, limits in legs:
            if math.hypot(*d) <= 0:
                continue
            t, _ = _leg(d, limits, buggy, "")
            stats["motion"] += t
            axis = "y" if abs(d[0]) >= abs(d[1]) else "z"
            stats[axis] += t
        pos = target
    stats["dwell"] = stats["stations"] * station_dwell(profile, capture_s)
    stats["total"] = stats["motion"] + stats["dwell"]
    return stats


def show(stats: dict, label: str = "") -> None:
    print(f"  {label:<22} 运动 {stats['motion']:7.1f} s "
          f"(Y {stats['y']:7.1f} / Z {stats['z']:5.1f})  "
          f"固定开销 {stats['dwell']:6.1f} s  整轮 {stats['total']:7.1f} s = "
          f"{stats['total'] / 60:5.2f} min")


def tuned(y_speed: float, z_speed: float) -> MotionProfile:
    return replace(M1,
                   y=replace(M1.y, travel_speed=y_speed, travel_acc=y_speed * 10.0),
                   z=replace(M1.z, travel_speed=z_speed, travel_acc=z_speed * 10.0))


def main() -> None:
    y_speed = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
    z_speed = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    capture_s = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

    print("=" * 92)
    print("一、控制器侧速度硬上界（与机械无关）")
    print(f"  脉冲当量 = 细分/导程 = 100000/95 = {PPMM:.3f} 脉冲/mm")
    print(f"  控制器上限频率 = {CONTROLLER_MAX_HZ / 1000:.0f} kHz/轴（说明书 §一）⇒ 硬上界 {freq_ceiling():.1f} mm/s")
    print("  各速度占用的脉冲率：" + "  ".join(
        f"{v}→{pulses_per_s(v) / 1000:.0f}kHz({pulses_per_s(v) / CONTROLLER_MAX_HZ:.0%})"
        for v in (20, 50, 90, 120, 150, 190)))
    print()

    base = round_time(M1, capture_s)
    print("=" * 92)
    print("二、整轮时长口径（60 站位 = 5 层 × 12 框，蛇形，换层只动 Z）")
    print(f"  Y 累计空程 {base['y_span']:.0f} mm = {GRID_LAYERS} 层 × 11 框 × 374.33 mm"
          f"   ← 上轮 86 s 口径漏乘的就是这个 ×{GRID_LAYERS}")
    print(f"  Z 累计空程 {base['z_span']:.0f} mm")
    print(f"  单站固定开销 {station_dwell(M1, capture_s):.2f} s "
          f"= 衰减 {M1.decay_s} + 灯稳 {M1.lamp_settle_s} + 采图 {capture_s} + 灯后 {M1.lamp_after_s}")
    print()
    show(round_time(M1, capture_s, buggy=True), "改造前 goto（颠倒）")
    show(round_time(M1, capture_s), "修正后（保持 Y50/Z20）")
    print(f"  ⇒ 单纯修正两段限值颠倒：整轮缩短 "
          f"{(1 - round_time(M1, capture_s)['total'] / round_time(M1, capture_s, buggy=True)['total']):.0%}")
    print()

    print("=" * 92)
    print("三、修正后各速度的整轮时长（Z 保持 20 mm/s，仅动 Y）")
    print(f"  {'Y速度':>7} {'脉冲率':>8} {'占上限':>7} {'整轮(s)':>9} {'整轮(min)':>10}")
    print("  " + "-" * 46)
    for v in (50, 90, 120, 150, 190):
        r = round_time(replace(M1, y=replace(M1.y, travel_speed=float(v), travel_acc=float(v) * 10)),
                       capture_s)
        print(f"  {v:>5} {pulses_per_s(v) / 1000:>7.0f}k {pulses_per_s(v) / CONTROLLER_MAX_HZ:>7.0%}"
              f" {r['total']:>9.1f} {r['total'] / 60:>10.2f}")
    print()

    print("=" * 92)
    print(f"四、选定值：Y {y_speed:.0f} / acc {y_speed * 10:.0f}、Z {z_speed:.0f} / acc {z_speed * 10:.0f} mm/s")
    chosen = tuned(y_speed, z_speed)
    show(round_time(chosen, capture_s), "选定")
    show(round_time(M1, capture_s, buggy=True), "对比改造前")
    print()


if __name__ == "__main__":
    main()
