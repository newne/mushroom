"""`measure.curve`：缺测留点、分段、不跨长缺测算速率。"""

import pytest

from measure.curve import (
    CurvePoint,
    anchor_series,
    assert_no_delta_in_curve,
    layer_median,
    rates,
)


def row(ts, value, **kw):
    base = dict(
        ts=ts, anchor_id="S101", h_anchor=value, n_bags_valid=2 if value is not None else 0,
        n_bags_total=3, panel_coverage=2 / 3 if value is not None else 0.0,
        segmentation_status="by_version_and_batch", usable=value is not None,
        pipeline_version="p1", model_version="m1", roi_version="r1",
        calibration_version="c1", reasons=(),
    )
    base.update(kw)
    return base


def test_missing_frames_are_kept_as_points_not_dropped():
    series = anchor_series([row("2026-10-20T10:00:00", 50),
                            row("2026-10-20T13:00:00", None),
                            row("2026-10-20T16:00:00", 60)])
    assert [p.status for p in series] == ["ok", "missing", "ok"]
    assert series[1].value is None


def test_input_is_sorted_by_ts():
    series = anchor_series([row("2026-10-20T16:00:00", 60),
                            row("2026-10-20T10:00:00", 50)])
    assert [p.ts[-8:-3] for p in series] == ["10:00", "16:00"]


def test_version_change_starts_a_new_segment():
    series = anchor_series([
        row("2026-10-20T10:00:00", 50),
        row("2026-10-20T13:00:00", 55),
        row("2026-10-20T16:00:00", 60, model_version="m2"),
        row("2026-10-20T19:00:00", 62, model_version="m2"),
    ])
    assert [p.segment_id for p in series] == [0, 0, 1, 1]


def test_rate_only_when_same_segment_adjacent_with_values_and_sane_dt():
    series = anchor_series([
        row("2026-10-20T10:00:00", 50),
        row("2026-10-20T12:00:00", 60),
        row("2026-10-20T13:00:00", 62),
        row("2026-10-20T15:00:00", 66),
    ])
    got = rates(series, min_span_h=2.0, max_gap_h=12.0)
    assert [r.status for r in got] == ["ok", "too_short", "ok"]
    assert got[0].rate_per_h == pytest.approx(5.0)
    assert got[2].rate_per_h == pytest.approx(2.0)


def test_missing_point_breaks_the_rate_across_it():
    series = anchor_series([row("2026-10-20T10:00:00", 50),
                            row("2026-10-20T13:00:00", None),
                            row("2026-10-20T16:00:00", 80)])
    got = rates(series)
    assert [r.status for r in got] == ["no_value", "no_value"]


def test_long_gap_has_no_rate():
    series = anchor_series([row("2026-10-20T08:00:00", 50),
                            row("2026-10-20T22:00:00", 90)])
    assert rates(series, max_gap_h=12.0)[0].status == "gap"


def test_segment_boundary_has_no_rate():
    series = anchor_series([row("2026-10-20T10:00:00", 50),
                            row("2026-10-20T13:00:00", 60, roi_version="r2")])
    got = rates(series)
    assert got[0].status == "segment_changed" and got[0].rate_per_h is None


def test_layer_median_excludes_anchors_without_points():
    a = anchor_series([row("2026-10-20T10:00:00", 10), row("2026-10-20T13:00:00", 20)])
    b = anchor_series([row("2026-10-20T10:00:00", 30), row("2026-10-20T13:00:00", None)])
    c = anchor_series([row("2026-10-20T10:00:00", None), row("2026-10-20T13:00:00", 40)])
    got = layer_median({"A": a, "B": b, "C": c})
    assert [(p.ts[-8:-3], p.value, p.n_anchors) for p in got] == [
        ("10:00", 20.0, 2), ("13:00", 30.0, 2)]


def test_usable_is_carried_but_never_renamed_verified():
    series = anchor_series([row("2026-10-20T10:00:00", 50, usable=False)])
    assert series[0].usable is False
    assert not hasattr(series[0], "verified")
    assert "verified" not in {f.name for f in CurvePoint.__dataclass_fields__.values()}


def test_delta_is_not_a_curve_field():
    assert_no_delta_in_curve()
    assert "delta" not in CurvePoint.__dataclass_fields__
