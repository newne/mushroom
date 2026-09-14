"""生长 × 环境对齐分析（票 08）：分阶段生长速率 × 环境分箱 → 最佳参数区间表。

输入为 measurements / environment 两表的小时级数据（spec §6）；输出区间行::

    {"stage": "20-40mm", "param": "temp_c", "lo": 23.5, "hi": 25.1,
     "rate_mm_per_h": 0.42, "n": 36}
"""

from __future__ import annotations

import pandas as pd

MIN_SAMPLES_PER_BIN = 3  # 样本不足的区间不输出（避免误导）


def hourly_rates(measurements: pd.DataFrame) -> pd.DataFrame:
    """按框重采样为小时级生长曲线，差分得生长速率（mm/h）。

    输入列: ts, box_id, mean_len_mm；输出列: ts(小时), box_id, level_mm, rate_mm_per_h
    """
    m = measurements.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    m["hour"] = m["ts"].dt.floor("h")
    hourly = (m.groupby(["box_id", "hour"])["mean_len_mm"]
              .mean().reset_index(name="level_mm"))
    hourly = hourly.sort_values(["box_id", "hour"])
    hourly["rate_mm_per_h"] = hourly.groupby("box_id")["level_mm"].diff()
    return hourly.dropna(subset=["rate_mm_per_h"])


def align(rates: pd.DataFrame, environment: pd.DataFrame) -> pd.DataFrame:
    """生长速率与环境小时均值按时间对齐。

    environment 列: ts, temp_c, rh_pct, co2_ppm, light_lux
    """
    env = environment.copy()
    env["ts"] = pd.to_datetime(env["ts"])
    env["hour"] = env["ts"].dt.floor("h")
    env_hourly = (env.groupby("hour")[["temp_c", "rh_pct", "co2_ppm", "light_lux"]]
                  .mean().reset_index())
    merged = rates.merge(env_hourly, on="hour", how="inner")
    merged["stage_mm"] = (merged["level_mm"] // 20 * 20).astype(int)
    merged["stage"] = merged["stage_mm"].astype(str) + "-" + (merged["stage_mm"] + 20).astype(str) + "mm"
    return merged


PARAM_COLS = ("temp_c", "rh_pct", "co2_ppm", "light_lux")


def best_ranges(merged: pd.DataFrame, *, n_bins: int = 4) -> list[dict]:
    """对齐数据 → 最佳参数区间表（分位数分箱，取平均速率最高且样本充足的箱）。"""
    out: list[dict] = []
    if merged.empty:
        return out
    for stage, group in merged.groupby("stage"):
        for param in PARAM_COLS:
            if param not in group or group[param].isna().all():
                continue
            try:
                bins = pd.qcut(group[param], q=n_bins, duplicates="drop")
            except ValueError:
                continue
            stats = group.groupby(bins, observed=True)["rate_mm_per_h"].agg(["mean", "count"])
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
                "rate_mm_per_h": round(float(stats.loc[best, "mean"]), 4),
                "n": int(stats.loc[best, "count"]),
            })
    return out


def run_from_db(db_path: str) -> list[dict]:
    """从 SQLite 读两表跑全流程（保留：只读式计算，不落表）。"""
    from analysis.db import connect

    conn = connect(db_path)
    measurements = pd.read_sql("SELECT ts, box_id, mean_len_mm FROM measurements", conn)
    environment = pd.read_sql("SELECT ts, temp_c, rh_pct, co2_ppm, light_lux FROM environment", conn)
    if measurements.empty or environment.empty:
        return []
    return best_ranges(align(hourly_rates(measurements), environment))


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
