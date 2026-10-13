"""图像质量门控（票 05 / 票 13）：模糊 / 过曝 / 近黑（灯未亮）。

**这一份是帧级可用性的唯一尺度**（Chief 2026-10-13 裁定）：任何别处的"这帧能不能用"
判定都必须对齐本模块，不许造第二套；需要不同阈值就写票据。

## 判据与处置

| 判据 | 含义 | 处置 |
| --- | --- | --- |
| `blurry` | 拉普拉斯方差过低（纹理不足/失焦） | **不可用**（不产测量行） |
| `overexposed` | 接近纯白像素占比过高（加湿期水汽、反光） | **不可用** |
| `no_light` | 整帧近黑（灯未亮/无信息量） | **不可用**——降权会把它放进统计 |

三者的阈值**都不许写成拍脑袋的常数**：当前默认值是占位（沿用既有 100 / 0.05，近黑
0.95），必须用真实历史帧（跨轮次、跨天、跨生长阶段 + 已知坏帧对照）的分布标定，
见票 13。**别拿单张强晕影帧当"近黑"样本**：整屏明暗渐变会让均值判据误判，
所以近黑用"高分位亮度"而不是"均值"。
"""

from __future__ import annotations

import numpy as np

#: 占位阈值（等票 13 用真实帧分布替换；改动必须留回归帧与理由）
DEFAULT_BLUR_THRESHOLD = 100.0
DEFAULT_MAX_OVEREXPOSED = 0.05
#: 近黑：p99 亮度低于此值（[0,1] 归一化）= 整帧几乎没有亮区
DEFAULT_DARK_P99 = 0.12


def _as_unit(gray: np.ndarray) -> np.ndarray:
    """灰度图统一到 [0,1]（uint8 或已是浮点都吃）。"""
    g = np.asarray(gray, dtype=float)
    if g.size and g.max() > 1.5:
        g = g / 255.0
    return g


def laplacian_variance(gray: np.ndarray) -> float:
    """拉普拉斯方差——经典清晰度指标，越低越模糊。"""
    g = gray.astype(float)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        raise ValueError("需要 2D 灰度图且尺寸 ≥3×3")
    lap = (-4.0 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def is_blurry(gray: np.ndarray, threshold: float = DEFAULT_BLUR_THRESHOLD) -> bool:
    return laplacian_variance(gray) < threshold


def overexposed_fraction(gray: np.ndarray, level: int = 250) -> float:
    """接近纯白的像素占比。"""
    return float((np.asarray(gray) >= level).mean())


def is_overexposed(gray: np.ndarray, max_fraction: float = DEFAULT_MAX_OVEREXPOSED,
                   level: int = 250) -> bool:
    return overexposed_fraction(gray, level) > max_fraction


def p99_brightness(gray: np.ndarray) -> float:
    """高分位亮度（[0,1]）：整帧近黑时它也很低。

    为什么不用均值：真机帧常有整屏明暗渐变（S306 实测边缘近黑、中心亮），
    均值会被"边缘黑"拉低，把一张有信息的帧误判成灯没亮。
    """
    g = _as_unit(gray)
    return float(np.percentile(g, 99)) if g.size else 0.0


def is_dark(gray: np.ndarray, *, dark_p99: float = DEFAULT_DARK_P99) -> bool:
    """整帧近黑（灯未亮/无信息量）⇒ 该帧不可用。"""
    return p99_brightness(gray) < dark_p99


def frame_quality(gray: np.ndarray, *, blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
                  max_overexposed: float = DEFAULT_MAX_OVEREXPOSED,
                  dark_p99: float = DEFAULT_DARK_P99) -> tuple[bool, str]:
    """返回 ``(是否可用, 标记串)``；标记串 `+` 分隔（票 11 §3），空串表示通过。"""
    flags = []
    if is_blurry(gray, blur_threshold):
        flags.append("blurry")
    if is_overexposed(gray, max_overexposed):
        flags.append("overexposed")
    if is_dark(gray, dark_p99=dark_p99):
        flags.append("no_light")
    return (not flags), "+".join(flags)
