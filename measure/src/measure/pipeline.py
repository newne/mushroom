"""检出与按框聚合（票 05）。

Detector 协议把"图像 → 单朵菇度量"与聚合解耦：
- 真实模型（分割/检测）后续接入，实现本协议即可；
- DummyDetector 供测试与管线联调。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Detection:
    """单朵菇/单簇区域的度量结果（mm）。len/cap 允许为 None（该档位未拍到）。"""

    box_id: str
    len_mm: float | None = None
    cap_mm: float | None = None
    quality_ok: bool = True
    flags: str = ""
    #: 区域形态度量（像素口径，由 baseline 检出器填充；见 measure.baseline.BlobMetrics）。
    #: 默认 None 是为了不逼任何既有实现（含测试 stub）去构造它。
    metrics: object | None = None
    #: 本次换算用的 mm/px（站位标定量），便于回溯"这行数字怎么来的"。
    mm_per_px: float | None = None


class Detector(Protocol):
    def detect(self, image: np.ndarray, *, mm_per_px: float, box_id: str) -> list[Detection]: ...


class DummyDetector:
    """联调占位：为每帧返回固定数量的预设度量。"""

    def __init__(self, *, n: int = 3, len_mm: float = 60.0, cap_mm: float = 40.0):
        self._template = [Detection(box_id="", len_mm=len_mm, cap_mm=cap_mm) for _ in range(n)]

    def detect(self, image, *, mm_per_px: float, box_id: str) -> list[Detection]:
        out = []
        for d in self._template:
            out.append(Detection(box_id=box_id, len_mm=d.len_mm, cap_mm=d.cap_mm))
        return out


@dataclass
class BoxStats:
    box_id: str
    n_total: int = 0
    n_used: int = 0
    mean_len_mm: float | None = None
    mean_cap_mm: float | None = None
    p10_len_mm: float | None = None
    p90_len_mm: float | None = None
    quality_flags: str = ""
    #: 菌簇范围（票 11：measurements 现有 8 列放不下，先由 aggregate 侧承载，等环境官
    #: 决定落库形态）。单位 mm / mm²。
    mean_cluster_w_mm: float | None = None
    mean_cluster_h_mm: float | None = None
    cluster_w_min_mm: float | None = None
    cluster_w_max_mm: float | None = None
    mean_area_mm2: float | None = None
    mean_projected_d_mm: float | None = None


def aggregate_box(detections: list[Detection]) -> BoxStats:
    """单框统计：质量不合格的检出剔除，不入统计（spec §5.2）。

    ``n_total`` / ``n_used`` 是**实例数**，不是"可用朵数"——朵数是否可信要看
    quality 里的 `cluster_merged`/`no_seed`（见 `measure.contract`）。
    """
    if not detections:
        return BoxStats(box_id="")

    def stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
        if not values:
            return None, None, None
        arr = np.asarray(values)
        return (float(arr.mean()), float(np.percentile(arr, 10)),
                float(np.percentile(arr, 90)))

    used = [d for d in detections if d.quality_ok]
    lens = [d.len_mm for d in used if d.len_mm is not None]
    caps = [d.cap_mm for d in used if d.cap_mm is not None]
    flags = sorted({f for d in detections if not d.quality_ok for f in d.flags.split(",") if f})
    mean_len, p10, p90 = stats(lens)
    mean_cap, _, _ = stats(caps)

    def metric_of(d: Detection, name: str) -> float | None:
        m = getattr(d, "metrics", None)
        return None if m is None else getattr(m, name, None)

    def to_mm(values: list[float | None], mm_per_px: float | None) -> list[float]:
        if mm_per_px is None:
            return []
        return [float(v) * mm_per_px for v in values if v is not None]

    per_box_scale = next((getattr(d, "mm_per_px", None) for d in used
                          if getattr(d, "mm_per_px", None)), None)
    widths = to_mm([metric_of(d, "cluster_w_px") for d in used], per_box_scale)
    heights = to_mm([metric_of(d, "cluster_h_px") for d in used], per_box_scale)
    px_areas = [metric_of(d, "area_px") for d in used]
    eq_ds = to_mm([metric_of(d, "eq_diameter_px") for d in used], per_box_scale)
    # 面积是**二次量**：单边换算一次（mm/px）再乘以同一条换算，才是 mm²
    areas = [float(v) * per_box_scale * per_box_scale
             for v in px_areas if v is not None and per_box_scale] if per_box_scale else []
    return BoxStats(
        box_id=detections[0].box_id,
        n_total=len(detections),
        n_used=len(used),
        mean_len_mm=mean_len,
        mean_cap_mm=mean_cap,
        p10_len_mm=p10,
        p90_len_mm=p90,
        quality_flags=",".join(flags),
        mean_cluster_w_mm=None if not widths else float(np.mean(widths)),
        mean_cluster_h_mm=None if not heights else float(np.mean(heights)),
        cluster_w_min_mm=None if not widths else float(min(widths)),
        cluster_w_max_mm=None if not widths else float(max(widths)),
        mean_area_mm2=None if not areas else float(np.mean(areas)),
        mean_projected_d_mm=None if not eq_ds else float(np.mean(eq_ds)),
    )


def aggregate_round(detections: list[Detection]) -> dict[str, BoxStats]:
    """一轮巡检的多框聚合，按 box_id 分组。"""
    groups: dict[str, list[Detection]] = {}
    for d in detections:
        groups.setdefault(d.box_id, []).append(d)
    return {box: aggregate_box(dets) for box, dets in groups.items()}
