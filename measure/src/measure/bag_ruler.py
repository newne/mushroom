"""L1/L2：菌包实例分割（帧内标尺的来源）。

## 为什么需要它

平视 `level0` 是**沿过道、略俯的远景**：同帧内近排清楚、远排迅速变小
（实测同一类物体表观尺寸差 ≥2 倍），所以**全画面单一 mm/px 必然错**。
本模块把菌包切成**实例**，每个实例带自己的宽度与深度排号，
让下游"每个菇体用同一排的菌包定局部 mm/px"。

## 诚实的边界

- 相邻菌包常常**粘成一个连通域**：这里用距离变换峰值 + 种子归属硬拆，
  拆得好不好由 `split_ok` 与 `width_cv`（排内宽度变异系数）暴露，不装懂；
- **远排**纹理/对比度低于下限时该排整排标 `unreliable`（照票 18：值写 NULL，
  处置=不可用，**不是降权**）；
- 本模块**只产像素量**，mm 换算是下游 `estimate` 的事（标尺值由现场注入，
  **这里没有任何内置猜测值**）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from measure.imageops import (
    bbox_of,
    block_downscale,
    distance_transform,
    fill_holes,
    label_components,
    opening,
    paint_seeds,
    pick_seeds,
)
from measure.baseline import otsu_threshold, rgb_value_sat


@dataclass
class BagRegion:
    """一个菌包实例（像素量）。"""

    x0: int
    y0: int
    x1: int
    y1: int
    centroid_y: float
    centroid_x: float
    width_px: int          # 最宽行跨度
    height_px: int
    area_px: int
    width_profile: np.ndarray       # 每行跨度（长度 = height_px），供"实例处局部宽度"
    row_id: int            # 深度排号（按 centroid_y 分带；0 = 最上/最远）
    contrast: float        # 本实例区域内 p95-p5
    sigma: float           # 本实例区域内标准差
    split_ok: bool = True

    def width_at(self, y: float) -> int:
        """该实例在指定图像行处的横向跨度（越界则取最近行）。"""
        rel = int(round(y - self.y0))
        rel = max(0, min(len(self.width_profile) - 1, rel))
        return int(self.width_profile[rel])

    def characteristic_width_px(self) -> int:
        """**特征宽度**：轮廓中"接近最宽"的那些行的中位跨度。

        为什么不直接用在某一行 `width_at(y)`：菇体长在袋**顶上**，它的 y 落在袋体上沿
        之外/之上，那里量到的只是菇体自己的宽度（试验里 7px vs 袋宽 200px）。
        所以标尺必须用**袋体自身的特征宽度**，而不是"实例所在行"的宽度。
        """
        prof = np.asarray(self.width_profile)
        if prof.size == 0:
            return int(self.width_px)
        wide = prof[prof >= 0.5 * prof.max()]
        return int(np.median(wide)) if wide.size else int(self.width_px)


@dataclass
class BagConfig:
    #: 前景阈值：None = Otsu 自适应；给值 = 固定（0–255）
    val_threshold: float | None = None
    open_radius: int = 1
    #: 填内部孔洞：**默认关**——全帧洪泛在 4.65MPx 上是分钟级，袋级精度用不到它
    fill_holes: bool = False
    #: 工作分辨率降采样倍数（1=原图；2=1/2；4=1/4）。帧内标尺只需袋级精度。
    downscale: int = 2
    #: 最小菌包面积（**原始像素**）：小于它当噪声
    min_area_px: int = 20000
    #: 距离变换种子的最小间距（≈ 半个袋宽，用来拆相邻袋；**原始像素**）
    seed_min_distance_px: int = 140
    #: 深度分带高度（像素）：同带视为"同一排"
    row_band_px: int = 200
    #: 远排判定：带内对比度/纹理低于这两个下限 ⇒ unreliable
    min_contrast: float = 60.0
    min_sigma: float = 15.0
    #: 弱对比兜底：Otsu 分不出候选时，用这个分位阈值重试一次（**不许静默丢排**）
    weak_retry_percentile: float = 95.0


def _row_profile(mask: np.ndarray) -> np.ndarray:
    """每行横向跨度（无前景处为 0）。"""
    h = mask.shape[0]
    row_min = np.full(h, np.iinfo(np.int64).max, np.int64)
    row_max = np.full(h, -1, np.int64)
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return np.zeros(h, np.int64)
    np.minimum.at(row_min, ys, xs)
    np.maximum.at(row_max, ys, xs)
    return np.where(row_max >= 0, row_max - row_min + 1, 0).astype(np.int64)


def find_bags(gray: np.ndarray, cfg: BagConfig | None = None) -> tuple[list[BagRegion], dict]:
    """一帧灰度图 → 菌包实例列表 + 诊断 dict。

    ``gray`` 建议是 **0–255** 灰度（与 `measure.quality` 同刻度）。

    **性能**：在 ``cfg.downscale`` 的分辨率上做阈值/形态学/连通域，距离变换只在
    **每个连通域的外接框内做一次**（框内再按需降采样），坐标最后乘回去。
    真机 2880×1616 上这是"秒级 vs 分钟级"的差别（全帧逐行 DT 实测走不动）。

    **语义（Chief 2026-10-14 裁定）**：L1 **只做候选**，**不许静默丢排**——
    Otsu 分不出候选时用弱对比阈值重试一次；低对比排照常返回，
    由 `row_is_reliable()` 判、由发射层出 `unreliable`（而不是变成 `no_detection`）。
    """
    cfg = cfg or BagConfig()
    if gray.ndim != 2:
        raise ValueError("需要 2D 灰度图")
    ds = max(1, int(cfg.downscale))
    small = block_downscale(gray.astype(np.float32), ds) if ds > 1 else gray.astype(np.float32)
    min_area_small = max(50, int(cfg.min_area_px / (ds * ds)))
    seed_dist_small = max(4, int(round(cfg.seed_min_distance_px / ds)))

    def _mask_at(thr_value: float) -> np.ndarray:
        m = opening(small > thr_value, cfg.open_radius)
        if cfg.fill_holes:
            box = bbox_of(m)
            if box is not None:
                m = m.copy()
                m[box[0]:box[2], box[1]:box[3]] = fill_holes(m[box[0]:box[2], box[1]:box[3]])
        return m

    thr = float(cfg.val_threshold) if cfg.val_threshold is not None else otsu_threshold(small)
    mask = _mask_at(thr)
    diag: dict = {"threshold": thr, "fg_frac": float(mask.mean()), "downscale": ds,
                  "n_regions": 0, "weak_retry": False}

    def _regions_of(m: np.ndarray) -> tuple[list[BagRegion], int, int]:
        labels, sizes = label_components(m)
        kept = [i for i in range(1, len(sizes)) if sizes[i] >= min_area_small]
        regions: list[BagRegion] = []
        for lab in kept:
            box = bbox_of(labels == lab, pad=2)
            if box is None:
                continue
            sub = (labels[box[0]:box[2], box[1]:box[3]] == lab)
            # 距离变换**只在这个小框里**做（袋块再降一级，拆分只需要袋级精度）
            factor = max(1, int(math.ceil(max(sub.shape) / 400.0)))
            work = block_downscale(sub.astype(np.float32), factor) > 0.5 if factor > 1 else sub
            seeds = pick_seeds(distance_transform(work),
                               min_distance=max(3, seed_dist_small // factor),
                               abs_min=max(2.0, seed_dist_small * 0.25 / factor),
                               max_seeds=64)
            if len(seeds) == 0:
                parts = [sub]
                split_ok = False
            else:
                owner = paint_seeds(work.shape, seeds)
                parts_big = _upscale_owner(owner, sub.shape, factor)
                parts = [sub & (parts_big == k) for k in range(len(seeds))]
                parts = [p for p in parts if int(p.sum()) >= min_area_small]
                split_ok = len(parts) > 1
            for part in parts:
                ys, xs = np.nonzero(part)
                if len(ys) == 0:
                    continue
                ys_full = (ys + box[0]) * ds
                xs_full = (xs + box[1]) * ds
                y0f, y1f = int(ys_full.min()), int(ys_full.max())
                prof_small = _row_profile(part)
                rows_small = np.nonzero(prof_small)[0]
                prof = prof_small[rows_small.min():rows_small.max() + 1] * ds
                # 对比度/纹理在**原图外接框**里算（袋表面均色，掩膜内统计会算出 0）
                y_lo, y_hi = max(0, y0f - 5), min(gray.shape[0], y1f + 6)
                x_lo = max(0, int(xs_full.min()) - 5)
                x_hi = min(gray.shape[1], int(xs_full.max()) + 6)
                local = gray[y_lo:y_hi, x_lo:x_hi]
                regions.append(BagRegion(
                    x0=int(xs_full.min()), y0=y0f, x1=int(xs_full.max()), y1=y1f,
                    centroid_y=float(ys_full.mean()), centroid_x=float(xs_full.mean()),
                    width_px=int(prof.max()), height_px=int(y1f - y0f + 1),
                    area_px=int(len(ys_full)), width_profile=prof,
                    row_id=0,
                    contrast=float(np.percentile(local, 95) - np.percentile(local, 5)),
                    sigma=float(local.std()),
                    split_ok=split_ok,
                ))
        return regions, int(len(sizes) - 1), len(kept)

    regions, n_comp, n_kept_raw = _regions_of(mask)
    if not regions and cfg.weak_retry_percentile is not None:
        # 弱对比兜底：**不静默丢排**——低对比排也要作为候选交出去，由 row_is_reliable 判、
        # 由发射层出 `unreliable`（Chief 裁定：两种"不可用"不许混成一句）
        weak = float(np.percentile(small, cfg.weak_retry_percentile))
        if weak < thr or not mask.any():
            mask = _mask_at(weak)
            regions, n_comp, n_kept_raw = _regions_of(mask)
            diag["weak_retry"] = True
            diag["threshold"] = weak

    diag["n_components"] = n_comp
    diag["n_kept_components"] = n_kept_raw
    if not regions:
        diag["reason"] = "no_bag_region"

    # 深度分带：按 centroid_y 升序分带（y 越小 = 越远）
    regions.sort(key=lambda r: r.centroid_y)
    row_id, row_start = 0, None
    for r in regions:
        if row_start is None or (r.centroid_y - row_start) > cfg.row_band_px:
            if row_start is not None:
                row_id += 1
            row_start = r.centroid_y
        r.row_id = row_id
    diag["n_regions"] = len(regions)
    diag["n_rows"] = row_id + 1
    return regions, diag


def _upscale_owner(owner: np.ndarray, shape: tuple[int, int], factor: int) -> np.ndarray:
    """把降采样后的归属图放回原尺寸（最近邻）。"""
    if factor <= 1:
        return owner
    h, w = shape
    ys = np.minimum((np.arange(h) // factor), owner.shape[0] - 1)
    xs = np.minimum((np.arange(w) // factor), owner.shape[1] - 1)
    return owner[ys][:, xs]


def row_is_reliable(regions: list[BagRegion], row_id: int, cfg: BagConfig | None = None) -> bool:
    """该排是否可信：排内至少 1 个菌包，且对比度/纹理不低于下限（远排会挂）。"""
    cfg = cfg or BagConfig()
    row = [r for r in regions if r.row_id == row_id]
    if not row:
        return False
    return (max(r.contrast for r in row) >= cfg.min_contrast
            and max(r.sigma for r in row) >= cfg.min_sigma)


def row_width_cv(regions: list[BagRegion], row_id: int) -> float | None:
    """排内袋宽变异系数（一致性凭证；<1 个袋时 None）。"""
    w = [r.width_px for r in regions if r.row_id == row_id]
    if len(w) < 2:
        return None
    a = np.asarray(w, float)
    return float(a.std() / a.mean())


def nearest_bag_in_same_row(regions: list[BagRegion], y: float,
                            cfg: BagConfig | None = None, *,
                            x: float | None = None) -> BagRegion | None:
    """找与给定 (x, y) **同排**的菌包：先按深度带筛，再按水平距离取最近。

    为什么不能只按 y 取最近：菇体长在袋**顶上**，它的质心比袋体质心高（试验里差 106px，
    而"同排"容差只有 100px ⇒ 会把同排的袋排掉）。所以：
    - "同排"由 `row_band_px` 判定（深度带），**不是**紧密的 y 容差；
    - 同带内用水平距离选（菇体在哪个袋上，就取哪个袋）；
    - 跨带一律不借（禁止把近排尺度套到远排）。
    """
    cfg = cfg or BagConfig()
    if not regions:
        return None
    same_band = [r for r in regions if abs(r.centroid_y - y) <= cfg.row_band_px]
    if not same_band:
        return None
    if x is None:
        return min(same_band, key=lambda r: abs(r.centroid_y - y))
    return min(same_band, key=lambda r: abs(r.centroid_x - x))
