"""环境序列的聚合与统计（票 02，口径见 ADR-0021）。

原始表 `environment` 是每分钟一条的实测序列；界面按**小时**看、按**天**汇总。
本模块只做纯计算（进 dict、出 dict），HTTP 在 `api.py`，取数在 `db.py`。

**刻意不依赖 pandas**：prod 的 analysis venv 里没有 pandas（只有 `align.py` 用它，
而且是惰性导入），而 `api.py` 在模块级导入本模块——引一个第三方库进来就等于把
"服务起不来"的风险装进每次重启（2026-10-12 就是这么挂过一次）。标准库够用。

补光的 `lamp_state` 是分类量（0=自动 1=手动 2=停止 3=关闭），聚合时取**众数**
（并列取较晚出现的那个）——它不是可以求平均的物理量。想要"点亮分钟"得由设定周期
推导，那是设定值，不要混进这张表。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

#: 库里存的是小时聚合；`raw` 不在这条链上——原始分钟序列按窗口现取 TDengine。
GRANULARITIES = ("hour", "day")
MEASURED_COLS = ("temp_c", "rh_pct", "co2_ppm")
#: 每个实测参数的均值列 → (最小列, 最大列)
AGG_COLUMNS = {"temp_c": ("temp_min", "temp_max"),
               "rh_pct": ("rh_min", "rh_max"),
               "co2_ppm": ("co2_min", "co2_max")}
LAMP_STATE_MEANING = {0: "自动", 1: "手动", 2: "停止", 3: "关闭"}
FIELDS = ("ts", "zone", "temp_c", "temp_min", "temp_max",
          "rh_pct", "rh_min", "rh_max",
          "co2_ppm", "co2_min", "co2_max",
          "lamp_state", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m",
          "lamp_on_min", "n")

#: 循环补光折算：lamp_on_min = 该小时采样点数 × OnSSet/(OnSSet+OffSSet)。
#: 用采样点数而不是固定 60 分钟——断档的小时不该按满一小时虚报点亮时长。
def lamp_minutes(samples: int, on_s, off_s, on_m=None, off_m=None):
    """一个时间窗内的折算点亮分钟。

    现场口径（2026-10-12 用户）：**开启几秒、关闭约两小时**。面板上秒档
    （OnSSet/OffSSet）与分档（OnMSet/OffMSet）并存，而实测值是
    `OnSSet=2`（秒）、`OffSSet=0`、`OffMSet=120`（分 = 2 小时）——
    也就是说**两侧各自取自不同的档**。所以按侧退回：

    * 开启秒数 = `OnSSet`，为 0/空时退回 `OnMSet × 60`；
    * 关闭秒数 = `OffSSet`，为 0/空时退回 `OffMSet × 60`；
    * 两侧都拿不到 → `None`（"没配循环"不是"补光为零"）。

    `samples` 是**分钟数**（每采样点一分钟），点亮分钟 = 分钟数 × 占空比。
    返回的是**设定量折算**，不是照度，也不是实测点亮状态。
    """
    on_eff = _num(on_s) if (_num(on_s) or 0) > 0 else (
        _num(on_m) * 60 if _num(on_m) is not None else None)
    off_eff = _num(off_s) if (_num(off_s) or 0) > 0 else (
        _num(off_m) * 60 if _num(off_m) is not None else None)
    if on_eff is None and off_eff is None:
        return None
    total = (on_eff or 0.0) + (off_eff or 0.0)
    if total <= 0:
        return None
    return round(samples * ((on_eff or 0.0) / total), 4)


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out          # NaN != NaN


def filter_rows(
    rows: list[dict], *, zone: str | None = None, start: str | None = None, end: str | None = None
) -> list[dict]:
    """按库房与时间窗筛选。`start` / `end` 是含端点的本地 ISO8601 前缀比较。"""
    out = []
    for raw in rows or []:
        ts = str(raw.get("ts") or "")
        if not ts:
            continue
        if zone and raw.get("zone") != zone:
            continue
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        out.append({k: raw.get(k) for k in FIELDS})
    out.sort(key=lambda r: (r["ts"], r["zone"] or ""))
    return out


def _bucket(ts: str, granularity: str) -> str:
    return ts[:13] + ":00:00" if granularity == "hour" else ts[:10] + "T00:00:00"


def aggregate_hourly(rows: list[dict]) -> list[dict]:
    """逐分钟采样 → 小时聚合（入库前的那一步）。

    每小时给：均值、最小、最大、采样点数，以及补光状态众数。`n` 不足 60 说明该小时有
    断档——**不补**，让缺口在 `n` 上看得见。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for raw in rows or []:
        ts = str(raw.get("ts") or "")
        if not ts:
            continue
        groups.setdefault((_bucket(ts, "hour"), str(raw.get("zone") or "")), []).append(raw)

    out: list[dict] = []
    for (hour, zone) in sorted(groups):
        group = groups[(hour, zone)]
        row: dict = {"ts": hour, "zone": zone, "n": len(group),
                     "lamp_state": _mode([g.get("lamp_state") for g in group])}
        # 循环参数取该小时**最后一次**设定：一小时里改过循环参数时按改完的值算
        # （这种改法一天最多几次，误差落在边界那一个小时上）。
        cycle = [(g.get("lamp_on_s"), g.get("lamp_off_s"), g.get("lamp_on_m"), g.get("lamp_off_m"))
                 for g in group if any(_num(g.get(k)) is not None for k in
                                       ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m"))]
        on_s, off_s, on_m, off_m = cycle[-1] if cycle else (None, None, None, None)
        row["lamp_on_s"], row["lamp_off_s"] = _num(on_s), _num(off_s)
        row["lamp_on_m"], row["lamp_off_m"] = _num(on_m), _num(off_m)
        row["lamp_on_min"] = lamp_minutes(len(group), on_s, off_s, on_m, off_m)
        for col, (lo_key, hi_key) in AGG_COLUMNS.items():
            values = [v for v in (_num(g.get(col)) for g in group) if v is not None]
            row[col] = _mean(values)
            row[lo_key] = round(min(values), 2) if values else None
            row[hi_key] = round(max(values), 2) if values else None
        out.append(row)
    return out


def _mean(values: list[float]):
    return round(sum(values) / len(values), 2) if values else None


def _mode_weighted(pairs):
    """分类量的加权众数；并列时取**较晚**出现的（输入按时间排序）。

    权重 = 该小时/该天的采样点数：一小时 60 个点和一小时 11 个点，不该等权。
    """
    tally: Counter = Counter()
    order: list[float] = []
    for value, weight in pairs:
        v = _num(value)
        if v is None:
            continue
        tally[v] += max(int(weight or 0), 1)
        order.append(v)
    if not tally:
        return None
    best = max(tally.values())
    for value in reversed(order):
        if tally[value] == best:
            return float(value)
    return float(order[-1])


def _mode(values: list):
    return _mode_weighted([(v, 1) for v in values])


def _quantile(sorted_vals: list[float], q: float):
    """线性插值分位数，与 pandas 默认口径一致（index = q*(n-1)）。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def series(
    rows: list[dict],
    *,
    granularity: str = "hour",
    zone: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """序列。`raw` 原样返回逐条采样；`hour`/`day` 按桶求均值，补光取众数。"""
    if granularity not in GRANULARITIES:
        raise ValueError(
            f"granularity 只支持 {GRANULARITIES}；raw 不在库里，走 GET /environment?granularity=raw 现取"
        )
    kept = filter_rows(rows, zone=zone, start=start, end=end)
    if not kept:
        return []
    if granularity == "hour":
        return [_clean_row(r) for r in kept]

    # day：把小时行再折一层。均值按采样点数加权——一小时 60 个点和一小时 11 个点
    # 不该等权，否则断档会把日均值拽偏。
    days: dict[tuple[str, str], list[dict]] = {}
    for r in kept:
        days.setdefault((_bucket(r["ts"], "day"), r["zone"] or ""), []).append(r)

    out: list[dict] = []
    for (day, z) in sorted(days):
        group = days[(day, z)]
        total = sum(int(g["n"] or 0) for g in group)
        row: dict = {"ts": day, "zone": z, "n": total, "hours": len(group)}
        for col, (lo_key, hi_key) in AGG_COLUMNS.items():
            pairs = [(_num(g[col]), int(g["n"] or 0)) for g in group if _num(g[col]) is not None]
            weight = sum(w for _, w in pairs) or len(pairs)
            row[col] = round(sum(v * w for v, w in pairs) / weight, 2) if pairs else None
            los = [v for v in (_num(g[lo_key]) for g in group) if v is not None]
            his = [v for v in (_num(g[hi_key]) for g in group) if v is not None]
            row[lo_key] = round(min(los), 2) if los else None
            row[hi_key] = round(max(his), 2) if his else None
        row["lamp_state"] = _mode_weighted(
            [(g["lamp_state"], int(g["n"] or 0)) for g in group]
        )
        # 点亮分钟是**时长**，跨小时要相加，不能求平均
        minutes = [v for v in (_num(g.get("lamp_on_min")) for g in group) if v is not None]
        row["lamp_on_min"] = round(sum(minutes), 2) if minutes else None
        keys = ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")
        last = next((g for g in reversed(group)
                     if any(_num(g.get(k)) is not None for k in keys)), None)
        for key in keys:
            row[key] = _num(last.get(key)) if last else None
        out.append(row)
    return out


def _clean_row(r: dict) -> dict:
    """JSON 里不放 NaN：缺就是 None。"""
    out: dict = {"ts": r["ts"], "zone": r["zone"], "n": int(r["n"] or 0)}
    for key in ("temp_c", "temp_min", "temp_max", "rh_pct", "rh_min", "rh_max",
                "co2_ppm", "co2_min", "co2_max",
                "lamp_state", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m",
                "lamp_on_min"):
        out[key] = _num(r.get(key))
    return out


def batches(rows: list[dict], windows: list[dict], *, zone: str | None = None) -> list[dict]:
    """按**批次窗口**汇总环境参数；`windows` 来自生产台账（`analysis.batches` 表）。

    这里有一段被真机证否的弯路：最初想"按数据断档切段"，假设换批时数据会断。
    2026-10-12 实测：库房环境是**连续采集**的，25 天只会切出一段，跨了两批——
    段不等于批次。所以批次边界只认台账，`windows` 里每项是
    `{entry_date, until, batch_no}`，区间取 `[entry_date, until)`。
    """
    kept = filter_rows(rows, zone=zone)
    if not kept or not windows:
        return []

    out: list[dict] = []
    for w in windows:
        start = str(w["entry_date"]) + "T00:00:00"
        until = w.get("until")
        end = str(until) + "T00:00:00" if until else None
        seg = [r for r in kept if r["ts"] >= start and (end is None or r["ts"] < end)]
        if not seg:
            continue
        stats: dict = {
            "batch_no": w.get("batch_no"),
            "entry_date": w["entry_date"],
            # 台账侧的**原始**库房号（可能是旧编号 7/8）+ 归一到规范号的那一列；
            # 页面要能显示「607（原始 7）」，所以两列都得往下带（Chief 2026-10-14）。
            "room_id": w.get("room_id"),
            "room_id_canonical": w.get("room_id_canonical"),
            "start": seg[0]["ts"],
            "end": seg[-1]["ts"],
            "hours": len(seg),
            "samples": sum(int(r["n"] or 0) for r in seg),
            "days": round(len(seg) / 24.0, 1),
            "params": {},
        }
        for col, (lo_key, hi_key) in AGG_COLUMNS.items():
            means = sorted(v for v in (_num(r.get(col)) for r in seg) if v is not None)
            if not means:
                stats["params"][col] = None
                continue
            los = [v for v in (_num(r.get(lo_key)) for r in seg) if v is not None] or means
            his = [v for v in (_num(r.get(hi_key)) for r in seg) if v is not None] or means
            stats["params"][col] = {
                "mean": round(sum(means) / len(means), 2),
                "min": round(min(los), 2),
                "median": round(_quantile(means, 0.5), 2),
                "max": round(max(his), 2),
            }
        minutes = [v for v in (_num(r.get("lamp_on_min")) for r in seg) if v is not None]
        keys = ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")
        last = next((r for r in reversed(seg)
                     if any(_num(r.get(k)) is not None for k in keys)), None)
        stats["lamp"] = {k: (_num(last.get(k)) if last else None) for k in keys}
        stats["lamp"]["on_min_total"] = round(sum(minutes), 2) if minutes else None
        out.append(stats)
    return out


def _hour_gaps(timestamps: list[str], *, limit: int = 48) -> list[str]:
    """按小时找缺口：某个整点一条数据都没有，就是断档（不插值，如实列出）。"""
    hours = sorted({ts[:13] + ":00:00" for ts in timestamps})
    if len(hours) < 2:
        return []
    have = set(hours)
    cur = datetime.fromisoformat(hours[0])
    last = datetime.fromisoformat(hours[-1])
    missing: list[str] = []
    while cur <= last and len(missing) < limit:
        stamp = cur.strftime("%Y-%m-%dT%H:00:00")
        if stamp not in have:
            missing.append(stamp)
        cur += timedelta(hours=1)
    return missing


def summary(
    rows: list[dict], *, zone: str | None = None, start: str | None = None, end: str | None = None
) -> dict:
    """窗口内的分位数统计与缺口清单。

    `sources_missing` 列出**整列无值**的参数——界面据此标注"无数据源"，而不是画一条 0。
    """
    kept = filter_rows(rows, zone=zone, start=start, end=end)
    total = sum(int(r["n"] or 0) for r in kept)
    result: dict = {
        "zone": zone,
        "n": total,                      # 采样点总数（不是行数：一行一小时）
        "hours": len(kept),
        "first_ts": kept[0]["ts"] if kept else None,
        "last_ts": kept[-1]["ts"] if kept else None,
        "params": {},
        "lamp_states": {},
        "gaps": [],
        "incomplete": [],
        "sources_missing": [],
    }
    if not kept:
        result["sources_missing"] = list(MEASURED_COLS) + ["lamp_state"]
        return result

    for col in MEASURED_COLS:
        means = [_num(r[col]) for r in kept]
        means = sorted(v for v in means if v is not None)
        if not means:
            result["sources_missing"].append(col)
            result["params"][col] = None
            continue
        lo_key, hi_key = AGG_COLUMNS[col]
        los = [v for v in (_num(r.get(lo_key)) for r in kept) if v is not None] or means
        his = [v for v in (_num(r.get(hi_key)) for r in kept) if v is not None] or means
        result["params"][col] = {
            # 极值来自存储列（采样点级别的真实最大/最小）；
            # 分位数是"每小时一个点"的分位数——库里已经没有采样点了，别把它读成逐点分位。
            "min": round(min(los), 2),
            "q25": round(_quantile(means, 0.25), 2),
            "median": round(_quantile(means, 0.5), 2),
            "q75": round(_quantile(means, 0.75), 2),
            "max": round(max(his), 2),
            "count": total,
        }

    weighted = [(r["lamp_state"], int(r["n"] or 0)) for r in kept]
    if all(_num(v) is None for v, _ in weighted):
        result["sources_missing"].append("lamp_state")
    else:
        tally: Counter = Counter()
        for value, weight in weighted:
            v = _num(value)
            if v is not None:
                tally[v] += max(weight, 1)
        result["lamp_states"] = {str(int(s)): c for s, c in tally.most_common()}

    # 循环补光：总点亮分钟 + 折算占空比。都是**设定量**（由 OnSSet/OffSSet 推出），
    # 不是照度；面板上要和设备状态一起看。
    totals = [v for v in (_num(r.get("lamp_on_min")) for r in kept) if v is not None]
    keys = ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")
    last = next((r for r in reversed(kept)
                 if any(_num(r.get(k)) is not None for k in keys)), None)
    lamp = {k: (_num(last.get(k)) if last else None) for k in keys}
    on_s, off_s = lamp["lamp_on_s"], lamp["lamp_off_s"]
    on_m, off_m = lamp["lamp_on_m"], lamp["lamp_off_m"]
    on_eff = on_s if (on_s or 0) > 0 else ((on_m or 0) * 60 if on_m is not None else None)
    off_eff = off_s if (off_s or 0) > 0 else ((off_m or 0) * 60 if off_m is not None else None)
    lamp["on_effective_s"] = on_eff
    lamp["off_effective_s"] = off_eff
    total = (on_eff or 0.0) + (off_eff or 0.0)
    lamp["duty"] = round((on_eff or 0.0) / total, 6) if total > 0 else None
    lamp["on_min_total"] = round(sum(totals), 2) if totals else None
    lamp["on_min_per_hour"] = round(sum(totals) / len(kept), 3) if totals and kept else None
    result["lamp"] = lamp

    stamps = [r["ts"] for r in kept]
    result["gaps"] = _hour_gaps(stamps)
    result["incomplete"] = [
        {"ts": r["ts"], "n": int(r["n"] or 0)}
        for r in kept if int(r["n"] or 0) < 60
    ][:48]
    return result
