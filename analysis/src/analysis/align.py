"""生长 × 环境对齐分析（票 08）：分阶段生长速率 × 环境分箱 → 最佳参数区间表。

输入为 measurements / environment 两表的小时级数据（spec §6）；输出区间行::

    {"stage": "20-40mm", "param": "temp_c", "lo": 23.5, "hi": 25.1,
     "rate_mm_per_h": 0.42, "n": 36}
"""

from __future__ import annotations

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
    """从 SQLite 读两表跑全流程（保留：只读式计算，不落表）。

    **先过滤**（票 13）：可疑轮/复制帧内的测量行不进统计 —— 复制帧会让长势曲线
    看起来正常，那种错误比缺数据更难发现。
    """
    from analysis.db import connect, fetch_measurements, fetch_rounds
    from analysis.integrity import partition

    conn = connect(db_path)
    measurements = pd.DataFrame(fetch_measurements(conn))
    environment = pd.read_sql(
        "SELECT ts, zone, temp_c, rh_pct, co2_ppm, lamp_on_min FROM environment", conn
    )
    if measurements.empty or environment.empty:
        return []

    kept, dropped = partition(measurements.to_dict("records"), fetch_rounds(conn))
    if dropped:
        print(f"[ranges] 排除 {len(dropped)} 条落在可疑轮窗口内的测量行（票 13）")
    if not kept:
        return []
    return best_ranges(align(hourly_rates(pd.DataFrame(kept)), environment))


def refresh_ranges(db_path: str, *, computed_at: str | None = None) -> int:
    """每日批处理入口：计算最佳参数区间并整体替换 ranges 表（评审 #6）。

    API 的 GET /ranges 只读本表——重算与读取分离，请求代价为 O(表大小)。
    """
    from datetime import datetime

    from analysis.db import connect, replace_ranges

    rows = run_from_db(db_path)
    conn = connect(db_path)
    replace_ranges(conn, rows, computed_at or datetime.now().isoformat(timespec="seconds"))
    return len(rows)
