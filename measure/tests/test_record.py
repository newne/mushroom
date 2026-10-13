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
                "mean_cap_mm": None, "p10_len_mm": None, "p90_len_mm": None,
                "quality_flags": "baseline+low_confidence"}
    return BoxStats(**{**defaults, **kw})


def test_record_fields_match_ticket_11():
    """票 11 §1 的字段表，逐字逐序。"""
    assert RECORD_FIELDS == ("ts", "room_id", "box_id", "station_id", "angle_profile",
                             "n", "mean_len_mm", "mean_cap_mm", "p10_len", "p90_len",
                             "quality")


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
