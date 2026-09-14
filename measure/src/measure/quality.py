"""图像质量门控（票 05）：模糊 / 过曝检测（numpy 实现，不入统计的帧打标）。"""

from __future__ import annotations

import numpy as np


def laplacian_variance(gray: np.ndarray) -> float:
    """拉普拉斯方差——经典清晰度指标，越低越模糊。"""
    g = gray.astype(float)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        raise ValueError("需要 2D 灰度图且尺寸 ≥3×3")
    lap = (-4.0 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def is_blurry(gray: np.ndarray, threshold: float = 100.0) -> bool:
    return laplacian_variance(gray) < threshold


def overexposed_fraction(gray: np.ndarray, level: int = 250) -> float:
    """接近纯白的像素占比。"""
    return float((np.asarray(gray) >= level).mean())


def is_overexposed(gray: np.ndarray, max_fraction: float = 0.05, level: int = 250) -> bool:
    return overexposed_fraction(gray, level) > max_fraction


def frame_quality(gray: np.ndarray, *, blur_threshold: float = 100.0,
                  max_overexposed: float = 0.05) -> tuple[bool, str]:
    """返回 (是否可用, 标记串)；标记串逗号分隔，空串表示通过。"""
    flags = []
    if is_blurry(gray, blur_threshold):
        flags.append("blurry")
    if is_overexposed(gray, max_overexposed):
        flags.append("overexposed")
    return (not flags), ",".join(flags)
