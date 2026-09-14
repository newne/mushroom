"""分割几何量计算与像素-物理尺度转换。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class MeasurementResult:
    """单个目标测量结果。"""

    area_px: int
    area_mm2: float
    major_axis_px: float
    minor_axis_px: float
    major_axis_mm: float
    minor_axis_mm: float


@dataclass(frozen=True)
class MushroomSizeMetrics:
    """单帧蘑菇生长评估指标。"""

    stem_length_mm: float
    stem_diameter_mm: float
    cap_diameter_mm: float
    stem_area_mm2: float
    cap_area_mm2: float


@dataclass(frozen=True)
class BagReference:
    """菌袋真实尺寸（毫米）。"""

    width_mm: float
    height_mm: float


def _safe_ratio(numerator: float, denominator: float, eps: float = 1e-6) -> float:
    return float(numerator / max(denominator, eps))


def estimate_mm_per_pixel_from_bag(
    bag_mask: np.ndarray,
    bag_reference: BagReference,
) -> float:
    """根据菌袋 mask 估计毫米/像素换算比例。

    策略：
    1. 基于 mask 外接矩形计算宽高像素值
    2. 分别估计 x/y 方向比例
    3. 返回两者平均值，降低轻微透视误差
    """
    if bag_mask.ndim != 2:
        raise ValueError("bag_mask 必须是二维数组")

    ys, xs = np.where(bag_mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        raise ValueError("bag_mask 为空，无法估计尺度")

    width_px = float(xs.max() - xs.min() + 1)
    height_px = float(ys.max() - ys.min() + 1)

    mm_per_px_x = _safe_ratio(bag_reference.width_mm, width_px)
    mm_per_px_y = _safe_ratio(bag_reference.height_mm, height_px)
    return (mm_per_px_x + mm_per_px_y) * 0.5


def _mask_measurement(mask: np.ndarray, mm_per_px: float) -> MeasurementResult:
    ys, xs = np.where(mask > 0)
    if len(xs) < 5:
        return MeasurementResult(
            area_px=0,
            area_mm2=0.0,
            major_axis_px=0.0,
            minor_axis_px=0.0,
            major_axis_mm=0.0,
            minor_axis_mm=0.0,
        )

    area_px = int(len(xs))
    x_center = float(xs.mean())
    y_center = float(ys.mean())

    centered = np.column_stack([xs - x_center, ys - y_center]).astype(np.float64)
    cov = np.cov(centered.T)
    eigvals, _ = np.linalg.eigh(cov)
    eigvals = np.sort(np.maximum(eigvals, 0.0))[::-1]

    # 利用二阶矩近似椭圆主轴长度（2*sqrt(4*lambda)）
    major_axis_px = (
        float(2.0 * math.sqrt(4.0 * eigvals[0])) if eigvals.size > 0 else 0.0
    )
    minor_axis_px = (
        float(2.0 * math.sqrt(4.0 * eigvals[1])) if eigvals.size > 1 else 0.0
    )

    area_mm2 = float(area_px * (mm_per_px**2))
    return MeasurementResult(
        area_px=area_px,
        area_mm2=area_mm2,
        major_axis_px=major_axis_px,
        minor_axis_px=minor_axis_px,
        major_axis_mm=major_axis_px * mm_per_px,
        minor_axis_mm=minor_axis_px * mm_per_px,
    )


def extract_mushroom_size_metrics(
    stem_masks: Iterable[np.ndarray],
    cap_masks: Iterable[np.ndarray],
    mm_per_px: float,
) -> MushroomSizeMetrics:
    """从菌杆/菌帽实例 mask 集合计算平均物理尺寸。"""
    stem_measures = [_mask_measurement(mask, mm_per_px) for mask in stem_masks]
    cap_measures = [_mask_measurement(mask, mm_per_px) for mask in cap_masks]

    valid_stems = [m for m in stem_measures if m.area_px > 0]
    valid_caps = [m for m in cap_measures if m.area_px > 0]

    if not valid_stems and not valid_caps:
        return MushroomSizeMetrics(0.0, 0.0, 0.0, 0.0, 0.0)

    stem_length_mm = (
        float(np.mean([m.major_axis_mm for m in valid_stems])) if valid_stems else 0.0
    )
    stem_diameter_mm = (
        float(np.mean([m.minor_axis_mm for m in valid_stems])) if valid_stems else 0.0
    )
    cap_diameter_mm = (
        float(np.mean([m.major_axis_mm for m in valid_caps])) if valid_caps else 0.0
    )
    stem_area_mm2 = (
        float(np.mean([m.area_mm2 for m in valid_stems])) if valid_stems else 0.0
    )
    cap_area_mm2 = (
        float(np.mean([m.area_mm2 for m in valid_caps])) if valid_caps else 0.0
    )

    return MushroomSizeMetrics(
        stem_length_mm=stem_length_mm,
        stem_diameter_mm=stem_diameter_mm,
        cap_diameter_mm=cap_diameter_mm,
        stem_area_mm2=stem_area_mm2,
        cap_area_mm2=cap_area_mm2,
    )


def growth_rate(
    current_value: float, previous_value: float, delta_hours: float
) -> float:
    """计算单位小时增长速度。"""
    if delta_hours <= 0:
        return 0.0
    return (current_value - previous_value) / delta_hours
