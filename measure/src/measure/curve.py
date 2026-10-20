"""单锚点曲线：证据行 → 序列 → 分段 → 速率（spec §7）。

## 三条不许破的规则

1. **缺测留空**：没有有效袋的帧仍然产一个 `value=None` 的点，**不补 0、不插值**；
2. **分段**：版本四元组或批次变化 ⇒ 新段；段间不连点、不算速率；
3. **不跨长缺测**：中间有 `missing` 点，或 `dt` 超出 `max_gap_h` ⇒ 速率 `None`。

`delta` 不在这条链路里（spec §4.7：它只作 QA，不做主曲线）。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

import numpy as np

SEGMENT_FIELDS = ("pipeline_version", "model_version", "roi_version",
                  "calibration_version", "batch_no")


@dataclass(frozen=True)
class CurvePoint:
    ts: str
    anchor_id: str
    value: float | None
    n_bags_valid: int
    n_bags_total: int
    panel_coverage: float
    segmentation_status: str
    segment_id: int
    status: str
    usable: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RatePoint:
    ts: str
    prev_ts: str
    dt_h: float
    rate_per_h: float | None
    segment_id: int
    status: str


@dataclass(frozen=True)
class LayerPoint:
    ts: str
    value: float | None
    n_anchors: int


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def _segment_key(row) -> tuple:
    return tuple(str(_get(row, name, "")) for name in SEGMENT_FIELDS)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def anchor_series(rows, *, metric: str = "h_anchor") -> list[CurvePoint]:
    """锚点级证据行 → 有序序列（含 `missing` 点）。"""
    ordered = sorted(rows, key=lambda r: str(_get(r, "ts", "")))
    seg_ids: dict[tuple, int] = {}
    out: list[CurvePoint] = []
    for row in ordered:
        key = _segment_key(row)
        seg = seg_ids.setdefault(key, len(seg_ids))
        value = _get(row, metric)
        value = None if value is None else float(value)
        reasons = tuple(_get(row, "reasons", ()) or ())
        out.append(CurvePoint(
            ts=str(_get(row, "ts", "")),
            anchor_id=str(_get(row, "anchor_id", "")),
            value=value,
            n_bags_valid=int(_get(row, "n_bags_valid", 0) or 0),
            n_bags_total=int(_get(row, "n_bags_total", 0) or 0),
            panel_coverage=float(_get(row, "panel_coverage", 0.0) or 0.0),
            segmentation_status=str(_get(row, "segmentation_status", "by_version_and_batch")),
            segment_id=seg,
            status="ok" if value is not None else "missing",
            usable=bool(_get(row, "usable", value is not None)),
            reasons=reasons,
        ))
    return out


def rates(points, *, min_span_h: float = 2.0, max_gap_h: float = 12.0) -> list[RatePoint]:
    """相邻点速率；只在同段、相邻、都有值、`dt` 合理时出数。"""
    out: list[RatePoint] = []
    for prev, cur in zip(points, points[1:]):
        if prev.segment_id != cur.segment_id:
            out.append(RatePoint(cur.ts, prev.ts, _dt_h(prev.ts, cur.ts), None,
                                 cur.segment_id, "segment_changed"))
            continue
        dt_h = _dt_h(prev.ts, cur.ts)
        if prev.value is None or cur.value is None:
            out.append(RatePoint(cur.ts, prev.ts, dt_h, None, cur.segment_id, "no_value"))
            continue
        if dt_h < min_span_h:
            out.append(RatePoint(cur.ts, prev.ts, dt_h, None, cur.segment_id, "too_short"))
            continue
        if dt_h > max_gap_h:
            out.append(RatePoint(cur.ts, prev.ts, dt_h, None, cur.segment_id, "gap"))
            continue
        out.append(RatePoint(cur.ts, prev.ts, dt_h,
                             (cur.value - prev.value) / dt_h, cur.segment_id, "ok"))
    return out


def _dt_h(a: str, b: str) -> float:
    return (_parse_ts(b) - _parse_ts(a)).total_seconds() / 3600.0


def layer_median(points_by_anchor: dict[str, list[CurvePoint]]) -> list[LayerPoint]:
    """每个时间点对**有值**的锚点取中位；没有点的锚点不进分母。"""
    by_ts: dict[str, list[float]] = {}
    for points in points_by_anchor.values():
        for p in points:
            if p.value is not None:
                by_ts.setdefault(p.ts, []).append(p.value)
    return [LayerPoint(ts=ts, value=float(np.median(vals)), n_anchors=len(vals))
            for ts, vals in sorted(by_ts.items())]


def assert_no_delta_in_curve() -> None:
    """`delta` 不是曲线字段；任何把变更量塞进主曲线的实现都应在这里失败。"""
    names = {f.name for f in fields(CurvePoint)}
    if "delta" in names:
        raise AssertionError("CurvePoint 不得携带 delta：它是 QA 量，不是每帧量（spec §4.7）")
