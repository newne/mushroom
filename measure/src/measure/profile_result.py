"""Level 0/1/2 结果行与逐级降级（spec §4.7）。

**离线优先**：新指标先进 JSONL（帧/袋/锚点三层），不改 DDL、不挤 `measurements`。
逐袋粒度与 `measurements` 主键 `(ts, room_id, box_id)` 不匹配，硬塞会把"每袋"压成"每框"。

三条纪律：

1. 每个指标**独立有效性**；一个失败不拖垮整行（票 15 §2）。
2. 失败一律 `None`；只有"完成有效观察且确认没有"才允许 `0`（`confirmed_absent`）。
3. `delta` 不在这里出现：它只作 QA，不进主曲线（spec §4.7）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from measure.bag_top import BaselineProfile
from measure.cluster_profile import HeightProfile
from measure.debag import RegionMetrics, RegionResult


@dataclass(frozen=True)
class ResultKeys:
    image_id: str
    pipeline_version: str
    model_version: str
    roi_version: str
    calibration_version: str

    def to_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CapInput:
    """帽级输入；`confirmed_absent=True` 才允许计数 0。"""

    count: int | None = None
    area_fraction: float | None = None
    diameter_px: float | None = None
    confirmed_absent: bool = False


@dataclass(frozen=True)
class BagResult:
    keys: ResultKeys
    slot_id: str
    level: str = "bag"
    status: str = "ok"
    h_profile: float | None = None
    h_p50: float | None = None
    h_p90: float | None = None
    raw_top_height_px: float | None = None
    rel_profile_height: float | None = None
    rel_top_height: float | None = None
    rel_width: float | None = None
    ref_px: float | None = None
    ref_kind: str | None = None
    area_px: int | None = None
    eq_diameter_px: float | None = None
    cluster_width_px: int | None = None
    cluster_h_px: int | None = None
    removed_area_ratio: float | None = None
    mask_source: str | None = None
    baseline_valid: bool = False
    association_valid: bool = False
    measurement_valid: bool = False
    cluster_height_usable: bool = False
    region_metrics_usable: bool = False
    cap_count: int | None = None
    cap_area_fraction: float | None = None
    cap_diameter_px: float | None = None
    cap_metrics_usable: bool = False
    confirmed_absent: bool = False
    reasons: tuple[str, ...] = ()

    def to_row(self) -> dict:
        row = self.keys.to_row()
        row.update(asdict(self, dict_factory=dict))
        row.pop("keys")
        row["reasons"] = list(self.reasons)
        return row


def _null_bag(keys: ResultKeys, slot_id: str, *, status: str, reasons: tuple[str, ...],
              association_valid: bool, measurement_valid: bool,
              confirmed_absent: bool = False) -> BagResult:
    return BagResult(
        keys=keys, slot_id=slot_id, status=status, reasons=reasons,
        association_valid=association_valid, measurement_valid=measurement_valid,
        confirmed_absent=confirmed_absent,
    )


def build_bag_result(
    keys: ResultKeys,
    slot_id: str,
    *,
    height: HeightProfile | None = None,
    baseline: BaselineProfile | None = None,
    region: RegionResult | None = None,
    association_valid: bool = True,
    measurement_valid: bool = True,
    ref_px: float | None = None,
    ref_kind: str | None = None,
    cap: CapInput | None = None,
    confirmed_absent: bool = False,
) -> BagResult:
    """一袋一簇 → 一行；按 §4.7 逐级降级。"""
    if not measurement_valid:
        return _null_bag(keys, slot_id, status="no_detection",
                         reasons=("no_detection",), association_valid=association_valid,
                         measurement_valid=False)
    if not association_valid:
        return _null_bag(keys, slot_id, status="degraded",
                         reasons=("association_ambiguous",),
                         association_valid=False, measurement_valid=True)

    reasons: list[str] = []
    baseline_valid = bool(baseline is not None and baseline.baseline_valid)
    if not baseline_valid:
        reasons.append("baseline_invalid")

    height_usable = bool(height is not None and height.status == "ok"
                         and height.h_profile is not None and baseline_valid)
    if not height_usable and baseline_valid:
        reasons.extend(r for r in (height.reasons if height else ("no_height",)) if r)

    region_usable = bool(region is not None and region.metrics is not None
                         and region.mask_source == "cluster_minus_bag")
    if region is not None:
        reasons.extend(region.reasons)
    elif not region_usable:
        reasons.append("no_region")

    h_profile = height.h_profile if height_usable else None
    h_p50 = height.h_p50 if height_usable else None
    h_p90 = height.h_p90 if height_usable else None
    raw_top = height.raw_top_height_px if height_usable else None

    rel_profile = rel_top = rel_width = None
    if height_usable and ref_px:
        rel_profile = h_profile / ref_px
        rel_top = h_p90 / ref_px
    if region_usable and ref_px:
        rel_width = region.metrics.cluster_width_px / ref_px
    if ref_px is None:
        reasons.append("no_ref_px")

    metrics = region.metrics if region_usable else None
    cap_count = cap.count if cap else None
    cap_usable = bool(cap is not None and (cap.count is not None or cap.confirmed_absent))
    if cap is None:
        reasons.append("cap_model_unavailable")
    elif not cap_usable:
        reasons.append("no_cap")
    if cap is not None and cap.confirmed_absent:
        cap_count = 0

    status = "ok" if (height_usable or region_usable) else "degraded"
    return BagResult(
        keys=keys, slot_id=slot_id, status=status,
        h_profile=h_profile, h_p50=h_p50, h_p90=h_p90, raw_top_height_px=raw_top,
        rel_profile_height=rel_profile, rel_top_height=rel_top, rel_width=rel_width,
        ref_px=ref_px, ref_kind=ref_kind,
        area_px=metrics.area_px if metrics else None,
        eq_diameter_px=metrics.eq_diameter_px if metrics else None,
        cluster_width_px=metrics.cluster_width_px if metrics else None,
        cluster_h_px=metrics.cluster_h_px if metrics else None,
        removed_area_ratio=metrics.removed_area_ratio if metrics else None,
        mask_source=region.mask_source if region else None,
        baseline_valid=baseline_valid, association_valid=True, measurement_valid=True,
        cluster_height_usable=height_usable, region_metrics_usable=region_usable,
        cap_count=cap_count,
        cap_area_fraction=cap.area_fraction if cap_usable else None,
        cap_diameter_px=cap.diameter_px if cap_usable else None,
        cap_metrics_usable=cap_usable,
        confirmed_absent=confirmed_absent,
        reasons=tuple(dict.fromkeys(reasons)),
    )


@dataclass(frozen=True)
class AnchorResult:
    keys: ResultKeys
    anchor_id: str
    level: str = "anchor"
    h_anchor: float | None = None
    n_bags_valid: int = 0
    n_bags_total: int = 0
    panel_coverage: float = 0.0
    panel_coverage_low: bool = True
    anchor_width_px: int | None = None
    anchor_area_px: int | None = None
    anchor_eq_diameter_px: float | None = None
    anchor_mask_source: str | None = None
    segmentation_status: str = "by_version_and_batch"
    usable: bool = False
    reasons: tuple[str, ...] = ()

    def to_row(self) -> dict:
        row = self.keys.to_row()
        row.update(asdict(self, dict_factory=dict))
        row.pop("keys")
        row["reasons"] = list(self.reasons)
        return row


def aggregate_anchor(
    keys: ResultKeys,
    anchor_id: str,
    bags: list[BagResult],
    *,
    n_slots: int | None = None,
    anchor_region: RegionResult | None = None,
    panel_coverage_min: float = 0.8,
) -> AnchorResult:
    """袋级 → 锚点级：`median` over 可用袋，缺袋进 `panel_coverage` 分母。"""
    valid = [b for b in bags if b.cluster_height_usable and b.h_profile is not None]
    total = n_slots if n_slots is not None else len(bags)
    coverage = (len(valid) / total) if total else 0.0
    low = coverage < panel_coverage_min
    reasons: list[str] = []
    if not valid:
        reasons.append("no_usable_bags")
    if low:
        reasons.append("panel_coverage_low")

    metrics: RegionMetrics | None = anchor_region.metrics if anchor_region else None
    return AnchorResult(
        keys=keys, anchor_id=anchor_id,
        h_anchor=float(np.median([b.h_profile for b in valid])) if valid else None,
        n_bags_valid=len(valid), n_bags_total=total,
        panel_coverage=coverage, panel_coverage_low=low,
        anchor_width_px=metrics.cluster_width_px if metrics else None,
        anchor_area_px=metrics.area_px if metrics else None,
        anchor_eq_diameter_px=metrics.eq_diameter_px if metrics else None,
        anchor_mask_source=metrics.mask_source if metrics else None,
        usable=bool(valid) and not low,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class FrameResult:
    keys: ResultKeys
    anchor_id: str
    level: str = "frame"
    quality_flags: str = ""
    status: str = "ok"
    n_slots: int = 0
    n_bags_valid: int = 0
    n_bags_with_region: int = 0
    n_no_detection: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_row(self) -> dict:
        row = self.keys.to_row()
        row.update(asdict(self, dict_factory=dict))
        row.pop("keys")
        row["reasons"] = list(self.reasons)
        return row


def build_frame_result(keys: ResultKeys, anchor_id: str, bags: list[BagResult], *,
                       n_slots: int | None = None, quality_flags: str = "") -> FrameResult:
    total = n_slots if n_slots is not None else len(bags)
    return FrameResult(
        keys=keys, anchor_id=anchor_id, quality_flags=quality_flags,
        status="ok" if any(b.cluster_height_usable for b in bags) else "degraded",
        n_slots=total,
        n_bags_valid=sum(1 for b in bags if b.cluster_height_usable),
        n_bags_with_region=sum(1 for b in bags if b.region_metrics_usable),
        n_no_detection=sum(1 for b in bags if b.status == "no_detection"),
        reasons=tuple(dict.fromkeys(r for b in bags for r in b.reasons)),
    )


def write_jsonl(path: str | Path, rows) -> int:
    """逐行写 JSON；`None` 保持 `null`（不写 0、不省略）。返回行数。"""
    count = 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
