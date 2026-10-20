"""每帧簇高主指标：`H_profile` / `H_p50` / `H_p90`（spec §4.1）。

## 三个量回答同一件事

`H_* = b(x) − t(x)`：**袋口以上多高**。不是菌杆长度，也不是簇外接框高度。

- `H_profile` = `mean_{x∈V}`，`V` 由三条规则合成（列连续 / 顶部段可靠 / 连到基线）；
- `H_p50` = `median`，抗顶部毛刺；
- `H_p90` = `quantile(..., 0.9)`，描述最高的那批结构；**它就是原 `robust_top_height_px`**。

三者同帧、同 `b(x)`、同 `V`，**同生同灭**：只要 `height_valid=false`，三个都是 `None`。

## 逐列证据

只返回三个标量是不够的：§9.5 要按 `t(x)`/`b(x)` 逐项报误差，所以 `top_envelope`、
`column_heights`、`valid_columns` 与拒绝计数必须一起返回。无效列**不许用插值补进统计**
（探索链路的 `np.interp` 空洞 bug 同款）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from measure.bag_top import BaselineProfile
from measure.imageops import opening


@dataclass(frozen=True)
class ProfileConfig:
    """初值来自 spec §4.5；冻结后不得因结果不好回改。"""

    min_col_run_px: int = 3
    max_gap_px: int = 5
    reach_tol_px: int = 8
    min_valid_column_ratio: float = 0.3
    top_quantile: float = 0.9
    outlier_quantile: float = 0.995


@dataclass(frozen=True)
class HeightProfile:
    h_profile: float | None
    h_p50: float | None
    h_p90: float | None
    raw_top_height_px: float | None
    #: 敏感性证据：把 `baseline.interpolated` 的列也算进来时的均值；**不进主输出**
    h_profile_with_interp: float | None
    top_envelope: np.ndarray       # 原图宽，V 外为 NaN
    column_heights: np.ndarray     # 原图宽，V 外为 NaN
    valid_columns: np.ndarray      # 原图宽，bool
    valid_column_count: int
    valid_column_ratio: float
    supported_column_count: int
    status: str
    reasons: tuple[str, ...] = ()
    rejected: dict[str, int] | None = None


def _runs(column: np.ndarray) -> list[tuple[int, int]]:
    """一列的 True 连续段（闭区间），从下到上按 y 升序。"""
    idx = np.flatnonzero(column)
    if len(idx) == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(idx) - 1]
    return [(int(idx[s]), int(idx[e])) for s, e in zip(starts, ends)]


def _column_height(column: np.ndarray, baseline_y: float,
                   cfg: ProfileConfig) -> tuple[float | None, str]:
    """一列是否进 `V`；返回 `(h, 空串)` 或 `(None, 拒绝码)`。"""
    runs = _runs(column)
    if not runs:
        return None, "no_foreground"
    top, top_end = runs[0]
    if top_end - top + 1 < cfg.min_col_run_px:
        return None, "short_run"
    if baseline_y < top:
        return None, "baseline_above_top"
    # 规则 2：最高段与下一段之间断开太多 ⇒ 顶端那段不是"这一列的顶"
    if len(runs) > 1 and runs[1][0] - top_end - 1 > cfg.max_gap_px:
        return None, "top_gap"
    # 规则 3：从 t(x) 向下必须能走到 b(x)（逐段间隙 ≤ reach_tol）
    if baseline_y <= top_end + cfg.reach_tol_px:
        return float(baseline_y - top), ""
    prev_end = top_end
    for start, end in runs[1:]:
        if start - prev_end - 1 > cfg.reach_tol_px:
            break
        if start - cfg.reach_tol_px <= baseline_y <= end + cfg.reach_tol_px:
            return float(baseline_y - top), ""
        prev_end = end
    return None, "no_reach"


def profile_heights(
    cluster_mask: np.ndarray,
    baseline: BaselineProfile,
    *,
    association_valid: bool = True,
    measurement_valid: bool = True,
    config: ProfileConfig | None = None,
) -> HeightProfile:
    """`cluster_mask` + `b(x)` → 三个主指标 + 逐列证据。"""
    cfg = config or ProfileConfig()
    mask = np.asarray(cluster_mask, dtype=bool)
    h_img, w_img = mask.shape
    mask = opening(mask, radius=1)

    top_env = np.full(w_img, np.nan, float)
    heights = np.full(w_img, np.nan, float)
    valid = np.zeros(w_img, bool)

    b_full = np.full(w_img, np.nan, float)
    interp_full = np.zeros(w_img, bool)
    if baseline.b is not None:
        x0 = baseline.x0
        x1 = x0 + len(baseline.b)
        lo, hi = max(0, x0), min(w_img, x1)
        if hi > lo:
            b_full[lo:hi] = baseline.b[lo - x0:hi - x0]
            interp_full[lo:hi] = baseline.interpolated[lo - x0:hi - x0]

    supported = int(np.sum(mask.any(axis=0) & np.isfinite(b_full)))
    rejected: dict[str, int] = {}

    for x in range(w_img):
        if not mask[:, x].any() or not np.isfinite(b_full[x]):
            continue
        h, why = _column_height(mask[:, x], float(b_full[x]), cfg)
        if h is None:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        top_env[x] = float(b_full[x] - h)
        heights[x] = h
        if not interp_full[x]:
            valid[x] = True

    count = int(valid.sum())
    ratio = float(count / supported) if supported else 0.0
    reasons: list[str] = []
    if not baseline.baseline_valid:
        reasons.append("baseline_invalid")
    if not association_valid:
        reasons.append("association_invalid")
    if not measurement_valid:
        reasons.append("measurement_invalid")
    if count == 0:
        reasons.append("no_columns")
    if ratio < cfg.min_valid_column_ratio:
        reasons.append("insufficient_contour")

    base = dict(
        top_envelope=top_env, column_heights=heights, valid_columns=valid,
        valid_column_count=count, valid_column_ratio=ratio,
        supported_column_count=supported, rejected=rejected,
    )

    if reasons:
        return HeightProfile(
            h_profile=None, h_p50=None, h_p90=None, raw_top_height_px=None,
            h_profile_with_interp=None, status=reasons[0], reasons=tuple(reasons), **base)

    values = heights[valid]
    profile = float(np.mean(values))
    with_interp = None
    if interp_full.any():
        extra = np.zeros(w_img, bool)
        for x in range(w_img):
            if interp_full[x] and np.isfinite(heights[x]):
                extra[x] = True
        if extra.any():
            with_interp = float(np.mean(np.r_[values, heights[extra]]))

    return HeightProfile(
        h_profile=profile,
        h_p50=float(np.median(values)),
        h_p90=float(np.quantile(values, cfg.top_quantile, method="linear")),
        raw_top_height_px=float(np.quantile(values, cfg.outlier_quantile, method="linear")),
        h_profile_with_interp=with_interp,
        status="ok", reasons=(), **base)
