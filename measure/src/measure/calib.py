"""标定换算：像素 → mm（票 05）。

每个站位一次棋盘格标定得到 mm/px（spec §5.1）；本模块提供纯数学换算与
菌盖直径的圆拟合（Kasa 代数拟合，无需 cv2）。
"""

from __future__ import annotations

import numpy as np


def fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Kasa 圆拟合：最小二乘解 (cx, cy, r)。

    x² + y² = 2·cx·x + 2·cy·y + c，r = sqrt(cx² + cy² + c)
    """
    if len(points) < 3:
        raise ValueError("圆拟合至少需要 3 个点")
    a = np.asarray(points, dtype=float)
    x, y = a[:, 0], a[:, 1]
    a_mat = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b_vec = x**2 + y**2
    (cx, cy, c), *_ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
    r2 = cx * cx + cy * cy + c
    if r2 < 0:
        raise ValueError("拟合退化：点位不构成圆")
    return float(cx), float(cy), float(np.sqrt(r2))


def cap_diameter_mm(cap_contour_px: list[tuple[float, float]], mm_per_px: float) -> float:
    """由菌盖轮廓像素点求直径（mm）。"""
    _, _, r = fit_circle(cap_contour_px)
    return 2.0 * r * mm_per_px


def apply_scale(points_px: list[tuple[float, float]], mm_per_px: float) -> list[tuple[float, float]]:
    return [(x * mm_per_px, y * mm_per_px) for x, y in points_px]


def stipe_length_mm(base_px: tuple[float, float], tip_px: tuple[float, float],
                    mm_per_px: float, view_angle_deg: float = 45.0) -> float:
    """斜拍视角下的菇体长度：投影长度 / sin(视角)。

    顶拍（0°）不适用本函数（长度方向不可见）；45° 斜拍为默认档位。
    """
    if view_angle_deg <= 0 or view_angle_deg >= 90:
        raise ValueError("视角需在 (0, 90) 度之间")
    dx = (tip_px[0] - base_px[0]) * mm_per_px
    dy = (tip_px[1] - base_px[1]) * mm_per_px
    projected = float(np.hypot(dx, dy))
    return projected / float(np.sin(np.radians(view_angle_deg)))
