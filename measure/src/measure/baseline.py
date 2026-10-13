"""baseline 检出器：无模型线的"最小可用测量"。

## 它是什么 / 不是什么

`EUPEProxyPredictor`（`src/scripts/processing/run_segmentation_inference.py`）已有一条
启发式分割线：HSV 阈值 + 距离变换 + 形态学，产出 4 类语义 logits（bg/bag/stem/cap）。
但那套代码跑在 **torch + cv2** 上，而测量生产者在库房主机（`patrol` 侧）只有
numpy + Pillow；且它输出的是**语义**掩膜，不是本模块要的**区域实例**。

所以这不是第三条"另一套算法"：前景判据沿用那条线的同一思路（低饱和 + 高亮），
差别只在三处工程事实：

1. 纯 numpy 实现（`measure.imageops`），能在现场那台机器上跑；
2. 输出**区域实例**（连通域 + 距离峰值拆分 + 种子归属），而不是语义 logits；
3. 产出 mm 级度量并带 `baseline`/`level_view` 质量标记——**未经精度验证**。

## 阈值为什么是自适应的

S306 真机帧（1343×754）实测：灰光照明、`sat≈0`、整屏强晕影。固定 `val>0.55` 时
最大连通域占 37% 画面，固定阈值在那种帧上必然失败。默认 Otsu（按帧自适应），
调用方可用 `val_min` 覆盖回固定值（现场光稳定后复现性优先）。

## 输出口径

| 量 | 像素来源 | 可测性 |
| --- | --- | --- |
| 菌簇垂直跨度 `cluster_h_px` | 区域上下边界差 | 可算（平视投影） |
| 菌簇水平跨度 `cluster_w_px` | 最宽行跨度 | 可算（平视投影） |
| 菌簇投影径 `eq_diameter_px` | 等面积圆直径 | 可算，**不是菌盖直径** |
| 菌盖直径 | — | **不可测**：单档平视看不到菌盖轮廓 |
| 菌杆长度 | — | **不可测**：单档平视看不到杆 |

`Detection.len_mm` / `cap_mm` 因此**恒为 None**；本模块只产出像素级区域度量，
毫米换算由调用方按站位标定（`mm_per_px`）做，且**不许**把投影宽度冒充菌盖直径。

## 整个画面一块前景时怎么办

真实帧常常是"亮区占掉半屏"（S306 实测 Otsu 后最大块 52%）。那时没有可信的"朵"，
但整簇范围仍然可算：`BaselineConfig.whole_frame_fallback=True`（默认）会把**整幅前景**
当成一个"菌簇整块"度量，并给 `low_confidence` 标记——值仍然有用（看整簇变化），
但**朵数记 0，且明确标了不可信**。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from measure.imageops import (
    distance_transform,
    fill_holes,
    label_components,
    opening,
    paint_seeds,
    pick_seeds,
)
from measure.pipeline import Detection

#: 与 EUPE 线同源的蘑菇前景先验（高亮 + 低饱和）。现场实测：灰光照明下 sat≈0。
DEFAULT_SAT_MAX = 0.38
DEFAULT_VAL_MIN = 0.55

#: 小于此面积的连通域视为噪声（按帧面积比例）。
DEFAULT_MIN_AREA_FRAC = 0.0005
#: 大于此比例的单连通域视为"前景/背景没分开"，走整块兜底而不是硬拆（S306 实测 52%）。
DEFAULT_MAX_AREA_FRAC = 0.35


@dataclass(frozen=True)
class BlobMetrics:
    """一个区域实例的像素级形态度量（挂在 `Detection.metrics` 上）。"""

    area_px: int
    bbox_px: tuple[int, int, int, int]          # x0, y0, x1, y1（闭区间）
    cluster_w_px: int                           # 最宽行跨度
    cluster_h_px: int                           # 垂直跨度
    eq_diameter_px: float                       # 等面积圆直径 2*sqrt(A/pi)
    axis_minor_px: float                        # 二阶矩椭圆短轴（仅诊断用）
    axis_major_px: float
    widest_row_y: int
    widest_row_frac: float                      # 最宽行在区域里的相对高度（0=顶、1=底）
    n_seeds: int
    split_ok: bool
    flags: str = ""


@dataclass
class BaselineConfig:
    #: None = Otsu 自适应阈值（默认）；给值 = 固定阈值
    val_min: float | None = None
    sat_max: float = DEFAULT_SAT_MAX
    open_radius: int = 1
    fill_holes: bool = True
    min_area_frac: float = DEFAULT_MIN_AREA_FRAC
    max_area_frac: float = DEFAULT_MAX_AREA_FRAC
    #: 面积小于此值的连通域直接丢掉（像素），与 min_area_frac 取大者
    min_area_px_floor: int = 600
    min_distance_px: int = 12
    seed_abs_min_px: float = 4.0
    max_seeds: int = 64
    #: 前景一大块（超过 max_area_frac）时：True=整块当一个"菌簇整块"度量（带
    #: cluster_merged），False=判为 no_detection。默认 True：整簇变化仍然有信息量。
    whole_frame_fallback: bool = True


def otsu_threshold(values: np.ndarray, *, bins: int = 256) -> float:
    """Otsu 阈值（按直方图，纯 numpy）。全部同值时返回该值。"""
    v = np.asarray(values, dtype=np.float32).ravel()
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-9:
        return hi
    hist, edges = np.histogram(v, bins=bins, range=(lo, hi))
    p = hist.astype(np.float64) / max(1, hist.sum())
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)
    w1 = 1.0 - w0
    mu = np.cumsum(p * centers)
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = np.where((w0 > 0) & (w1 > 0), (mu_t * w0 - mu) ** 2 / (w0 * w1), 0.0)
    return float(centers[int(np.argmax(between))])


def rgb_value_sat(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """RGB（uint8 或 [0,1] float）→ ``(value, saturation)``，各 [0,1]。"""
    a = image.astype(np.float32)
    if a.ndim == 2:
        v = a / 255.0 if a.max() > 1.5 else a
        return v.astype(np.float32), np.zeros_like(v, np.float32)
    if a.shape[-1] == 1:
        a = np.repeat(a, 3, axis=-1)
    if a.max() > 1.5:
        a = a / 255.0
    mx = a.max(axis=-1)
    mn = a.min(axis=-1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return mx.astype(np.float32), sat.astype(np.float32)


def _blob_metrics(mask: np.ndarray, *, flags: str = "", n_seeds: int = 0,
                  split_ok: bool = False) -> BlobMetrics:
    """一个区域实例的形态度量（全部像素口径）。"""
    ys, xs = np.nonzero(mask)
    area = int(len(ys))
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    height = int(y1 - y0 + 1)
    # 逐行跨度（最宽行）
    row_min = np.full(mask.shape[0], np.iinfo(np.int64).max, np.int64)
    row_max = np.full(mask.shape[0], -1, np.int64)
    np.minimum.at(row_min, ys, xs)
    np.maximum.at(row_max, ys, xs)
    spans = np.where(row_max >= 0, row_max - row_min + 1, 0)
    widest_row = int(np.argmax(spans))
    cluster_w = int(spans[widest_row])
    # 二阶矩椭圆：均匀椭圆的二阶矩 λ = a²/4 ⇒ 半轴 a = 2√λ（仅诊断/参考）
    cy, cx = float(ys.mean()), float(xs.mean())
    dy = ys - cy
    dx = xs - cx
    cov = np.array([[float((dx * dx).mean()), float((dx * dy).mean())],
                    [float((dx * dy).mean()), float((dy * dy).mean())]])
    eig = np.clip(np.linalg.eigvalsh(cov), 0.0, None)
    return BlobMetrics(
        area_px=area, bbox_px=(x0, y0, x1, y1), cluster_w_px=cluster_w,
        cluster_h_px=height,
        eq_diameter_px=float(2.0 * np.sqrt(area / np.pi)),
        axis_minor_px=float(2.0 * np.sqrt(eig[0])),
        axis_major_px=float(2.0 * np.sqrt(eig[1])),
        widest_row_y=widest_row,
        widest_row_frac=float((widest_row - y0) / max(1, height - 1)),
        n_seeds=n_seeds, split_ok=split_ok, flags=flags,
    )


def _det_from_region(region: np.ndarray, flags: str, mm_per_px: float,
                     box_id: str, *, n_seeds: int = 0,
                     split_ok: bool = False) -> Detection:
    """区域 → `Detection`（毫米口径；菌盖直径列恒 NULL）。"""
    m = _blob_metrics(region, flags=flags, n_seeds=n_seeds, split_ok=split_ok)
    det = Detection(
        box_id=box_id,
        len_mm=None,      # 单档平视量不到菌杆长度，绝不编
        cap_mm=None,      # 单档平视看不到菌盖轮廓，绝不编
        quality_ok=True,
        flags=flags,
    )
    det.metrics = m                  # type: ignore[attr-defined]
    det.mm_per_px = mm_per_px        # type: ignore[attr-defined]
    return det


def detect_blobs(image: np.ndarray, *, mm_per_px: float, box_id: str,
                 config: BaselineConfig | None = None) -> tuple[list[Detection], dict]:
    """一帧 → 区域实例 `Detection` 列表（外加诊断 dict，便于现场排查）。

    不做任何"兜底假数"：前景/背景分不开且关掉整块兜底时返回空 + `reason`。
    """
    cfg = config or BaselineConfig()
    value, sat = rgb_value_sat(image)
    h, w = value.shape
    if cfg.val_min is None:
        thr, thr_source = otsu_threshold(value), "otsu"
    else:
        thr, thr_source = float(cfg.val_min), "fixed"
    raw = (value > thr) & (sat < cfg.sat_max)
    fg = opening(raw, cfg.open_radius)
    diag: dict = {
        "image_hw": (h, w), "val_min": thr, "val_min_source": thr_source,
        "sat_max": cfg.sat_max, "raw_frac": float(raw.mean()),
        "fg_frac": float(fg.mean()), "mm_per_px": mm_per_px,
        "n_components": 0, "n_kept": 0, "reason": "",
    }
    if not fg.any():
        diag["reason"] = "no_foreground"
        return [], diag

    labels, sizes = label_components(fg)
    frame_area = float(h * w)
    min_area = max(cfg.min_area_px_floor, cfg.min_area_frac * frame_area)
    order = np.argsort(-sizes[1:]) + 1 if len(sizes) > 1 else np.array([], np.int64)
    diag["n_components"] = int(len(sizes) - 1)
    kept = [int(i) for i in order if sizes[i] >= min_area]
    if not kept:
        diag["reason"] = "all_components_too_small"
        return [], diag
    biggest = int(sizes[kept[0]])
    diag["largest_area_frac"] = float(biggest / frame_area)
    if biggest > cfg.max_area_frac * frame_area:
        if not cfg.whole_frame_fallback:
            diag["reason"] = "fg_too_large"
            return [], diag
        # 整块兜底：把整个前景当一个"菌簇整块"，朵数不可信 ⇒ low_confidence
        diag["reason"] = "whole_frame_fallback"
        return [_det_from_region(fg, "low_confidence", mm_per_px, box_id)], diag

    detections: list[Detection] = []
    n_split_regions = 0
    for lab in kept:
        region = labels == lab
        if cfg.fill_holes:
            region = fill_holes(region)
        dist = distance_transform(region)
        seeds = pick_seeds(dist, min_distance=cfg.min_distance_px,
                           abs_min=cfg.seed_abs_min_px, max_seeds=cfg.max_seeds)
        if len(seeds) == 0:
            detections.append(_det_from_region(region, "low_confidence", mm_per_px, box_id))
            continue
        owner = paint_seeds(region.shape, seeds)
        seed_ys = np.sort(seeds[:, 0])
        split_ok = bool(len(seeds) == 1 or (seed_ys[-1] - seed_ys[0]) >= cfg.min_distance_px)
        for i in range(len(seeds)):
            sub = region & (owner == i)
            if int(sub.sum()) < min_area * 0.25:
                continue
            det = _det_from_region(sub, "" if split_ok else "low_confidence",
                                   mm_per_px, box_id,
                                   n_seeds=len(seeds), split_ok=split_ok)
            detections.append(det)
        if len(seeds) > 1:
            n_split_regions += 1
    diag["n_kept"] = len(detections)
    diag["n_split_regions"] = n_split_regions
    return detections, diag


class BaselineDetector:
    """`measure.pipeline.Detector` 协议的无模型实现（纯 numpy，见模块 docstring）。"""

    def __init__(self, config: BaselineConfig | None = None):
        self.config = config or BaselineConfig()
        self.last_diag: dict = {}
        self.last_detections: list[Detection] = []

    def detect(self, image: np.ndarray, *, mm_per_px: float, box_id: str) -> list[Detection]:
        dets, diag = detect_blobs(image, mm_per_px=mm_per_px, box_id=box_id,
                                  config=self.config)
        self.last_diag = diag
        self.last_detections = dets
        return dets
