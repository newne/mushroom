"""生长 × 环境对齐分析（票 08）：分阶段生长速率 × 环境分箱 → 最佳参数区间表。

输入为 measurements / environment 两表的小时级数据（spec §6）；输出区间行::

    {"stage": "20-40mm", "param": "temp_c", "lo": 23.5, "hi": 25.1,
     "rate_mm_per_h": 0.42, "n": 36}
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analysis.growth import GROWTH_METRICS

MIN_SAMPLES_PER_BIN = 3  # 样本不足的区间不输出（避免误导）


def _pick_metric(m: pd.DataFrame, metric: str | None = None) -> tuple[str, str]:
    """选一个**有值**的长势量（列名, 单位）。

    默认按 `GROWTH_METRICS` 的优先级找第一个有值的：平视单档下 `mean_len_mm` 恒 NULL，
    实际会落到 `mean_projected_d_mm`（mm）或 `mean_area_mm2`（mm²）。
    一个都没有 ⇒ 报错（让上层说"样本不足"，不要硬算）。
    """
    for name, unit in GROWTH_METRICS:
        if metric and name != metric:
            continue
        if name in m.columns and m[name].notna().any():
            return name, unit
    raise ValueError(
        f"没有任何可用的长势量（{metric or '按优先级找'}）："
        f"平视下杆长恒 NULL，需要菌簇量（{', '.join(n for n, _ in GROWTH_METRICS)}）"
    )


def hourly_rates(
    measurements: pd.DataFrame,
    *,
    verified_only: bool = True,
    metric: str | None = None,
) -> pd.DataFrame:
    """按 (库房, 框) 重采样为小时级生长曲线，差分得速率。

    输入列: ts, room_id, box_id[, quality] + 长势量（至少一个）
    输出列: room_id, box_id, hour, level_mm(该量的均值), `rate_per_h`、`rate_mm_per_h`、
    `metric`、`unit`

    - **量纲（票 05 / Chief 硬要求）**：帧里带 `metric`/`unit`；`rate_mm_per_h`
      **只在 `unit == 'mm'` 时非空**，面积口径下为 NaN —— 绝不让 `_mm_` 字段承载 mm²。
    - **只吃 `verified`**（票 11 §3）；`metric` 缺省按 `GROWTH_METRICS` 优先级取第一个有值的量。
    - **只在相邻整点间差分**：任一侧缺测（含被过滤掉的）⇒ 该段无速率，**不凑数**。
    """
    m = measurements.copy()
    if "room_id" not in m.columns:
        raise ValueError(
            "measurements 缺 room_id（票 11）：缺库房维度会把不同库房串成一条曲线"
        )
    if verified_only and "quality" in m.columns:
        m = m[m["quality"].fillna("").str.contains("verified", regex=False)]
    m["ts"] = pd.to_datetime(m["ts"])
    m["hour"] = m["ts"].dt.floor("h")
    if m.empty:
        # 空表也要带上正确的 dtype：否则 `hour` 是 object，和环境的 datetime64 合并时
        # pandas 直接抛 "trying to merge on object and datetime64 columns"。
        return pd.DataFrame({
            "room_id": pd.Series(dtype="object"),
            "box_id": pd.Series(dtype="object"),
            "hour": pd.Series(dtype="datetime64[ns]"),
            "level_mm": pd.Series(dtype="float64"),
            "rate_per_h": pd.Series(dtype="float64"),
            "rate_mm_per_h": pd.Series(dtype="float64"),
            "metric": pd.Series(dtype="object"),
            "unit": pd.Series(dtype="object"),
        })

    metric_name, unit = _pick_metric(m, metric)
    hourly = (m.groupby(["room_id", "box_id", "hour"])[metric_name]
              .mean().reset_index(name="level_mm"))
    hourly = hourly.sort_values(["room_id", "box_id", "hour"])
    grouped = hourly.groupby(["room_id", "box_id"])
    prev_hour = grouped["hour"].shift(1)
    prev_level = grouped["level_mm"].shift(1)
    one_hour = pd.Timedelta(hours=1)
    rate = (hourly["level_mm"] - prev_level).where((hourly["hour"] - prev_hour) == one_hour)
    hourly["rate_per_h"] = rate
    hourly["rate_mm_per_h"] = rate if unit == "mm" else float("nan")
    hourly["metric"] = metric_name
    hourly["unit"] = unit
    return hourly.dropna(subset=["rate_per_h"])[
        ["room_id", "box_id", "hour", "level_mm", "rate_per_h", "rate_mm_per_h",
         "metric", "unit"]
    ]


def align(rates: pd.DataFrame, environment: pd.DataFrame) -> pd.DataFrame:
    """生长速率与环境小时均值按 **(库房, 小时)** 对齐。

    environment 列: ts, zone, temp_c, rh_pct, co2_ppm[, lamp_on_min]

    两条硬约束（票 11/票 05）：环境必须按 `zone` 分组，否则四个库房混进同一个分箱；
    `zone` 与测量的 `room_id` 是同一个东西，只做一次改名，不造第三个名字。
    """
    if "room_id" not in rates.columns:
        raise ValueError("rates 缺 room_id（票 11）：对不齐库房的速率不能进分箱")
    env = environment.copy()
    if "zone" not in env.columns:
        raise ValueError(
            "environment 缺 zone（票 05）：不按库房聚合会把四个库房混进同一个分箱"
        )
    env["ts"] = pd.to_datetime(env["ts"])
    env["hour"] = env["ts"].dt.floor("h")
    cols = [c for c in ("temp_c", "rh_pct", "co2_ppm", "lamp_on_min") if c in env.columns]
    if rates.empty or env.empty:
        # 空输入直接给空结果，**不要进 merge**：空 DataFrame 的 dtype 随 pandas 版本
        # 摇摆（object vs datetime64），混跑换解释器时会抛
        # "You are trying to merge on object and datetime64 columns" —— 测试门禁
        # 必须与版本/顺序无关。
        return pd.DataFrame(columns=[
            "room_id", "box_id", "hour", "level_mm", "rate_mm_per_h", *cols, "stage_mm", "stage",
        ])
    env_hourly = (
        env.groupby(["zone", "hour"])[cols].mean().reset_index()
        .rename(columns={"zone": "room_id"})
    )
    merged = rates.merge(env_hourly, on=["room_id", "hour"], how="inner")
    # 分箱口径：暂留 20mm 长度分档；spec §2.7 的 D 分档要生长天数，
    # 等票 04 的批次窗口 join 落地后再换（票 05）。
    merged["stage_mm"] = (merged["level_mm"] // 20 * 20).astype(int)
    merged["stage"] = merged["stage_mm"].astype(str) + "-" + (merged["stage_mm"] + 20).astype(str) + "mm"
    return merged


#: 可分箱的连续环境量。补光的设备状态（0=自动…3=关闭）是分类量，不进分箱；
#: 进分箱的是由循环参数（OnSSet/OffSSet）折算出来的**点亮分钟**（ADR-0021）。
PARAM_COLS = ("temp_c", "rh_pct", "co2_ppm", "lamp_on_min")


def best_ranges(merged: pd.DataFrame, *, n_bins: int = 4) -> list[dict]:
    """对齐数据 → 最佳参数区间表（分位数分箱，取平均速率最高且样本充足的箱）。

    输出的每一行都带 **`metric` / `unit` / `rate_per_h`**（量纲随行走，票 05）；
    `rate_mm_per_h` 不再由这里给 —— 由 `db.replace_ranges` 按 `unit=='mm/h'` 决定写不写。
    """
    out: list[dict] = []
    if merged.empty:
        return out
    metric = str(merged["metric"].iloc[0]) if "metric" in merged.columns else None
    unit = str(merged["unit"].iloc[0]) if "unit" in merged.columns else None
    for stage, group in merged.groupby("stage"):
        for param in PARAM_COLS:
            if param not in group or group[param].isna().all():
                continue
            try:
                bins = pd.qcut(group[param], q=n_bins, duplicates="drop")
            except ValueError:
                continue
            stats = group.groupby(bins, observed=True)["rate_per_h"].agg(["mean", "count"])
            stats = stats[stats["count"] >= MIN_SAMPLES_PER_BIN]
            if stats.empty:
                continue
            best = stats["mean"].idxmax()
            lo, hi = best.left, best.right
            out.append({
                "stage": stage,
                "param": param,
                "lo": round(float(lo), 2),
                "hi": round(float(hi), 2),
                "metric": metric,
                "unit": unit,
                "rate_per_h": round(float(stats.loc[best, "mean"]), 4),
                "n": int(stats.loc[best, "count"]),
            })
    return out


def run_from_db(db_path: str) -> list[dict]:
    """从 SQLite 读两表跑全流程（只读式计算，不落表）。返回区间行，**不返回为什么空**。

    要"为什么空"请用 `compute_ranges`（票 05 的空态标注）——它给同一批行 + 状态。
    """
    return compute_ranges(db_path).rows


#: 区间表重算的状态机（票 05）。**空表必须说得出为什么空**：
#: "从没跑过" / "没有测量" / "全被排除" / "没有环境数据" / "平视下没有可用长势量" /
#: "样本不足" 是六件不同的事，前五件靠再跑一次是好不了的，页面不能都显示成"暂无数据"。
STATE_OK = "ok"
STATE_NO_MEASUREMENTS = "no_measurements"
STATE_ALL_EXCLUDED = "all_excluded"
STATE_NO_ENVIRONMENT = "no_environment"
STATE_NO_GROWTH_METRIC = "no_growth_metric"
STATE_INSUFFICIENT_SAMPLES = "insufficient_samples"

#: 每个状态一句人话（页面直接显示，不用自己拼，也别让四个页面各写一份）。
STATE_TEXT = {
    STATE_OK: "区间表已算好（{n} 行）",
    STATE_NO_MEASUREMENTS: "样本不足：库房里还没有任何测量行（测量管线未接上）",
    STATE_ALL_EXCLUDED: (
        "样本不足：测量行全部落在被排除的可疑轮/复制帧窗口内（见 detail.excluded_by_reason）"
    ),
    STATE_NO_ENVIRONMENT: "样本不足：还没有环境小时聚合，长势速率无法与环境对齐",
    STATE_NO_GROWTH_METRIC: (
        "样本不足：没有可用的长势量（平视单档下杆长恒缺测，需要菌簇量）"
    ),
    STATE_INSUFFICIENT_SAMPLES: (
        "样本不足：每个参数分箱的有效样本 < {min}（相邻整点的速率段不够），暂不输出区间"
    ),
}


@dataclass
class RangesRun:
    """一次重算的**全部结果**：区间行 + 状态 + 明细计数。

    `detail` 里的计数是"排除必须可见"的落点（票 17）：被 10-09 那条显式排除清单排掉的行
    要能在这里数出来，不许静默消失。
    """

    rows: list[dict]
    state: str
    state_text: str
    detail: dict


def compute_ranges(db_path: str, *, metric: str | None = None) -> RangesRun:
    """从 SQLite 读两表跑全流程，返回 **区间行 + 为什么空**（票 05）。

    - **先过滤**（票 13/17）：可疑轮、复制帧、以及显式排除清单命中的测量行不进统计 ——
      复制帧会让长势曲线看起来正常，那种错误比缺数据更难发现。
    - 每一步失败都映射成一个**具名状态**，绝不返回一个无解释的空列表。
    """
    from analysis.db import connect, fetch_measurements, fetch_rounds
    from analysis.integrity import partition

    conn = connect(db_path)
    measurements = pd.DataFrame(fetch_measurements(conn))
    environment = pd.read_sql(
        "SELECT ts, zone, temp_c, rh_pct, co2_ppm, lamp_on_min FROM environment", conn
    )
    detail: dict = {
        "n_measurements": int(len(measurements)),
        "n_environment": int(len(environment)),
        "n_kept": 0,
        "n_excluded": 0,
        "excluded_by_reason": {},
        "min_samples_per_bin": MIN_SAMPLES_PER_BIN,
    }

    def done(state: str, extra: dict | None = None) -> RangesRun:
        detail.update(extra or {})
        text = STATE_TEXT[state].format(n=0, min=MIN_SAMPLES_PER_BIN)
        return RangesRun(rows=[], state=state, state_text=text, detail=dict(detail))

    if measurements.empty:
        return done(STATE_NO_MEASUREMENTS)

    kept, dropped = partition(measurements.to_dict("records"), fetch_rounds(conn))
    detail["n_kept"] = len(kept)
    detail["n_excluded"] = len(dropped)
    by_reason: dict[str, int] = {}
    for item in dropped:
        by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
    detail["excluded_by_reason"] = by_reason
    # 前 20 条原文（带人话原因）——"排除了多少"之外还要能回答"排掉了哪一段、凭什么"
    detail["excluded_samples"] = [
        {"ts": d.ts, "zone": d.zone, "reason": d.reason, "window": d.window, "detail": d.detail}
        for d in dropped[:20]
    ]
    detail["excluded_samples_truncated"] = len(dropped) > 20
    if dropped:
        print(f"[ranges] 排除 {len(dropped)} 条落在可疑轮窗口内的测量行（票 13/17）")

    if not kept:
        return done(STATE_ALL_EXCLUDED)
    if environment.empty:
        return done(STATE_NO_ENVIRONMENT)

    kept_df = pd.DataFrame(kept)
    try:
        metric_name, unit = _pick_metric(kept_df, metric)
    except ValueError as e:
        # 平视单档下这条现在是**常态**（`mean_len_mm` 恒 NULL，菌簇四列还没有生产者）：
        # 必须说清是"没有长势量"，而不是笼统的"样本不足"。
        detail["error"] = str(e)
        return done(STATE_NO_GROWTH_METRIC)
    detail["metric"] = metric_name
    detail["unit"] = unit

    rates = hourly_rates(kept_df, metric=metric_name)
    detail["n_rate_rows"] = int(len(rates))
    merged = align(rates, environment)
    detail["n_aligned_hours"] = int(len(merged))
    rows = best_ranges(merged)

    if not rows:
        return done(STATE_INSUFFICIENT_SAMPLES)

    detail["n_rows"] = len(rows)
    return RangesRun(
        rows=rows,
        state=STATE_OK,
        state_text=STATE_TEXT[STATE_OK].format(n=len(rows)),
        detail=dict(detail),
    )


def refresh_ranges(
    db_path: str, *, computed_at: str | None = None, metric: str | None = None
) -> int:
    """每日批处理入口（票 05）：计算最佳参数区间并整体替换 ranges 表（评审 #6）。

    API 的 GET /ranges 只读本表——重算与读取分离，请求代价为 O(表大小)。

    **同一事务里写区间行与状态**：行与状态不许对不上（"有 12 行"却写着 `样本不足`）。
    """
    from datetime import datetime

    from analysis.db import connect, replace_ranges, write_ranges_state

    stamp = computed_at or datetime.now().isoformat(timespec="seconds")
    run = compute_ranges(db_path, metric=metric)
    conn = connect(db_path)
    replace_ranges(conn, run.rows, stamp)
    write_ranges_state(
        conn,
        state=run.state,
        state_text=run.state_text,
        computed_at=stamp,
        detail=run.detail,
        n_rows=len(run.rows),
        metric=run.detail.get("metric"),
        unit=run.detail.get("unit"),
    )
    print(f"[ranges] state={run.state} rows={len(run.rows)} {run.state_text}")
    return len(run.rows)
