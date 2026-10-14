"""L1–L3 生产接线：菌包实例 → 袋肩 ROI → 菇体实例 → **袋级菌簇**（票 15，Chief 2026-10-14 批准接线）。

## 输出什么

每个**菌簇**（同一袋上的菇体实例集合）一个 `Detection`，`metrics` 里带**像素量**与
**两个方向的局部尺度**：

- 水平 `mm_per_px_x = 袋长 240 mm / 该袋特征宽 px`（不受俯角影响）；
- 竖直 `mm_per_px_y = 袋径 140 mm / 该袋特征宽 px`，**θ 已注入时再 ÷cosθ**（把投影补偿
  折进这一路，`aggregate_box` 只管乘）；
- 两个方向**分开放**，因为"同帧一个 mm/px"正是 §1.1 实测差 2 倍以上的错法。

每帧的**证据**（票 20 五列）随 `FrameResult.evidence` 返回：

| 证据 | 取值 |
| --- | --- |
| `estimated_fields` | `mean_cluster_w_mm+mean_cluster_h_mm`（**只列真的出了值的列**） |
| `scale_source` | `bag_standard`（袋标准尺寸）/ `calibration_target`（站位 `--mm-per-px`，ADR-0024 优先）/ `none`（本帧没有可用标尺） |
| `mm_per_px` / `scale_ref_mm` / `scale_ref_px` | 水平那一路的实际比例与参考物（可交叉复算 `mm_per_px ≈ ref_mm / ref_px`） |

## 质量语义（票 15 §14）

- `estimate` 恒出（值是估算）；`no_theta` = 有标尺只缺 θ（**不算不可用**）；
- `height_uncorrected` = 竖直列是**未做 cosθ 补偿的投影跨度**（**不是长度**，仅可作相对比较）；
- `unreliable` = 该簇所在的排不可信（远排）⇒ 该检出 `quality_ok=False`，不入统计；
- `no_scale` = 本帧没有可用标尺（无袋/袋宽不可用）⇒ mm 列 NULL；
- **`no_detection` 与 `unreliable` 严格分开**：零候选是 `no_detection`，有候选判不可信是 `unreliable`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from measure import contract
from measure.bag_ruler import (
    BagConfig,
    BagRegion,
    find_bags,
    nearest_bag_in_same_row,
    row_is_reliable,
)
from measure.estimate import BAG_DIAMETER_MM, BAG_LENGTH_MM, filter_above_bag_top
from measure.fruitbody import FruitConfig, FruitInstance, find_fruitbodies
from measure.pipeline import Detection

#: 袋肩带：画面下方这个比例（L3 只在这里跑；§8.1 C3）
DEFAULT_ROI_FRAC = 0.45


@dataclass
class ClusterMetrics:
    """`Detection.metrics` 的实现：像素量 + **两个方向**的局部 mm/px。"""

    cluster_w_px: float
    cluster_h_px: float
    area_px: float
    mm_per_px_x: float | None = None
    mm_per_px_y: float | None = None

    @property
    def eq_diameter_px(self) -> float:
        """等效直径（面积口径），供 `mean_projected_d_mm` 用。"""
        return float(math.sqrt(max(self.area_px, 0.0) * 4.0 / math.pi))


@dataclass
class FrameEvidence:
    """票 20 的五个证据列 + 帧级标记（`production` 直接填进行里）。"""

    estimated_fields: tuple[str, ...] = ()
    scale_source: str | None = None
    mm_per_px: float | None = None
    scale_ref_mm: float | None = None
    scale_ref_px: float | None = None
    flags: frozenset[str] = frozenset()
    n_instances: int = 0          # **朵数**（实例数），不是簇数
    note: str = ""


@dataclass
class FrameResult:
    detections: list[Detection]
    evidence: FrameEvidence


@dataclass
class L3Config:
    #: 站位标定值（ADR-0024 优先）；给了就用它、`scale_source=calibration_target`
    station_mm_per_px: float | None = None
    bag_length_mm: float = BAG_LENGTH_MM
    bag_diameter_mm: float = BAG_DIAMETER_MM
    #: **俯角（deg）**：现场竖直标尺测定值；未注入 ⇒ 不许猜
    depression_deg: float | None = None
    #: 竖直列在 θ 未注入时是否出"未补偿投影跨度"（Chief 2026-10-14 裁定 (b)：接线时开）
    allow_uncorrected_height: bool = True
    roi_frac: float = DEFAULT_ROI_FRAC
    bag_cfg: BagConfig = field(default_factory=BagConfig)
    fruit_cfg: FruitConfig = field(default_factory=FruitConfig)


class L3Detector:
    """`measure_fn` 用的检出器：一帧 → 袋级菌簇检出 + 证据。"""

    def __init__(self, config: L3Config | None = None):
        self.cfg = config or L3Config()

    # ---- 内部 ----

    def _bag_clusters(self, kept: list[FruitInstance], bags: list[BagRegion]
                      ) -> dict[int, tuple[BagRegion, list[FruitInstance]]]:
        groups: dict[int, tuple[BagRegion, list[FruitInstance]]] = {}
        for inst in kept:
            bag = nearest_bag_in_same_row(bags, inst.cy, self.cfg.bag_cfg, x=inst.cx)
            if bag is None:
                continue
            key = id(bag)
            if key not in groups:
                groups[key] = (bag, [])
            groups[key][1].append(inst)
        return groups

    def _scales(self, bag: BagRegion) -> tuple[float | None, float | None, float | None]:
        """(x 尺度, y 尺度, 参考宽 px)。θ 未注入 ⇒ y 尺度不做投影补偿。"""
        cfg = self.cfg
        if cfg.station_mm_per_px:
            mpp = float(cfg.station_mm_per_px)
            return mpp, mpp, None
        ref_px = float(bag.characteristic_width_px() or bag.width_px)
        if ref_px <= 1:
            return None, None, None
        mpp_x = cfg.bag_length_mm / ref_px
        mpp_y = cfg.bag_diameter_mm / ref_px
        if cfg.depression_deg is not None:
            cos_t = math.cos(math.radians(float(cfg.depression_deg)))
            if cos_t > 1e-6:
                mpp_y = mpp_y / cos_t
        return mpp_x, mpp_y, ref_px

    # ---- 对外协议 ----

    def detect_frame(self, image: np.ndarray, *, box_id: str) -> FrameResult:
        cfg = self.cfg
        gray = image if image.ndim == 2 else image.mean(axis=-1)
        h, _w = gray.shape
        bags, bdiag = find_bags(gray, cfg.bag_cfg)
        y0 = int(h * (1.0 - float(cfg.roi_frac)))
        insts, _fdiag = find_fruitbodies(gray[y0:], None, cfg.fruit_cfg, origin=(y0, 0))
        kept, gdiag = filter_above_bag_top(insts, bags)

        flags: set[str] = {"estimate"}
        note_bits: list[str] = []
        if cfg.depression_deg is None:
            flags.add("no_theta")
            if cfg.allow_uncorrected_height:
                flags.add("height_uncorrected")
                note_bits.append("竖直列=未做 cosθ 补偿的投影跨度（不是长度）")
            else:
                note_bits.append("未注入俯角 ⇒ 竖直列 NULL")

        if not bags:
            # 零候选：**没有候选可判** ⇒ no_detection（与 unreliable 严格分开）
            flags.add("no_scale")
            flags.add("no_detection")
            ev = FrameEvidence(
                estimated_fields=(), scale_source="none", flags=frozenset(flags),
                n_instances=0,
                note="本帧没有菌包候选 ⇒ 无标尺、无 mm；" + "；".join(note_bits))
            return FrameResult([], ev)

        groups = self._bag_clusters(kept, bags)
        dets: list[Detection] = []
        refs: list[float] = []
        mpps: list[float] = []
        n_instances = 0
        unreliable_only = True
        for bag, group in groups.values():
            reliable = row_is_reliable(bags, bag.row_id, cfg.bag_cfg)
            mpp_x, mpp_y, ref_px = self._scales(bag)
            if ref_px:
                refs.append(ref_px)
            if mpp_x:
                mpps.append(mpp_x)
            w_px = float(max(i.x1 for i in group) - min(i.x0 for i in group))
            h_px = float(max(i.y1 for i in group) - min(i.y0 for i in group))
            area = float(sum(i.area_px for i in group))
            det_flags = {"estimate"}
            if not reliable:
                det_flags.add("unreliable")
            else:
                unreliable_only = False
            dets.append(Detection(
                box_id=box_id, len_mm=None, cap_mm=None,
                quality_ok=bool(reliable),
                flags=contract.format_flags(det_flags),
                metrics=ClusterMetrics(cluster_w_px=w_px, cluster_h_px=h_px,
                                       area_px=area, mm_per_px_x=mpp_x, mm_per_px_y=mpp_y),
                mm_per_px=mpp_x,
            ))
            n_instances += len(group)

        # 袋存在但一个簇都没定出来 ⇒ 没有候选可判（**不是** unreliable）
        if not dets:
            flags.add("no_scale")
            flags.add("no_detection")
            ev = FrameEvidence(
                estimated_fields=(), scale_source="none", flags=frozenset(flags),
                n_instances=0,
                note="菌包有候选但没有可归属的菇体实例 ⇒ 无 mm；" + "；".join(note_bits))
            return FrameResult([], ev)

        if unreliable_only:
            flags.add("unreliable")      # 整帧只剩不可信的排（远排）
        if not mpps:
            flags.add("no_scale")
            note_bits.append("找不到可用参考宽 ⇒ mm 列 NULL")

        usable_scale = bool(mpps)
        if cfg.station_mm_per_px:
            scale_source = "calibration_target"
        elif usable_scale:
            scale_source = "bag_standard"
        else:
            scale_source = "none"

        fields: list[str] = []
        if usable_scale:
            fields.append("mean_cluster_w_mm")
            if cfg.depression_deg is not None or cfg.allow_uncorrected_height:
                fields.append("mean_cluster_h_mm")
        ref_med = float(np.median(refs)) if refs else None
        ev = FrameEvidence(
            estimated_fields=tuple(fields),
            scale_source=scale_source,
            mm_per_px=float(np.median(mpps)) if mpps else None,
            scale_ref_mm=(cfg.bag_length_mm if scale_source == "bag_standard" else None),
            scale_ref_px=(ref_med if scale_source == "bag_standard" else None),
            flags=frozenset(flags),
            n_instances=n_instances,
            note="；".join(note_bits),
        )
        return FrameResult(dets, ev)

    def detect(self, image: np.ndarray, *, mm_per_px: float | None = None,
               box_id: str) -> list[Detection]:
        """兼容旧协议（只取检出）；证据请走 `detect_frame()`。"""
        return self.detect_frame(image, box_id=box_id).detections


def detector_from_settings(*, mm_per_px: float | None = None,
                           depression_deg: float | None = None,
                           allow_uncorrected_height: bool = True,
                           **kw: Any) -> L3Detector:
    """生产装配用的工厂（deploy 侧只传站位标定值与现场 θ）。"""
    return L3Detector(L3Config(station_mm_per_px=mm_per_px,
                               depression_deg=depression_deg,
                               allow_uncorrected_height=allow_uncorrected_height,
                               **kw))
