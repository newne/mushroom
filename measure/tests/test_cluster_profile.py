"""`measure.cluster_profile`：H 三兄弟同源、半空簇必被排除。"""

import numpy as np
import pytest

from measure.bag_top import BaselineProfile
from measure.cluster_profile import ProfileConfig, profile_heights


def baseline(b, *, x0=0, valid=True, interp=None) -> BaselineProfile:
    b = np.asarray(b, float)
    return BaselineProfile(
        slot_id="s", x0=x0, b=b,
        valid_columns=np.ones(len(b), bool),
        interpolated=np.zeros(len(b), bool) if interp is None else np.asarray(interp, bool),
        baseline_valid=valid, coverage=1.0,
    )


def mask_flat(h=120, w=100, top=40, x0=0, x1=None) -> np.ndarray:
    m = np.zeros((h, w), bool)
    m[top:, x0:x1] = True
    return m


def test_flat_baseline_returns_all_three_equal():
    out = profile_heights(mask_flat(), baseline(np.full(80, 100.0)))
    assert out.status == "ok"
    assert out.h_profile == pytest.approx(60.0)
    assert out.h_p50 == pytest.approx(60.0)
    assert out.h_p90 == pytest.approx(60.0)
    assert out.valid_column_count == 80
    assert out.valid_column_ratio == pytest.approx(1.0)


def test_slanted_baseline_h_p90_differs_from_bbox_height():
    """b 从 100 斜到 179、t 平在 10 ⇒ h 90..169；外接框高是 190。"""
    m = np.zeros((200, 80), bool)
    m[10:, :] = True
    out = profile_heights(m, baseline(100.0 + np.arange(80)))
    values = np.arange(90.0, 170.0)
    assert out.h_profile == pytest.approx(values.mean())
    assert out.h_p50 == pytest.approx(np.median(values))
    assert out.h_p90 == pytest.approx(np.quantile(values, 0.9))
    bbox_height = 199 - 10 + 1
    assert abs(out.h_p90 - bbox_height) > 10  # 不是"簇外接框高度"


def test_floating_top_segment_is_rejected():
    """最高段悬空 20 px > max_gap ⇒ 这 40 列不是"被基线截断"，是被空隙截断。"""
    m = mask_flat()
    m[10:20, :40] = True
    out = profile_heights(m, baseline(np.full(80, 100.0)))
    assert out.valid_column_count == 40
    assert out.rejected["top_gap"] == 40
    assert out.h_profile == pytest.approx(60.0)


def test_all_columns_floating_yields_no_columns_and_insufficient_contour():
    m = np.zeros((120, 80), bool)
    m[10:20, :] = True
    m[40:, :] = True
    out = profile_heights(m, baseline(np.full(80, 100.0)))
    assert out.valid_column_count == 0
    assert out.h_profile is None and out.h_p50 is None and out.h_p90 is None
    assert "no_columns" in out.reasons and "insufficient_contour" in out.reasons


def test_reach_rule_is_independent_of_top_gap_rule():
    """间隙 6：max_gap=8 放行、reach_tol=4 拒绝；把 reach_tol 放到 8 又放行。"""
    m = np.zeros((120, 80), bool)
    m[10:20, :] = True
    m[26:, :] = True
    b = baseline(np.full(80, 100.0))

    strict = profile_heights(m, b, config=ProfileConfig(max_gap_px=8, reach_tol_px=4))
    assert strict.valid_column_count == 0
    assert strict.rejected["no_reach"] == 80

    loose = profile_heights(m, b, config=ProfileConfig(max_gap_px=8, reach_tol_px=8))
    assert loose.valid_column_count == 80
    assert loose.h_profile == pytest.approx(90.0)


def test_valid_column_ratio_boundary_is_inclusive():
    m = np.zeros((120, 100), bool)
    b = baseline(np.full(100, 100.0))
    for x in range(30):
        m[40:, x] = True
    for x in range(30, 100):
        m[10:20, x] = True
        m[40:, x] = True

    ok = profile_heights(m, b)
    assert ok.valid_column_count == 30
    assert ok.valid_column_ratio == pytest.approx(0.3)
    assert ok.status == "ok"

    m[40:, 29] = False  # 该列只剩悬空段 ⇒ 29/100
    bad = profile_heights(m, b)
    assert bad.valid_column_count == 29
    assert bad.status == "insufficient_contour"
    assert bad.h_profile is None


def test_interpolated_baseline_columns_are_excluded_from_v():
    m = mask_flat()
    m[10:40, :10] = True
    interp = np.zeros(80, bool)
    interp[:10] = True
    out = profile_heights(m, baseline(np.full(80, 100.0), interp=interp))
    assert out.valid_column_count == 70
    assert out.h_profile == pytest.approx(60.0)
    assert out.h_profile_with_interp == pytest.approx(63.75)


def test_baseline_invalid_makes_all_three_none():
    out = profile_heights(mask_flat(), baseline(np.full(80, 100.0), valid=False))
    assert out.h_profile is None and out.h_p50 is None and out.h_p90 is None
    assert out.status == "baseline_invalid"
    assert "baseline_invalid" in out.reasons


def test_association_failure_kills_height_but_leaves_columns():
    out = profile_heights(mask_flat(), baseline(np.full(80, 100.0)),
                          association_valid=False)
    assert out.h_profile is None
    assert out.status == "association_invalid"
    assert out.valid_column_count == 80  # 证据仍在，只是不出袋级高度


def test_invalid_columns_are_not_interpolated_into_the_statistic():
    """中间少一列前景（不是插值基线）不能把两侧线性补进来。"""
    m = mask_flat()
    m[:, 30] = False
    out = profile_heights(m, baseline(np.full(80, 100.0)))
    assert out.valid_column_count == 79
    assert np.isnan(out.column_heights[30])


def test_same_input_is_bit_identical():
    m = mask_flat()
    b = baseline(np.linspace(100, 120, 80))
    a = profile_heights(m, b)
    c = profile_heights(m, b)
    assert a.h_profile == c.h_profile
    assert np.array_equal(a.valid_columns, c.valid_columns)
