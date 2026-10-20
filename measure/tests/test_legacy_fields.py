"""spec v6 §4.7：单杆 legacy 字段退出活跃口径（产出侧）。"""

import pytest

from measure import contract
from measure.pipeline import BoxStats
from measure.record import MeasurementRecord


def test_field_tiers_are_explicit():
    assert contract.field_tier("mean_len_mm") == "legacy"
    assert contract.field_tier("p10_len") == "legacy"
    assert contract.field_tier("p90_len") == "legacy"
    assert contract.field_tier("StipeEstimate") == "legacy"
    assert contract.field_tier("H_profile") == "active"
    assert contract.field_tier("mean_cluster_h_mm") == "auxiliary"
    assert contract.field_tier("n") == "unknown"


def test_estimated_fields_drop_legacy_names():
    assert contract.without_legacy_fields("mean_len_mm+p90_len+mean_cluster_w_mm") == "mean_cluster_w_mm"
    assert contract.without_legacy_fields(["p10_len", "mean_cap_mm"]) == "mean_cap_mm"
    assert contract.without_legacy_fields("mean_len_mm") is None
    assert contract.without_legacy_fields(None) is None


def test_new_rows_write_legacy_columns_as_null():
    stats = BoxStats(
        box_id="B105", n_total=3, n_used=3,
        mean_len_mm=52.0, mean_cap_mm=None,
        mean_cluster_w_mm=564.5, mean_cluster_h_mm=376.5,
        mean_area_mm2=133124.0, mean_projected_d_mm=411.7,
        p10_len_mm=40.0, p90_len_mm=61.0,
        quality_flags="estimate",
    )
    rec = MeasurementRecord.from_box_stats(
        stats, "2026-10-20T12:00:00", room_id="611",
        estimated_fields=["mean_len_mm", "mean_cluster_w_mm"],
    )
    assert rec.mean_len_mm is None and rec.p10_len is None and rec.p90_len is None
    assert rec.estimated_fields == "mean_cluster_w_mm"
    row = rec.to_row()
    assert row["mean_len_mm"] is None and row["p10_len"] is None and row["p90_len"] is None
    assert row["estimated_fields"] == "mean_cluster_w_mm"
    # 辅助列仍照发（不能把"降级"误做成"缺测"）
    assert row["mean_cluster_w_mm"] == pytest.approx(564.5)


def test_historical_rows_still_read_legacy_values():
    """历史行读兼容：直接构造的记录不被清空（只有新产出才置 NULL）。"""
    rec = MeasurementRecord(ts="2026-09-13T20:35:15", room_id="611", box_id="B101",
                            n=6, mean_len_mm=42.5, p10_len=38.0, p90_len=47.0)
    row = rec.to_row()
    assert row["mean_len_mm"] == pytest.approx(42.5)
    assert row["p10_len"] == pytest.approx(38.0)
