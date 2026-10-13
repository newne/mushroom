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
        env_rows.append({"ts": ts.isoformat(), "zone": "611", "temp_c": float(temp),
                         "rh_pct": 85.0, "co2_ppm": 800.0, "lamp_state": 2.0,
                         "lamp_on_min": 30.0})

    # 每小时一个框的一条测量：level 累积，增速取决于上一小时的温度
    measure_rows = []
    for box_id in ("B01", "B02"):
        level = 10.0
        for i, ts in enumerate(hours):
            measure_rows.append({"ts": ts.isoformat(), "room_id": "611", "box_id": box_id,
                                 "mean_len_mm": level, "quality": "verified"})
            temp = env_rows[i]["temp_c"]
            rate = 0.6 if 24 <= temp <= 26 else 0.2
            level += rate
    return pd.DataFrame(measure_rows), pd.DataFrame(env_rows)


def test_hourly_rates_shape():
    m, _ = make_data()
    rates = hourly_rates(m)
    # 2 框 × 48 小时，去掉首个差分行
    assert len(rates) == 2 * 47
    assert set(rates.columns) >= {"room_id", "box_id", "hour", "level_mm", "rate_mm_per_h"}


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
    empty_m = pd.DataFrame(columns=["ts", "room_id", "box_id", "mean_len_mm", "quality"])
    empty_e = pd.DataFrame(columns=["ts", "zone", "temp_c", "rh_pct", "co2_ppm",
                                    "lamp_state", "lamp_on_min"])
    assert best_ranges(align(hourly_rates(empty_m), empty_e)) == []


# ---------- 库房维度：这是票 11 那个坑的回归测试 ----------


def test_same_box_id_in_two_rooms_is_not_mixed():
    """两个库房有同一个框号时，差分别把两条曲线串起来（缺 room_id 就会）。"""
    m, _ = make_data()
    other = m.copy()
    other["room_id"] = "612"
    other["mean_len_mm"] = other["mean_len_mm"] + 100.0   # 另一个库房长得完全不同的量级

    rates = hourly_rates(pd.concat([m, other], ignore_index=True))
    assert set(rates["room_id"]) == {"611", "612"}
    # 每 (库房, 框, 小时) 一条，而不是两条被平均成一条
    assert len(rates) == 4 * 47
    # 611 的速率不会被 612 的 100mm 台阶污染（台阶只出现在各自的第一条差分里）
    first = rates[(rates["room_id"] == "611") & (rates["box_id"] == "B01")]
    assert first["rate_mm_per_h"].abs().max() < 1.0


def test_unverified_rows_do_not_enter_the_curve():
    """票 11 §3：区间表与决策层只吃 `verified`。"""
    m, _ = make_data()
    m.loc[m.index[:5], "quality"] = "no_detection"
    kept = hourly_rates(m)
    all_rows = hourly_rates(m, verified_only=False)
    assert len(kept) < len(all_rows)


def test_missing_room_id_fails_loudly():
    """缺库房维度必须报错，不许**静默**把库房混起来算。"""
    m, _ = make_data()
    with pytest.raises(ValueError, match="room_id"):
        hourly_rates(m.drop(columns=["room_id"]))


def test_missing_zone_fails_loudly():
    m, e = make_data()
    with pytest.raises(ValueError, match="zone"):
        align(hourly_rates(m), e.drop(columns=["zone"]))
