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
    segment_key: tuple[str, ...]
    status: str
    usable: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment:
    segment_id: int
    key: tuple[str, ...]
    start_ts: str
    end_ts: str
    n_points: int


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
    segment_id: int
    value: float | None
    n_anchors: int


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def _segment_key(row) -> tuple:
    return tuple(str(_get(row, name, "")) for name in SEGMENT_FIELDS)


def _parse_ts(ts: str) -> datetime:
    """ISO 时间；统一成 naive UTC，避免带/不带时区的两行无法相减。"""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)


def _ts_key(row) -> datetime:
    try:
        return _parse_ts(str(_get(row, "ts", "")))
    except ValueError:
        return datetime.min


def anchor_series(rows, *, metric: str = "h_anchor") -> list[CurvePoint]:
    """锚点级证据行 → 有序序列（含 `missing` 点）。"""
    ordered = sorted(rows, key=_ts_key)
    out: list[CurvePoint] = []
    prev_key: tuple | None = None
    segment_id = -1
    for row in ordered:
        key = _segment_key(row)
        # 段号只在**相邻**行换键时递增：A→B→A 必须是三段，不能把 A 并回第一段。
        if prev_key is None or key != prev_key:
            segment_id += 1
            prev_key = key
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
            segment_id=segment_id,
            segment_key=key,
            status="ok" if value is not None else "missing",
            usable=bool(_get(row, "usable", value is not None)),
            reasons=reasons,
        ))
    return out


def segment(points) -> list[Segment]:
    """连续同段点 → `Segment`；段不合并、不重排。"""
    out: list[Segment] = []
    for p in points:
        if not out or out[-1].segment_id != p.segment_id:
            out.append(Segment(p.segment_id, p.segment_key, p.ts, p.ts, 1))
        else:
            last = out[-1]
            out[-1] = Segment(last.segment_id, last.key, last.start_ts, p.ts,
                              last.n_points + 1)
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
        if dt_h < 0:
            out.append(RatePoint(cur.ts, prev.ts, dt_h, None, cur.segment_id, "out_of_order"))
            continue
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
    """每个 (段, 时间点) 对**有值**的锚点取中位；没有点的锚点不进分母。

    按 (段, 时间) 分组，避免把版本升级前后的点混进同一个中位。
    """
    by_key: dict[tuple[int, str], list[float]] = {}
    for points in points_by_anchor.values():
        for p in points:
            if p.value is not None:
                by_key.setdefault((p.segment_id, p.ts), []).append(p.value)
    ordered = sorted(by_key.items(), key=lambda kv: (kv[0][0], _parse_ts(kv[0][1])))
    return [LayerPoint(ts=ts, segment_id=seg, value=float(np.median(vals)),
                       n_anchors=len(vals))
            for (seg, ts), vals in ordered]


def assert_no_delta_in_curve() -> None:
    """`delta` 不是曲线字段；任何把变更量塞进主曲线的实现都应在这里失败。"""
    names = {f.name for f in fields(CurvePoint)}
    if "delta" in names:
        raise AssertionError("CurvePoint 不得携带 delta：它是 QA 量，不是每帧量（spec §4.7）")
