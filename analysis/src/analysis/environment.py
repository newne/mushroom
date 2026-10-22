"""环境序列的聚合与统计（票 02，口径见 ADR-0021）。

原始表 `environment` 是每分钟一条的实测序列；界面按**小时**看、按**天**汇总。
本模块只做纯计算（进 dict、出 dict），HTTP 在 `api.py`，取数在 `db.py`。

**刻意不依赖 pandas**：prod 的 analysis venv 里没有 pandas（只有 `align.py` 用它，
而且是惰性导入），而 `api.py` 在模块级导入本模块——引一个第三方库进来就等于把
"服务起不来"的风险装进每次重启（2026-10-12 就是这么挂过一次）。标准库够用。

补光的 `lamp_state` 是分类量（0=自动 1=手动 2=停止 3=关闭），聚合时取**众数**
（并列取较晚出现的那个）——它不是可以求平均的物理量。想要"点亮分钟"得由设定周期
推导，那是设定值，不要混进这张表。

补光还要分**蓝光 / 白光**（2026-10-22 用户口径）：`lamp_blue_groups` /
`lamp_white_groups` 是四组灯的光源选择（`Choose1..4`）折出来的**组号清单**，
是设定量，聚合时取该窗口最后一次配置，不做平均、也不做箱线。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from analysis.schema import DAILY_DISTRIBUTION_PARAMS, DAILY_FIVE_SUFFIXES

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
          "lamp_blue_groups", "lamp_white_groups",
          "lamp_on_min", "n")

#: 光照里**文本型**的两列：光源组号清单（`"2,4"`）。求均值/众数都没有意义，
#: 聚合时取"该小时/该天最后一次配置"（设定量，同一小时内改了按改完的算）。
LAMP_TEXT_FIELDS = ("lamp_blue_groups", "lamp_white_groups")

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
        # 光源组号：同一小时内取**最后一次**配置（与循环参数同一口径）
        for key in LAMP_TEXT_FIELDS:
            row[key] = _last_text(group, key)
        for col, (lo_key, hi_key) in AGG_COLUMNS.items():
            values = [v for v in (_num(g.get(col)) for g in group) if v is not None]
            row[col] = _mean(values)
            row[lo_key] = round(min(values), 2) if values else None
            row[hi_key] = round(max(values), 2) if values else None
        out.append(row)
    return out


def _mean(values: list[float]):
    return round(sum(values) / len(values), 2) if values else None


def _last_text(rows: list[dict], key: str):
    """文本型设定列的"最后一次"。

    `None` = 这一窗口从没上报过（数据缺失）；`""` = 上报了，但**这一档没有组**
    （比如四组全选白光 ⇒ 蓝光组为空）。两者必须分得开：前者是没数，后者是**有数且为零**。
    """
    for row in reversed(rows):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


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
        for key in LAMP_TEXT_FIELDS:
            row[key] = _last_text(group, key)
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
    for key in LAMP_TEXT_FIELDS:
        value = r.get(key)
        out[key] = None if value is None else str(value)
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
        # 光源构成：批次对比也要能看出这一批是补蓝光还是补白光（2026-10-22）
        stats["lamp"]["blue_groups"] = _last_text(seg, "lamp_blue_groups")
        stats["lamp"]["white_groups"] = _last_text(seg, "lamp_white_groups")
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

    # 循环补光：总点亮分钟 + 折算占空比。都是**设定量**（由设定周期推出），不是照度。
    #
    # 2026-10-22 用户口径：现场 `OnSSet`/`OffSSet` 取不到值 ⇒ **补光只看设定**
    # （`OnMSet`/`OffMSet` 与四组的光源选择），折算出来的"点亮分钟"不再作为页面读数。
    # 这两个派生量保留在响应里给区间表等下游用，页面不展示。
    totals = [v for v in (_num(r.get("lamp_on_min")) for r in kept) if v is not None]
    keys = ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")
    last = next((r for r in reversed(kept)
                 if any(_num(r.get(k)) is not None for k in keys)), None)
    lamp = {k: (_num(last.get(k)) if last else None) for k in keys}
    # 光源构成：蓝光/白光各自是哪几组（组号清单，最后一次配置）
    lamp["blue_groups"] = _last_text(kept, "lamp_blue_groups")
    lamp["white_groups"] = _last_text(kept, "lamp_white_groups")
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


def five_numbers(values) -> dict | None:
    """一组值的五数（min / q1 / median / q3 / max / n）。

    **口径**：调用方传进来的必须是**小时均值**（一天最多 24 个点），所以这是
    "每小时一个点"的分位数，不是"每分钟一个点"的。采样点级的极值在
    `daily_distribution` 里单独取（来自 `*_min`/`*_max` 列），两者**不得混读**
    （`env-analysis/spec.md` §2.3）。空输入返回 `None`——"没有值"不是"值等于 0"。
    """
    vals = sorted(v for v in (_num(x) for x in values or ()) if v is not None)
    if not vals:
        return None
    return {
        "n": len(vals),
        "min": round(vals[0], 2),
        "q1": round(_quantile(vals, 0.25), 2),
        "median": round(_quantile(vals, 0.5), 2),
        "q3": round(_quantile(vals, 0.75), 2),
        "max": round(vals[-1], 2),
    }


def daily_distribution(
    rows: list[dict],
    *,
    zone: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """小时行 → **按天的日分布行**（日分布视图的取数，ADR-0027）。

    一天给三件事，粒度各标各的：

    * 三参数的**五数**（小时口径，一天最多 24 个点）——箱体与须用的是它；
    * 三参数的**采样点级极值**（`*_min` / `*_max` 两列）——图上的淡色范围带；
    * 采样点数 `samples`（Σn）与小时点数 `hours`：`hours` 少于 24 就是那天有缺口，
      **不补、不插值**，让缺口在数字上看得见。

    光照**不在此列**：它只带该日末态制度（`lamp_on_m` / `lamp_off_m` 设定）、
    **光源构成**（`lamp_blue_groups` / `lamp_white_groups`：蓝光/白光各是哪几组）
    与点亮分钟合计；箱线对它没有意义（ADR-0021 修订 / ADR-0027）。

    批次身份（`entry_date` / `batch_no` / `growth_day`）**不在这里算**——那是台账的事，
    由 `analysis.env_daily.refresh` 回填（本模块保持"只做纯计算、不碰存储"）。
    """
    kept = filter_rows(rows, zone=zone, start=start, end=end)
    days: dict[tuple[str, str], list[dict]] = {}
    for r in kept:
        days.setdefault((r["ts"][:10], r["zone"] or ""), []).append(r)

    out: list[dict] = []
    for (day, z) in sorted(days):
        group = days[(day, z)]
        item: dict = {
            "day": day,
            "zone": z,
            "hours": len(group),
            "samples": sum(int(g["n"] or 0) for g in group),
        }
        for col in DAILY_DISTRIBUTION_PARAMS:
            lo_key, hi_key = AGG_COLUMNS[col]
            five = five_numbers(g.get(col) for g in group) or {}
            for suffix in DAILY_FIVE_SUFFIXES:
                item[f"{col}_{suffix}"] = five.get(suffix)
            item[f"{col}_n"] = five.get("n")
            los = [v for v in (_num(g.get(lo_key)) for g in group) if v is not None]
            his = [v for v in (_num(g.get(hi_key)) for g in group) if v is not None]
            item[f"{col}_sample_min"] = round(min(los), 2) if los else None
            item[f"{col}_sample_max"] = round(max(his), 2) if his else None
        # 点亮分钟是**时长**，跨小时相加（与 `series(granularity='day')` 同一口径）
        minutes = [v for v in (_num(g.get("lamp_on_min")) for g in group) if v is not None]
        item["lamp_on_min_total"] = round(sum(minutes), 2) if minutes else None
        keys = ("lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")
        last = next((g for g in reversed(group)
                     if any(_num(g.get(k)) is not None for k in keys)), None)
        for key in keys:
            item[key] = _num(last.get(key)) if last else None
        for key in LAMP_TEXT_FIELDS:
            item[key] = _last_text(group, key)
        out.append(item)
    return out
