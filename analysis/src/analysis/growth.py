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
VERDICTS = ("growing", "stalled", "shrinking", "no_prev",
            "not_comparable", "interval_too_short")

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
        )

    @property
    def usable(self) -> bool:
        """这一行能不能拿来比：长度有值，且检出朵数够。"""
        return self.mean_len_mm is not None and (self.n or 0) >= MIN_N


@dataclass(frozen=True)
class GrowthDelta:
    """当前点 vs 上一个点的对比结论。"""

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
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "note": self.note,
            "n_now": self.n_now,
            "n_prev": self.n_prev,
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


def compare(prev: GrowthPoint, now: GrowthPoint, *,
            stall_mm_per_h: float = STALL_MM_PER_H,
            min_span_h: float = MIN_SPAN_H,
            min_n: int = MIN_N) -> GrowthDelta:
    """两个时间点的对比。**任何"不确定"都落到保守判词上**（见模块顶部三条规则）。"""
    base = {"box_id": now.box_id or prev.box_id, "ts": now.ts, "prev_ts": prev.ts,
            "n_now": now.n, "n_prev": prev.n, "d_len_mm": None, "d_cap_mm": None,
            "rate_len_mm_per_h": None, "rate_cap_mm_per_h": None, "dt_h": None}

    dt = _hours(prev.ts, now.ts)
    if dt is not None:
        base["dt_h"] = dt

    if not (now.usable and prev.usable):
        why = []
        if now.mean_len_mm is None or prev.mean_len_mm is None:
            why.append("有一次没测到长度")
        if (now.n or 0) < min_n or (prev.n or 0) < min_n:
            why.append(f"检出朵数不足 {min_n}（{prev.n} → {now.n}）")
        return GrowthDelta(**base, verdict="not_comparable", note="；".join(why))

    d_len = float(now.mean_len_mm) - float(prev.mean_len_mm)      # type: ignore[arg-type]
    d_cap = (None if now.mean_cap_mm is None or prev.mean_cap_mm is None
             else float(now.mean_cap_mm) - float(prev.mean_cap_mm))
    base["d_len_mm"] = d_len
    base["d_cap_mm"] = d_cap

    if dt is None:
        return GrowthDelta(**base, verdict="not_comparable",
                           note="时间戳解析不了，算不出速率")
    if dt <= 0:
        return GrowthDelta(**base, verdict="not_comparable",
                           note=f"时间没有前进（{prev.ts} → {now.ts}）")
    if dt < min_span_h:
        return GrowthDelta(**base, verdict="interval_too_short",
                           note=f"只隔了 {dt * 60:.0f} 分钟（< {min_span_h * 60:.0f} 分钟），"
                                "差分被噪声主导")

    rate = d_len / dt
    base["rate_len_mm_per_h"] = rate
    base["rate_cap_mm_per_h"] = None if d_cap is None else d_cap / dt

    if rate > stall_mm_per_h:
        verdict, note = "growing", f"平均 {rate:.2f} mm/h（{dt:.1f} 小时内长了 {d_len:.1f} mm）"
    elif rate < -stall_mm_per_h:
        verdict, note = "shrinking", (f"平均 {rate:.2f} mm/h（短了 {abs(d_len):.1f} mm）"
                                      "——菇不会变短，先看图核对口径")
    else:
        verdict, note = "stalled", f"平均 {rate:.2f} mm/h，落在 ±{stall_mm_per_h} 内"
    if now.quality or prev.quality:
        note += f"（有质量标记：{prev.quality or '-'} → {now.quality or '-'}）"
    return GrowthDelta(**base, verdict=verdict, note=note)


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
