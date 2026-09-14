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
    """单朵菇的度量结果（mm）。len/cap 允许为 None（该档位未拍到）。"""

    box_id: str
    len_mm: float | None = None
    cap_mm: float | None = None
    quality_ok: bool = True
    flags: str = ""


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


def aggregate_box(detections: list[Detection]) -> BoxStats:
    """单框统计：质量不合格的检出剔除，不入统计（spec §5.2）。"""
    if not detections:
        return BoxStats(box_id="")
    used = [d for d in detections if d.quality_ok]
    lens = [d.len_mm for d in used if d.len_mm is not None]
    caps = [d.cap_mm for d in used if d.cap_mm is not None]
    flags = sorted({f for d in detections if not d.quality_ok for f in d.flags.split(",") if f})

    def stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
        if not values:
            return None, None, None
        arr = np.asarray(values)
        return (float(arr.mean()), float(np.percentile(arr, 10)),
                float(np.percentile(arr, 90)))

    mean_len, p10, p90 = stats(lens)
    mean_cap, _, _ = stats(caps)
    return BoxStats(
        box_id=detections[0].box_id,
        n_total=len(detections),
        n_used=len(used),
        mean_len_mm=mean_len,
        mean_cap_mm=mean_cap,
        p10_len_mm=p10,
        p90_len_mm=p90,
        quality_flags=",".join(flags),
    )


def aggregate_round(detections: list[Detection]) -> dict[str, BoxStats]:
    """一轮巡检的多框聚合，按 box_id 分组。"""
    groups: dict[str, list[Detection]] = {}
    for d in detections:
        groups.setdefault(d.box_id, []).append(d)
    return {box: aggregate_box(dets) for box, dets in groups.items()}
