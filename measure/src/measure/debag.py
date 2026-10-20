"""L2b 去袋：区域指标的唯一合法输入 + `mask_source` 契约。

spec §4.2 的硬前提：`cluster_width_px` / `area_px` / `eq_diameter_px` / `cluster_h_px`
必须在 **`cluster` 掩码减去 `bag` 掩码（含袋面可见部分）之后**的掩码上算。
没去袋的 `cluster_h_px` 量的是"袋 + 簇"，下边界切在袋口 ⇒ **不是簇高**。

本模块只做减法与几何，不做归属、不拆合并实例、不猜袋。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

#: 区域指标必须带 `mask_source`；缺了就是契约违规（不是"默认 cluster_minus_bag"）。
MASK_SOURCES = ("cluster_minus_bag", "cluster_raw", "envelope")


@dataclass(frozen=True)
class DebagedMask:
    mask: np.ndarray
    mask_source: str
    removed_area_ratio: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegionMetrics:
    area_px: int
    bbox_px: tuple[int, int, int, int]   # x0, y0, x1, y1（半开）
    cluster_width_px: int                # 最宽行跨度
    cluster_h_px: int                    # 上下跨度：**仅形态摘要**
    eq_diameter_px: float
    mask_source: str
    removed_area_ratio: float


@dataclass(frozen=True)
class RegionResult:
    metrics: RegionMetrics | None
    mask_source: str
    reasons: tuple[str, ...] = ()


def debag(cluster_mask, bag_masks) -> DebagedMask:
    """`cluster_mask \\ bag_masks`（多袋取并集）。缺袋掩码 ⇒ 不伪造去袋结果。"""
    cluster = np.asarray(cluster_mask, dtype=bool)
    masks = [np.asarray(m, dtype=bool) for m in (bag_masks or []) if m is not None]
    if not masks:
        return DebagedMask(cluster, "cluster_raw", 0.0, ("bag_mask_missing",))

    for m in masks:
        if m.shape != cluster.shape:
            raise ValueError(f"bag 掩码形状 {m.shape} 与 cluster {cluster.shape} 不一致")

    bag_union = np.zeros_like(cluster)
    for m in masks:
        bag_union |= m
    clean = cluster & ~bag_union

    raw_area = int(cluster.sum())
    removed = 1.0 - (int(clean.sum()) / raw_area) if raw_area else 0.0
    reasons = () if clean.any() else ("no_region_after_debag",)
    return DebagedMask(clean, "cluster_minus_bag", removed, reasons)


def region_metrics(mask, *, mask_source: str) -> RegionMetrics | None:
    """去袋后（或显式标注来源的）掩码 → 区域指标；空掩码返回 None。"""
    if mask_source not in MASK_SOURCES:
        raise ValueError(f"mask_source 必须是 {MASK_SOURCES} 之一，实际 {mask_source!r}")
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return None

    ys, xs = np.nonzero(m)
    area = int(m.sum())
    widest = 0
    for y in np.unique(ys):
        row = np.flatnonzero(m[y])
        widest = max(widest, int(row[-1] - row[0] + 1))

    return RegionMetrics(
        area_px=area,
        bbox_px=(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
        cluster_width_px=widest,
        cluster_h_px=int(ys.max() - ys.min() + 1),
        eq_diameter_px=float(2.0 * np.sqrt(area / np.pi)),
        mask_source=mask_source,
        removed_area_ratio=0.0,
    )


def measure_region(cluster_mask, bag_masks) -> RegionResult:
    """一袋一簇的合法区域指标入口。

    - 没有 bag 掩码 ⇒ **区域指标 NULL**（`bag_mask_missing`）；高度不受影响（那是票 13 的事）；
    - 去袋后为空 ⇒ NULL + `no_region_after_debag`；
    - 多袋合并到同一 cluster ⇒ 本模块**不拆**，由上游标 `merged_multiple_bags`。
    """
    d = debag(cluster_mask, bag_masks)
    if d.mask_source != "cluster_minus_bag":
        return RegionResult(None, d.mask_source, d.reasons)

    metrics = region_metrics(d.mask, mask_source=d.mask_source)
    if metrics is None:
        return RegionResult(None, d.mask_source, d.reasons or ("no_region_after_debag",))
    metrics = replace(metrics, removed_area_ratio=d.removed_area_ratio)
    reasons = tuple(r for r in d.reasons if r != "no_region_after_debag")
    return RegionResult(metrics, d.mask_source, reasons)
