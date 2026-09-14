"""对齐分析测试：合成数据里温度 24–26℃ 区间生长最快，验证区间表能找回它。"""

import pandas as pd
import pytest
from analysis.align import align, best_ranges, hourly_rates

H = pd.Timedelta("1h")


def make_data():
    """48 小时合成数据：温度处于 24–26℃ 的小时，生长速率 ×3。"""
    start = pd.Timestamp("2026-08-01 00:00:00")
    hours = [start + i * H for i in range(48)]

    env_rows = []
    for i, ts in enumerate(hours):
        temp = 20 + (i % 4) * 3  # 20,23,26,29 循环 → 温区各 12 小时
        env_rows.append({"ts": ts.isoformat(), "temp_c": float(temp),
                         "rh_pct": 85.0, "co2_ppm": 800.0, "light_lux": 100.0})

    # 每小时一个框的一条测量：level 累积，增速取决于上一小时的温度
    measure_rows = []
    for box_id in ("B01", "B02"):
        level = 10.0
        for i, ts in enumerate(hours):
            measure_rows.append({"ts": ts.isoformat(), "box_id": box_id,
                                 "mean_len_mm": level})
            temp = env_rows[i]["temp_c"]
            rate = 0.6 if 24 <= temp <= 26 else 0.2
            level += rate
    return pd.DataFrame(measure_rows), pd.DataFrame(env_rows)


def test_hourly_rates_shape():
    m, _ = make_data()
    rates = hourly_rates(m)
    # 2 框 × 48 小时，去掉首个差分行
    assert len(rates) == 2 * 47
    assert set(rates.columns) >= {"box_id", "hour", "level_mm", "rate_mm_per_h"}


def test_best_ranges_finds_hot_bin():
    m, e = make_data()
    rates = hourly_rates(m)
    merged = align(rates, e)
    ranges = best_ranges(merged)
    temp_rows = [r for r in ranges if r["param"] == "temp_c"]
    assert temp_rows, "应产出温度区间行"
    best = max(temp_rows, key=lambda r: r["rate_mm_per_h"])
    # 最优温度区间应落在 24–26℃ 一带（分箱边界允许 ±1.5 浮动）
    assert 22.5 <= best["lo"] <= 26.0
    assert best["rate_mm_per_h"] == pytest.approx(0.6, abs=0.1)
    assert best["n"] >= 3


def test_stage_bucketing():
    m, e = make_data()
    merged = align(hourly_rates(m), e)
    # level 从 10mm 起 → 属于 0-20mm 与 20-40mm 两阶段
    stages = set(merged["stage"].unique())
    assert stages <= {"0-20mm", "20-40mm", "40-60mm"}
    assert stages


def test_empty_inputs_yield_no_ranges():
    empty_m = pd.DataFrame(columns=["ts", "box_id", "mean_len_mm"])
    empty_e = pd.DataFrame(columns=["ts", "temp_c", "rh_pct", "co2_ppm", "light_lux"])
    assert best_ranges(align(hourly_rates(empty_m), empty_e)) == []
