from measure.pipeline import BoxStats
from measure.record import RECORD_FIELDS, MeasurementRecord


def make_stats(**kw):
    defaults = {"box_id": "B01", "n_total": 3, "n_used": 2, "mean_len_mm": 52.0,
                "mean_cap_mm": 31.0, "p10_len_mm": 48.0, "p90_len_mm": 56.0,
                "quality_flags": "blurry"}
    return BoxStats(**{**defaults, **kw})


def test_record_fields_match_spec_contract():
    assert RECORD_FIELDS == ("ts", "box_id", "n", "mean_len_mm", "mean_cap_mm",
                             "p10_len", "p90_len", "quality")


def test_from_box_stats_translates_names_once():
    rec = MeasurementRecord.from_box_stats(make_stats(), ts="2026-08-30T08:00:00")
    assert rec.n == 3                      # n_total -> n
    assert rec.p10_len == 48.0             # p10_len_mm -> p10_len
    assert rec.quality == "blurry"         # quality_flags -> quality
    assert rec.ts == "2026-08-30T08:00:00"


def test_to_row_keys_exactly_contract():
    row = MeasurementRecord.from_box_stats(make_stats(), ts="t1").to_row()
    assert tuple(row.keys()) == RECORD_FIELDS


def test_roundtrip_via_json_safe_types():
    import json

    row = MeasurementRecord.from_box_stats(make_stats(), ts="t1").to_row()
    assert json.loads(json.dumps(row))["n"] == 3
