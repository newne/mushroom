"""`measure.record` 的单元测试：线格式字段表是三方共用的唯一钉子（架构评审 #3）。

票 11 §1 铁律：`RECORD_FIELDS`（measure 侧）与 `MEASUREMENT_FIELDS`（analysis 侧）
必须一字不差；加字段先改票。验收断言（票 11 §7）在最后一个用例。
"""

import json

import pytest

from measure.pipeline import BoxStats
from measure.record import RECORD_FIELDS, MeasurementRecord


def make_stats(**kw):
    defaults = {"box_id": "B105", "n_total": 3, "n_used": 2, "mean_len_mm": None,
                "mean_cap_mm": None, "mean_cluster_w_mm": 564.5,
                "mean_cluster_h_mm": 376.5, "mean_area_mm2": 133124.0,
                "mean_projected_d_mm": 411.7,
                "p10_len_mm": None, "p90_len_mm": None,
                "quality_flags": "baseline+low_confidence"}
    return BoxStats(**{**defaults, **kw})


def test_record_fields_match_ticket_11():
    """票 11 §1 的字段表 + 票 20 证据列，逐字逐序。"""
    assert RECORD_FIELDS == ("ts", "room_id", "box_id", "station_id", "angle_profile",
                             "n", "mean_len_mm", "mean_cap_mm",
                             "mean_cluster_w_mm", "mean_cluster_h_mm",
                             "mean_area_mm2", "mean_projected_d_mm",
                             "p10_len", "p90_len", "quality",
                             # 票 20 证据列（2026-10-14 环境官落列）
                             "estimated_fields", "scale_source", "mm_per_px",
                             "scale_ref_mm", "scale_ref_px")


def test_record_fields_are_the_analysis_contract():
    """与 analysis 侧清单**名字集合一字不差**（票 28 contract 后常量在 `analysis.schema`）。

    为什么不是"元组逐位相等"：analysis 侧 `MEASUREMENT_FIELDS` 是 `INSERT` 的列清单
    （用命名占位符，顺序无所谓），按集合比才是这两份清单真正的契约；
    比较顺序会在任一侧无害重排时误报。
    """
    from analysis.schema import MEASUREMENT_FIELDS

    assert set(RECORD_FIELDS) == set(MEASUREMENT_FIELDS)
    assert len(RECORD_FIELDS) == len(MEASUREMENT_FIELDS)


def test_room_id_is_mandatory():
    with pytest.raises(ValueError):
        MeasurementRecord(ts="t1", room_id="", box_id="B105")
    with pytest.raises(TypeError):
        MeasurementRecord(ts="t1", box_id="B105")      # type: ignore[call-arg]


def test_from_box_stats_translates_names_once():
    rec = MeasurementRecord.from_box_stats(make_stats(), ts="2026-08-30T08:00:00",
                                           room_id="611", station_id="S105",
                                           angle_profile="level0")
    assert rec.n == 3                      # n_total -> n
    assert rec.quality == "baseline+low_confidence"
    assert rec.ts == "2026-08-30T08:00:00"
    assert rec.room_id == "611"
    assert rec.station_id == "S105"
    assert rec.angle_profile == "level0"
    assert rec.mean_len_mm is None


def test_to_row_keys_exactly_contract():
    row = MeasurementRecord.from_box_stats(make_stats(), ts="t1", room_id="611").to_row()
    assert tuple(row.keys()) == RECORD_FIELDS
    assert row["room_id"] == "611"
    # 菌簇四列必须真发（平视单档下唯一的长势量）
    assert row["mean_cluster_h_mm"] == 376.5
    assert row["mean_cluster_w_mm"] == 564.5
    assert row["mean_area_mm2"] == 133124.0
    assert row["mean_projected_d_mm"] == 411.7


def test_roundtrip_via_json_safe_types():
    row = MeasurementRecord.from_box_stats(make_stats(), ts="t1", room_id="611").to_row()
    assert json.loads(json.dumps(row))["n"] == 3


def test_ticket_11_acceptance_row_has_room_id_and_null_lengths():
    """票 11 §7 验收：`to_row()` 含 `room_id`；缺测时长度列 NULL、quality=no_detection。"""
    rec = MeasurementRecord(ts="2026-10-13T15:00:00", room_id="611", box_id="B105",
                            station_id="S105", angle_profile="level0",
                            n=0, quality="no_detection",
                            notes="菌盖直径、菌杆长度：无对应角度档")
    row = rec.to_row()
    assert "room_id" in row and row["room_id"] == "611"
    assert row["mean_len_mm"] is None
    assert row["mean_cap_mm"] is None
    assert row["quality"] == "no_detection"
    assert row["n"] == 0
    assert "notes" not in row              # 不在契约字段表里，别偷渡


# ---------- 票 20 证据列（估算行必须能回答"哪些列是估的、mm/px 从哪来"） ----------

def test_evidence_fields_roundtrip_and_join_shape():
    """`estimated_fields` 收 list ⇒ 出线统一成 `+` 串（analysis 侧形状），空 ⇒ NULL。"""
    rec = MeasurementRecord.from_box_stats(
        make_stats(), ts="t1", room_id="611",
        estimated_fields=["mean_cluster_w_mm", "mean_cluster_h_mm"],
        scale_source="bag_standard", mm_per_px=0.825,
        scale_ref_mm=240.0, scale_ref_px=291)
    row = rec.to_row()
    assert row["estimated_fields"] == "mean_cluster_w_mm+mean_cluster_h_mm"
    assert row["scale_source"] == "bag_standard"
    assert row["mm_per_px"] == 0.825 and row["scale_ref_mm"] == 240.0
    # 交叉复算：mm/px ≈ ref_mm / ref_px（生产端数值列按 3 位小数出线，故容差 1e-3）
    assert row["mm_per_px"] == pytest.approx(row["scale_ref_mm"] / row["scale_ref_px"], abs=1e-3)
    # 传串也接受、去重保序；空 ⇒ NULL（不是空串、不是 0）
    assert MeasurementRecord(ts="t1", room_id="611", box_id="B",
                             estimated_fields="a+b+a").to_row()["estimated_fields"] == "a+b"
    assert MeasurementRecord(ts="t1", room_id="611", box_id="B").to_row()["estimated_fields"] is None
    assert MeasurementRecord(ts="t1", room_id="611", box_id="B",
                             estimated_fields=[]).to_row()["estimated_fields"] is None


def test_evidence_columns_are_null_when_no_scale():
    """缺标尺 ⇒ 证据列全 NULL（**不写 0**），`scale_source` 显式 none 时才标 none。"""
    row = MeasurementRecord.from_box_stats(make_stats(), ts="t1", room_id="611").to_row()
    for col in ("estimated_fields", "scale_source", "mm_per_px",
                "scale_ref_mm", "scale_ref_px"):
        assert row[col] is None, col
    none_row = MeasurementRecord(ts="t1", room_id="611", box_id="B",
                                 quality="no_detection+no_scale",
                                 scale_source="none").to_row()
    assert none_row["scale_source"] == "none" and none_row["mm_per_px"] is None


def test_estimated_fields_agrees_with_the_filter_entry_point():
    """出口形状必须能被**唯一过滤入口**读：估算列的 `field_is_usable()` 为 False。"""
    from measure.contract import field_is_usable

    row = MeasurementRecord.from_box_stats(
        make_stats(), ts="t1", room_id="611",
        estimated_fields=["mean_cluster_w_mm"]).to_row()
    assert row["estimated_fields"] == "mean_cluster_w_mm"
    assert field_is_usable("verified", row["estimated_fields"], "mean_cluster_w_mm") is False
    assert field_is_usable("verified", row["estimated_fields"], "n") is True


def test_scale_source_enum_matches_analysis_side():
    """枚举名以环境官定稿为准（`analysis/evidence.py`）——两边不许漂。"""
    from analysis.evidence import SCALE_SOURCES, normalize_scale_source

    from measure.record import MeasurementRecord as Rec

    for value in SCALE_SOURCES:
        row = Rec(ts="t1", room_id="611", box_id="B", scale_source=value).to_row()
        assert row["scale_source"] == value
        assert normalize_scale_source(row["scale_source"]) == value
    # 旧值 `bag_diameter` 由 analysis 归一（测量侧不再发它，但过线不炸）
    assert normalize_scale_source("bag_diameter") == "bag_standard"
