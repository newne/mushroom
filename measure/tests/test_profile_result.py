"""`measure.profile_result`：降级路由逐行、NULL 不写 0、JSONL 可回读。"""

import json

import numpy as np
import pytest

from measure.bag_top import BaselineProfile
from measure.cluster_profile import HeightProfile
from measure.debag import measure_region
from measure.profile_result import (
    CapInput,
    ResultKeys,
    aggregate_anchor,
    build_bag_result,
    build_frame_result,
    read_jsonl,
    write_jsonl,
)


def keys() -> ResultKeys:
    return ResultKeys("img-1", "p1", "m1", "roi1", "cal1")


def baseline(*, valid=True) -> BaselineProfile:
    b = np.full(80, 100.0)
    return BaselineProfile("s", 0, b, np.ones(80, bool), np.zeros(80, bool), valid)


def height(*, h=60.0, ok=True) -> HeightProfile:
    n = 80
    return HeightProfile(
        h_profile=h if ok else None, h_p50=h if ok else None, h_p90=h if ok else None,
        raw_top_height_px=h if ok else None, h_profile_with_interp=None,
        top_envelope=np.full(n, np.nan), column_heights=np.full(n, np.nan),
        valid_columns=np.ones(n, bool), valid_column_count=n, valid_column_ratio=1.0,
        supported_column_count=n, status="ok" if ok else "insufficient_contour",
        reasons=() if ok else ("insufficient_contour",), rejected={},
    )


def region(*, debagged=True):
    cluster = np.zeros((120, 200), bool)
    cluster[20:60, 60:100] = True
    if debagged:
        bag = np.zeros_like(cluster)
        bag[60:, 30:130] = True
        cluster |= bag
        return measure_region(cluster, [bag])
    return measure_region(cluster, [])


def test_baseline_invalid_kills_height_but_keeps_region():
    r = build_bag_result(keys(), "s1", height=height(), baseline=baseline(valid=False),
                         region=region(), ref_px=100.0, ref_kind="bag_width")
    assert r.h_profile is None and r.h_p50 is None and r.h_p90 is None
    assert r.cluster_height_usable is False
    assert r.area_px == 1600 and r.cluster_width_px == 40
    assert r.region_metrics_usable is True
    assert "baseline_invalid" in r.reasons


def test_association_failure_nulls_all_bag_metrics():
    r = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                         region=region(), association_valid=False, ref_px=100.0)
    assert r.h_profile is None and r.area_px is None
    assert r.status == "degraded"
    assert r.reasons == ("association_ambiguous",)


def test_measurement_failure_is_no_detection_not_zero():
    r = build_bag_result(keys(), "s1", measurement_valid=False)
    assert r.status == "no_detection"
    assert r.h_profile is None and r.area_px is None and r.cap_count is None
    assert 0 not in (r.h_profile, r.area_px, r.cap_count)


def test_missing_bag_mask_nulls_region_but_not_height():
    r = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                         region=region(debagged=False), ref_px=100.0)
    assert r.h_profile == pytest.approx(60.0)
    assert r.area_px is None and r.mask_source == "cluster_raw"
    assert "bag_mask_missing" in r.reasons


def test_cap_failure_does_not_block_cluster():
    r = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                         region=region(), ref_px=100.0, cap=None)
    assert r.h_profile == pytest.approx(60.0)
    assert r.cap_count is None and r.cap_metrics_usable is False
    assert "cap_model_unavailable" in r.reasons


def test_confirmed_absent_is_the_only_way_to_write_zero():
    zero = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                            region=region(), cap=CapInput(confirmed_absent=True))
    assert zero.cap_count == 0

    unknown = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                               region=region(), cap=CapInput())
    assert unknown.cap_count is None


def test_relative_metrics_need_ref_px_and_report_its_kind():
    r = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                         region=region(), ref_px=100.0, ref_kind="bag_width")
    assert r.rel_profile_height == pytest.approx(0.6)
    assert r.rel_top_height == pytest.approx(0.6)
    assert r.rel_width == pytest.approx(0.4)

    no_ref = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                              region=region())
    assert no_ref.rel_profile_height is None
    assert "no_ref_px" in no_ref.reasons


def test_anchor_median_excludes_bags_without_height_from_numerator():
    good = [build_bag_result(keys(), f"s{i}", height=height(h=v), baseline=baseline(),
                             region=region(), ref_px=100.0)
            for i, v in enumerate((40.0, 60.0))]
    bad = build_bag_result(keys(), "s9", height=height(ok=False), baseline=baseline(),
                           region=region(), ref_px=100.0)
    a = aggregate_anchor(keys(), "S101", good + [bad], n_slots=3)
    assert a.h_anchor == pytest.approx(50.0)
    assert a.n_bags_valid == 2 and a.n_bags_total == 3
    assert a.panel_coverage == pytest.approx(2 / 3)
    assert a.panel_coverage_low is True  # 2/3 < 0.8
    assert a.usable is False and "panel_coverage_low" in a.reasons


def test_anchor_absent_slot_is_counted_in_denominator():
    bag = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                           region=region(), ref_px=100.0)
    a = aggregate_anchor(keys(), "S101", [bag], n_slots=2)
    assert a.n_bags_valid == 1 and a.n_bags_total == 2
    assert a.panel_coverage == pytest.approx(0.5)


def test_frame_row_counts_and_jsonl_roundtrip(tmp_path):
    bag = build_bag_result(keys(), "s1", height=height(), baseline=baseline(),
                           region=region(), ref_px=100.0)
    frame = build_frame_result(keys(), "S101", [bag], n_slots=2, quality_flags="estimate")
    assert frame.n_bags_valid == 1 and frame.n_bags_with_region == 1
    assert frame.n_slots == 2

    path = tmp_path / "bag.jsonl"
    assert write_jsonl(path, [bag.to_row()]) == 1
    rows = read_jsonl(path)
    assert rows[0]["h_profile"] == pytest.approx(60.0)
    assert rows[0]["slot_id"] == "s1"
    assert rows[0]["pipeline_version"] == "p1"

    no_value = build_bag_result(keys(), "s2", measurement_valid=False)
    path2 = tmp_path / "null.jsonl"
    write_jsonl(path2, [no_value.to_row()])
    text = path2.read_text(encoding="utf-8")
    assert '"h_profile": null' in text
    assert json.loads(text)["h_profile"] is None
