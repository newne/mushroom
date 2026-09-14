"""两轴巡检运动几何（纯函数，可独立测试）。

本机两轴为 Y、Z，``Point`` 即 ``(y, z)``（虚拟坐标系坐标，单位 mm）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]

# 方向余弦小于该阈值视为该轴不参与本次运动（纯单轴运动时另一轴取不到倒数）
_MIN_SHARE = 1e-9


def approach_point(prev: Point, target: Point, offset: float) -> Point:
    """从 prev 前往 target 的"接近段"终点：距 target 沿来向退 offset mm。

    prev 与 target 重合时直接返回 target（无需接近段）。
    """
    dy = target[0] - prev[0]
    dz = target[1] - prev[1]
    dist = math.hypot(dy, dz)
    if dist < 1e-9 or offset <= 0 or offset >= dist:
        # 无需接近段：重合、零偏移，或剩余行程比接近段还短
        return target
    return (target[0] - dy / dist * offset, target[1] - dz / dist * offset)


def segment_delta(start: Point, end: Point) -> Point:
    return (end[0] - start[0], end[1] - start[1])


def composite_limits(
    delta: Point,
    axis_limits: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """把各轴的速度/加速度上限折算成插补的**合成**限值。

    ``FMC4030_Line_2Axis`` 的 speed/acc/dec 是合成量（厂商手册：不代表各轴实际
    速度/加速度），各轴的实际分量 = 合成量 × 该轴在运动方向上的方向余弦。要让任
    一根轴都不越过自己的上限，取::

        v_comp = min_i ( v_i / |cos θ_i| )

    - 纯单轴运动退化为该轴自身上限（另一轴方向余弦为 0，不参与取 min）；
    - 长轴主导的行程仍能跑满长轴速度，不会被短轴拖慢（这是直接取 min(v_i) 的错处：
      4492 mm 的 Y 行程只要掺进 1 mm 的 Z 分量就会被压到 Z 的速度）；
    - 任意运动方向下都不会让某根轴超速。

    ``axis_limits`` 与 ``delta`` 按同一顺序（本机即 (Y, Z)）给出，每项是
    ``(速度上限, 加速度上限)``。
    """
    dist = math.hypot(delta[0], delta[1])
    limits = list(zip(delta, axis_limits, strict=True))
    if dist < _MIN_SHARE:
        # 零位移：没有方向可言，保守取各轴上限的最小值
        return (min(v for v, _ in axis_limits), min(a for _, a in axis_limits))

    speed = math.inf
    acc = math.inf
    for d, (v_limit, a_limit) in limits:
        share = abs(d) / dist
        if share < _MIN_SHARE:
            continue
        speed = min(speed, v_limit / share)
        acc = min(acc, a_limit / share)
    # 理论上 dist>0 必有一轴 share>0；兜底避免 inf 漏出
    if math.isinf(speed):
        speed = min(v for v, _ in axis_limits)
    if math.isinf(acc):
        acc = min(a for _, a in axis_limits)
    return (speed, acc)
