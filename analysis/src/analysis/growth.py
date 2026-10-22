"""生长判断：把「这一框现在长什么样」变成「比上一个时间点长了多少、算不算在长」。

## 为什么单独一层

`measure` 给出的是**一次测量**（`mean_len_mm` / `mean_cap_mm` / p10 / p90 / 质量标记），
`align.hourly_rates` 给出的是**整条曲线的逐小时差分**（用于和环境做相关性）。
但现场每天真正要回答的是第三个问题：**"这一框跟上一个时间点比，长了没有？"**
——它需要一个明确的判定（在长 / 停滞 / 缩了 / 没法比），而不是让运维自己看两条曲线。

所以本模块的产出是**带判词**的对比：

    GrowthDelta(prev_ts, ts, dt_h, d_len_mm, rate_len_mm_per_h, verdict="stalled", note=…)

## 判定口径（都在参数里，可在调用处改）

| 判词 | 条件 |
| --- | --- |
| `no_prev` | 该框只有这一个时间点——没有可比对象 |
| `not_comparable` | 上一个点质量不可用，或两次采样数太少 |
| `growing` | 速率 ≥ `stall_mm_per_h`（默认 0.1 mm/h） |
| `stalled` | 速率落在 ±`stall_mm_per_h` 内 **且** 间隔够长（≥ `min_span_h`） |
| `shrinking` | 速率 ≤ −`stall_mm_per_h`（菇体变短：测量口径变了 / 采错了 / 真的掉头） |
| `interval_too_short` | 两个点挨得太近（< `min_span_h`），差分被噪声主导，**不下"停滞"结论** |

三条刻意的保守规则：

1. **间隔太短不下结论**。两轮巡检相隔几分钟时，长得再快也差不出一个像素；把这种
   差分报成 `stalled` 会让运维半夜收到假警报。所以宁可说"间隔太短，看不出"。
2. **样本太少不下结论**（任一端 `n < min_n`）：一帧里只认出 1 朵菇的均值没有代表性。
3. **`shrinking` 不当成"正常"**。菇不会变短。出现负增长意味着测量口径变了（换角度档、
   镜头脏了、菌盖被遮），所以要单独一类，让人去看图，而不是混在"停滞"里。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STALL_MM_PER_H = 0.1      # 判"停滞"的速率带宽（mm/h）
MIN_SPAN_H = 0.5          # 两个点至少隔这么久才敢下"停滞"结论
MIN_N = 2                 # 每次测量的最少检出朵数
#: 检出朵数比的"同一群体"区间（当前 / 上一次）。菌簇量是**按检出朵数求的均值**，
#: 朵数一变就不是同一群体：2026-10-22 生产 B206 菌簇直径 92.9 → 46.1 mm，
#: 同时 n 从 31 掉到 12——那不是"菇缩水"，是这一帧只认出了一小撮。
N_RATIO_MIN = 0.6
N_RATIO_MAX = 1.6
VERDICTS = ("growing", "stalled", "shrinking", "no_prev",
            "not_comparable", "interval_too_short")

#: 长势量优先级（票 11 §9）：**越靠前越优先，但必须两点同时有值才可比**。
#: 平视单档下 `mean_len_mm`（杆长）缺测，实际会落到 `mean_projected_d_mm`（投影径, mm）
#: 或 `mean_area_mm2`（面积, mm²）。绝不拿一个点的长度和另一个点的面积相减。
GROWTH_METRICS = (
    ("mean_len_mm", "mm"),
    ("mean_projected_d_mm", "mm"),
    ("mean_area_mm2", "mm2"),
)

#: 面积是二维量，**绝对**带宽（0.1 mm²/h）没有意义：用相对带宽。
#: 这两个默认值都**未定标**（票 16），响应里必须带 `band_source` 说明。
REL_STALL_PER_H = 0.01

#: 判词 → 给运维看的一句话（前端直接显示，不用自己拼）
VERDICT_TEXT = {
    "growing": "在长",
    "stalled": "基本没长",
    "shrinking": "比上一时间点更短（看图核对）",
    "no_prev": "没有上一个时间点",
    "not_comparable": "两次测量不可比",
    "interval_too_short": "距上一时间点太近，看不出趋势",
}


@dataclass(frozen=True)
class GrowthPoint:
    """一个时间点的测量摘要（`measurements` 表一行的域对象）。"""

    ts: str
    box_id: str
    n: int | None = None
    mean_len_mm: float | None = None
    mean_cap_mm: float | None = None
    p10_len: float | None = None
    p90_len: float | None = None
    quality: str | None = None
    # 菌簇范围（票 11 §1）：平视单档下**唯一可用的长势量**。
    mean_cluster_w_mm: float | None = None
    mean_cluster_h_mm: float | None = None
    mean_area_mm2: float | None = None
    mean_projected_d_mm: float | None = None

    @classmethod
    def from_row(cls, row: dict) -> GrowthPoint:
        return cls(
            ts=str(row.get("ts")),
            box_id=str(row.get("box_id") or ""),
            n=row.get("n"),
            mean_len_mm=row.get("mean_len_mm"),
            mean_cap_mm=row.get("mean_cap_mm"),
            p10_len=row.get("p10_len"),
            p90_len=row.get("p90_len"),
            quality=row.get("quality"),
            mean_cluster_w_mm=row.get("mean_cluster_w_mm"),
            mean_cluster_h_mm=row.get("mean_cluster_h_mm"),
            mean_area_mm2=row.get("mean_area_mm2"),
            mean_projected_d_mm=row.get("mean_projected_d_mm"),
        )

    def value(self, metric: str) -> float | None:
        """某个长势量的值；没有这一列/这一列为空就返回 None。"""
        got = getattr(self, metric, None)
        return None if got is None else float(got)

    @property
    def verified(self) -> bool:
        """质量标记里必须含 `verified`（票 11 §3）：整簇不可分时那些值是**估出来的**。"""
        return "verified" in (self.quality or "")

    @property
    def usable(self) -> bool:
        """这一行本身能不能拿来比：质量可信 + 检出朵数够。

        **刻意不要求 `mean_len_mm`**（票 11 §9）：平视单档下杆长恒 NULL，
        原来那句 `mean_len_mm is not None` 会让每一行都变成 `not_comparable`。
        用哪个长势量、以及两点有没有**同一个**量，由 `compare` 决定。
        """
        return self.verified and (self.n or 0) >= MIN_N


@dataclass(frozen=True)
class GrowthDelta:
    """当前点 vs 上一个点的对比结论。

    **量纲（票 05 / CONTEXT.md）：** 平视单档下杆长缺测，比较的是菌簇量，
    所以结果必须带 `metric` + `unit`（"每天 +320 mm²"与"每天 +1.2 mm"不是一回事）。
    旧的 `d_len_mm` / `rate_len_mm_per_h` **只在 `metric == 'mean_len_mm'` 时非空** ——
    绝不让一个叫 `_mm_` 的字段承载 mm²。
    """

    box_id: str
    ts: str
    prev_ts: str | None
    dt_h: float | None
    d_len_mm: float | None
    d_cap_mm: float | None
    rate_len_mm_per_h: float | None
    rate_cap_mm_per_h: float | None
    verdict: str
    note: str
    n_now: int | None = None
    n_prev: int | None = None
    #: 本次比较用的长势量（`GROWTH_METRICS` 里的列名）与单位；没有可共用量时为 None
    metric: str | None = None
    unit: str | None = None
    d_value: float | None = None
    rate_per_h: float | None = None
    band: float | None = None
    band_source: str | None = None
    #: 这个点为什么不能当"长势"读（`unverified` / `few_clusters` / `n_shift` /
    #: `backwards` / `short_interval` / `no_common_metric`）。**只标注，不改数、不删点**。
    flags: tuple[str, ...] = ()
    #: 检出朵数比（当前 / 上一次）；两边都 > 0 时才给。菌簇均值依赖朵数，朵数变了
    #: 就不是同一群体——2026-10-22 生产 B206：n 31→12、菌簇直径 92.9→46.1 mm。
    n_ratio: float | None = None

    @property
    def verdict_text(self) -> str:
        return VERDICT_TEXT.get(self.verdict, self.verdict)

    def to_row(self) -> dict:
        return {
            "box_id": self.box_id,
            "ts": self.ts,
            "prev_ts": self.prev_ts,
            "dt_h": _round(self.dt_h, 3),
            "d_len_mm": _round(self.d_len_mm, 3),
            "d_cap_mm": _round(self.d_cap_mm, 3),
            "rate_len_mm_per_h": _round(self.rate_len_mm_per_h, 4),
            "rate_cap_mm_per_h": _round(self.rate_cap_mm_per_h, 4),
            "metric": self.metric,
            "unit": self.unit,
            "d_value": _round(self.d_value, 3),
            "rate_per_h": _round(self.rate_per_h, 4),
            "band": _round(self.band, 4),
            "band_source": self.band_source,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "note": self.note,
            "n_now": self.n_now,
            "n_prev": self.n_prev,
            "flags": list(self.flags),
            "n_ratio": _round(self.n_ratio, 3),
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _hours(prev_ts: str, ts: str) -> float | None:
    """两个 ISO 时间差（小时）。解析不了就返回 None——**不猜**。"""
    try:
        a = datetime.fromisoformat(prev_ts)
        b = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds() / 3600.0


def _present_metrics(point: GrowthPoint) -> str:
    names = [f"{metric}={point.value(metric):g}" for metric, _ in GROWTH_METRICS
             if point.value(metric) is not None]
    return "、".join(names) or "无"


def _common_metric(prev: GrowthPoint, now: GrowthPoint) -> tuple[str, str] | None:
    """两点**同时有值**的最高优先长势量。没有就返回 None —— 不可比，不猜。"""
    for metric, unit in GROWTH_METRICS:
        if prev.value(metric) is not None and now.value(metric) is not None:
            return metric, unit
    return None


def _stall_band(unit: str, prev_value: float) -> tuple[float, str]:
    """判"停滞"的速率带宽。**默认值未定标**（票 16），所以响应里必须带 `band_source`。"""
    if unit == "mm":
        return abs(STALL_MM_PER_H), "default_uncalibrated"
    # 面积是二维量，绝对值带宽没有意义：用上一时间点的相对比例。
    return abs(prev_value) * REL_STALL_PER_H, "default_uncalibrated_relative"


def compare(prev: GrowthPoint, now: GrowthPoint, *,
            stall_mm_per_h: float = STALL_MM_PER_H,
            min_span_h: float = MIN_SPAN_H,
            min_n: int = MIN_N,
            require_verified: bool = True) -> GrowthDelta:
    """两个时间点的对比。**任何"不确定"都落到保守判词上**（见模块顶部三条规则）。

    与长度无关（票 11 §9）：用两点**同时有值**的最高优先长势量来比，并如实带回
    `metric`/`unit`；没有任何共用量时判 `not_comparable`，**不拿两个不同的量相减**。
    """
    base = {"box_id": now.box_id or prev.box_id, "ts": now.ts, "prev_ts": prev.ts,
            "n_now": now.n, "n_prev": prev.n, "d_len_mm": None, "d_cap_mm": None,
            "rate_len_mm_per_h": None, "rate_cap_mm_per_h": None, "dt_h": None,
            "metric": None, "unit": None, "d_value": None, "rate_per_h": None,
            "band": None, "band_source": None}

    dt = _hours(prev.ts, now.ts)
    if dt is not None:
        base["dt_h"] = dt

    why: list[str] = []
    flags: list[str] = []
    if require_verified:
        if not prev.verified:
            why.append(f"上一个点质量标记不可用（{prev.quality or '无标记'}）")
            flags.append("unverified")
        if not now.verified:
            why.append(f"当前点质量标记不可用（{now.quality or '无标记'}）")
            flags.append("unverified")
    if (prev.n or 0) < min_n or (now.n or 0) < min_n:
        why.append(f"检出朵数不足 {min_n}（{prev.n} → {now.n}）")
        flags.append("few_clusters")
    # 朵数变了 ⇒ 均值不是同一群体（菇不会缩水，是这一帧认出的朵数变了）
    n_ratio: float | None = None
    if prev.n and now.n:
        n_ratio = float(now.n) / float(prev.n)
        if n_ratio < N_RATIO_MIN or n_ratio > N_RATIO_MAX:
            why.append(
                f"检出朵数 {prev.n} → {now.n}（{n_ratio:.2f} 倍）："
                "菌簇均值不是同一群体，不能当长势读"
            )
            flags.append("n_shift")
    common = _common_metric(prev, now)
    if common is None:
        why.append("两次没有同一个可用的长势量"
                   f"（上一个：{_present_metrics(prev)}；这一个：{_present_metrics(now)}）")
        flags.append("no_common_metric")
    if why:
        return GrowthDelta(**base, verdict="not_comparable", note="；".join(why),
                           flags=tuple(dict.fromkeys(flags)), n_ratio=n_ratio)

    metric, unit = common                                      # type: ignore[misc]
    prev_value = prev.value(metric)
    now_value = now.value(metric)
    delta = float(now_value) - float(prev_value)               # type: ignore[arg-type]
    base["metric"] = metric
    base["unit"] = unit
    base["d_value"] = delta
    if metric == "mean_len_mm":
        # 旧字段只在**真的量了杆长**时才写：一个叫 `_mm_` 的字段不许承载 mm²
        base["d_len_mm"] = delta
        if now.mean_cap_mm is not None and prev.mean_cap_mm is not None:
            base["d_cap_mm"] = float(now.mean_cap_mm) - float(prev.mean_cap_mm)

    if dt is None:
        return GrowthDelta(**base, verdict="not_comparable",
                           note="时间戳解析不了，算不出速率",
                           flags=tuple(dict.fromkeys(flags)), n_ratio=n_ratio)
    if dt <= 0:
        return GrowthDelta(**base, verdict="not_comparable",
                           note=f"时间没有前进（{prev.ts} → {now.ts}）",
                           flags=tuple(dict.fromkeys(flags)), n_ratio=n_ratio)
    if dt < min_span_h:
        return GrowthDelta(**base, verdict="interval_too_short",
                           note=f"只隔了 {dt * 60:.0f} 分钟（< {min_span_h * 60:.0f} 分钟），"
                                "差分被噪声主导",
                           flags=tuple(dict.fromkeys([*flags, "short_interval"])),
                           n_ratio=n_ratio)

    rate = delta / dt
    base["rate_per_h"] = rate
    if metric == "mean_len_mm":
        base["rate_len_mm_per_h"] = rate
    if base["d_cap_mm"] is not None:
        base["rate_cap_mm_per_h"] = base["d_cap_mm"] / dt

    band, band_source = _stall_band(unit, float(prev_value))    # type: ignore[arg-type]
    base["band"] = band
    base["band_source"] = band_source
    how = f"{rate:+.3g} {unit}/h（{dt:.1f} 小时内 {delta:+.3g} {unit}）"

    if rate > band:
        verdict, note = "growing", f"在长：平均 {how}"
    elif rate < -band:
        if metric == "mean_len_mm":
            verdict, note = "shrinking", (f"平均 {how} —— 菇不会变短，先看图核对口径")
        else:
            verdict, note = "shrinking", (
                f"平均 {how} —— 菌簇量变小可能是检出差异/遮挡/视角变化，先看图核对"
            )
    else:
        verdict, note = "stalled", f"平均 {how}，落在 ±{band:.3g} {unit}/h 内（{band_source}）"
    if verdict == "shrinking":
        # 菇不会变短：负增长不是"长势"，是这一帧的测量不可比（遮挡 / 换档 / 认错群）
        flags.append("backwards")
    if now.quality or prev.quality:
        note += f"（有质量标记：{prev.quality or '-'} → {now.quality or '-'}）"
    return GrowthDelta(**base, verdict=verdict, note=note,
                       flags=tuple(dict.fromkeys(flags)), n_ratio=n_ratio)


def series(rows: list[dict], box_id: str, *, limit: int | None = None, **kw) -> list[GrowthDelta]:
    """某框的整条对比序列（按时间升序，第一个点是 `no_prev`）。"""
    mine = sorted((GrowthPoint.from_row(r) for r in rows if str(r.get("box_id")) == box_id),
                  key=lambda p: p.ts)
    if limit:
        mine = mine[-limit:]
    out: list[GrowthDelta] = []
    for i, point in enumerate(mine):
        if i == 0:
            out.append(GrowthDelta(box_id=point.box_id, ts=point.ts, prev_ts=None, dt_h=None,
                                   d_len_mm=None, d_cap_mm=None, rate_len_mm_per_h=None,
                                   rate_cap_mm_per_h=None, verdict="no_prev",
                                   note="这是该框最早的一个时间点", n_now=point.n))
        else:
            out.append(compare(mine[i - 1], point, **kw))
    return out


def latest_delta(rows: list[dict], box_id: str, **kw) -> GrowthDelta | None:
    """某框最新一个时间点的对比结论（页面上一行字就够用的那个）。"""
    got = series(rows, box_id, **kw)
    return got[-1] if got else None


def room_summary(rows: list[dict], *, boxes: list[str] | None = None, **kw) -> dict:
    """整间库房的判断：每个框的最新结论 + 按判词计数。

    ``boxes`` 给定时，**一个都不能少**地出现在结果里（缺数据的框报 `no_prev`）——
    现场最怕的是"列表里少了几个框"，那看不出是没长还是没拍。
    """
    ids = sorted(boxes) if boxes else sorted({str(r.get("box_id")) for r in rows if r.get("box_id")})
    per_box: dict[str, dict] = {}
    counts: dict[str, int] = {v: 0 for v in VERDICTS}
    for box in ids:
        delta = latest_delta(rows, box, **kw)
        if delta is None:
            counts["no_prev"] += 1
            per_box[box] = GrowthDelta(
                box_id=box, ts="", prev_ts=None, dt_h=None, d_len_mm=None, d_cap_mm=None,
                rate_len_mm_per_h=None, rate_cap_mm_per_h=None,
                verdict="no_prev", note="该框还没有任何测量记录",
            ).to_row()
            continue
        counts[delta.verdict] = counts.get(delta.verdict, 0) + 1
        per_box[box] = delta.to_row()
    return {
        "n_boxes": len(ids),
        "counts": {k: v for k, v in counts.items() if v},
        "boxes": per_box,
    }
