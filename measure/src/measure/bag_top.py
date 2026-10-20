"""袋顶基线：模板折线经配准变换映射到本帧，并按两级可用性判定。

## 它是什么 / 不是什么

`b(x)` 是**袋顶（袋口）**在某一列的 y，单位原图像素；`H_profile` 用它当上边界，
所以它的误差会**一比一**进入高度（spec §9.5：`b(x)` MAE ≤4 px）。

- 几何来自**模板**（票 08 的 `bag_slots[].top_line_ref`），不是每帧重新猜；
- 映射只走票 11 的 `Alignment.to_moving_points()`，**方向不许自己重写**；
- 本模块**不读图**、不检测袋、不量高度；`bag_mask` 只是可选的"外部遮挡/袋口偏移"证据。

## 两级可用性（spec §4.3）

可用 = 对齐接受 ∧ 槽位匹配 ∧ 袋口偏移 ≤ `bag_shift_max_px` ∧ 可见列 ≥ `min_baseline_columns`
∧ 覆盖槽宽 ≥ `min_baseline_coverage`。任一不满足 ⇒ `baseline_valid=false` 且 `b=None`。

**菌簇自遮挡不算遮挡**：簇越长越会挡住袋口，这是预期内的事。只有外部遮挡
（人手/管线/水汽）才通过 `occluded` 或 `bag_mask` 缺列反映出来。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class BaselineConfig:
    """阈值初值（spec §4.5 同一套冻结纪律：开发集后冻结，冻结后不回改）。"""

    #: 袋口相对模板槽位的偏移上限（**不是**票 11 的整帧机位平移上限）
    bag_shift_max_px: float = 40.0
    min_baseline_columns: int = 8
    min_baseline_coverage: float = 0.2


@dataclass(frozen=True)
class BaselineProfile:
    """一帧、一个槽位的袋顶基线。

    `b` 只覆盖当前帧的 `[x0, x0+len(b))` 列；**区间外不存在基线**，不做外推。
    `valid_columns` 是真正有可见证据的列（不含插值列）；`interpolated` 是区间内
    由折线补出的列，两者互斥。
    """

    slot_id: str
    x0: int
    b: np.ndarray | None
    valid_columns: np.ndarray
    interpolated: np.ndarray
    baseline_valid: bool
    reasons: tuple[str, ...] = ()
    bag_shift_px: float | None = None
    coverage: float = 0.0
    interpolated_ratio: float = 0.0

    @property
    def columns(self) -> np.ndarray:
        return np.arange(self.x0, self.x0 + len(self.b)) if self.b is not None else np.empty(0, int)


def _empty(slot_id: str, *reasons: str) -> BaselineProfile:
    return BaselineProfile(
        slot_id=slot_id, x0=0, b=None,
        valid_columns=np.zeros(0, bool), interpolated=np.zeros(0, bool),
        baseline_valid=False, reasons=tuple(reasons),
    )


def _monotone_x(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按 x 排序并去掉重复列（垂直线段会让同一列出现多次）。"""
    order = np.argsort(xs, kind="stable")
    xs, ys = xs[order], ys[order]
    keep = np.ones(len(xs), bool)
    keep[1:] = xs[1:] != xs[:-1]
    return xs[keep], ys[keep]


def map_bag_top(
    top_line_ref,
    alignment,
    *,
    slot_id: str,
    occluded=None,
    bag_mask: np.ndarray | None = None,
    template_slot_ids=None,
    config: BaselineConfig | None = None,
) -> BaselineProfile:
    """模板折线 → 本帧 `b(x)` + 可用性。

    - `top_line_ref`：模板参考帧坐标的袋顶折线，`(N,2)` 的 `(x, y)`；
    - `alignment`：票 11 的 `Alignment`；失败态直接返回 `baseline_unavailable`；
    - `occluded`：**外部遮挡**的全宽布尔掩码（可选）；菌簇自遮挡**不要**传进来；
    - `bag_mask`：本帧袋实例掩码（可选）；有它才能核"袋口偏移"与"该列确实有袋"。
    """
    cfg = config or BaselineConfig()
    if not getattr(alignment, "ok", False):
        return _empty(slot_id, getattr(alignment, "status", "registration_failed"))

    line = np.asarray(top_line_ref, dtype=float)
    if line.ndim != 2 or line.shape[1] != 2 or len(line) < 2:
        raise ValueError("top_line_ref 必须是 (N≥2, 2) 的 (x, y) 折线")

    mapped = np.asarray(alignment.to_moving_points(line), dtype=float)
    xs, ys = _monotone_x(mapped[:, 0], mapped[:, 1])
    if len(xs) < 2:
        return _empty(slot_id, "slot_missing")

    x0 = int(np.floor(xs.min()))
    x1 = int(np.floor(xs.max())) + 1
    if x1 - x0 < 2:
        return _empty(slot_id, "slot_missing")

    h, w = alignment.moving_hw
    columns = np.arange(x0, x1)
    in_frame = (columns >= 0) & (columns < w)
    b = np.interp(columns, xs, ys)

    valid = in_frame.copy()
    if occluded is not None:
        occ = np.asarray(occluded, dtype=bool)
        if occ.shape != (h, w):
            raise ValueError(f"occluded 形状应为 {(h, w)}，实际 {occ.shape}")
        valid &= ~occ[:, columns.clip(0, w - 1)].any(axis=0)

    profile = BaselineProfile(
        slot_id=slot_id, x0=x0, b=b,
        valid_columns=valid,
        interpolated=np.zeros(len(columns), bool),
        baseline_valid=True,
        coverage=float(valid.mean()) if len(valid) else 0.0,
    )
    return evaluate_baseline(profile, bag_mask=bag_mask,
                             template_slot_ids=template_slot_ids, config=cfg)


def _bag_top_y(bag_mask: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每列袋掩码的上边界 y；该列没有袋像素 ⇒ `(nan, False)`。"""
    ys = np.full(len(columns), np.nan, float)
    has = np.zeros(len(columns), bool)
    for i, x in enumerate(columns):
        if not (0 <= x < bag_mask.shape[1]):
            continue
        rows = np.flatnonzero(bag_mask[:, x])
        if len(rows):
            ys[i] = float(rows[0])
            has[i] = True
    return ys, has


def evaluate_baseline(
    profile: BaselineProfile,
    *,
    bag_mask: np.ndarray | None = None,
    template_slot_ids=None,
    config: BaselineConfig | None = None,
) -> BaselineProfile:
    """按 §4.3 的五道门重判；失败时 `b=None`，**不猜**。"""
    cfg = config or BaselineConfig()
    reasons: list[str] = []

    if profile.b is None:
        return replace(profile, baseline_valid=False,
                       reasons=profile.reasons or ("baseline_unavailable",))

    if template_slot_ids is not None and profile.slot_id not in set(template_slot_ids):
        reasons.append("slot_missing")
        return replace(profile, b=None, baseline_valid=False, reasons=tuple(reasons))

    valid = profile.valid_columns.copy()
    shift: float | None = None
    if bag_mask is not None:
        ys, has = _bag_top_y(np.asarray(bag_mask, bool), profile.columns)
        valid &= has
        both = valid & np.isfinite(ys)
        if both.any():
            shift = float(np.median(np.abs(profile.b[both] - ys[both])))

    interpolated = ~valid
    coverage = float(valid.mean()) if len(valid) else 0.0
    profile = replace(profile, valid_columns=valid, interpolated=interpolated,
                      coverage=coverage,
                      interpolated_ratio=float(interpolated.mean()) if len(valid) else 0.0,
                      bag_shift_px=shift)

    if shift is not None and shift > cfg.bag_shift_max_px:
        reasons.append("bag_shift_exceeded")
    if int(valid.sum()) < cfg.min_baseline_columns:
        reasons.append("too_few_columns")
    if coverage < cfg.min_baseline_coverage:
        reasons.append("coverage_low")

    if reasons:
        return replace(profile, b=None, baseline_valid=False, reasons=tuple(reasons))
    return replace(profile, baseline_valid=True, reasons=())
