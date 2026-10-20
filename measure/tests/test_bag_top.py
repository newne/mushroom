"""`measure.bag_top`：映射方向、两级可用性、插值边界。"""

import numpy as np
import pytest

from measure.align import Alignment
from measure.bag_top import BaselineConfig, map_bag_top


def alignment(*, dx=0.0, dy=0.0, status="ok", h=200, w=300) -> Alignment:
    matrix = ((1.0, 0.0, dx), (0.0, 1.0, dy))
    if status != "ok":
        return Alignment(status=status, transform_ref_to_moving=None,
                         transform_moving_to_ref=None, score=None, shift_px=None,
                         estimate_scale=1, moving_hw=(h, w))
    return Alignment(status=status, transform_ref_to_moving=matrix,
                     transform_moving_to_ref=((1.0, 0.0, -dx), (0.0, 1.0, -dy)),
                     score=0.99, shift_px=(dx, dy), estimate_scale=1,
                     moving_hw=(h, w))


def line(x0=10, x1=90, y=100.0) -> np.ndarray:
    xs = np.arange(x0, x1, dtype=float)
    return np.column_stack([xs, np.full_like(xs, y)])


def occlusion(w=300, h=200, cols=()) -> np.ndarray:
    mask = np.zeros((h, w), bool)
    for x in cols:
        mask[:, x] = True
    return mask


def bag_mask(w=300, h=200, top=110) -> np.ndarray:
    mask = np.zeros((h, w), bool)
    mask[top:, :] = True
    return mask


def test_identity_maps_baseline_without_shifting():
    p = map_bag_top(line(), alignment(), slot_id="slot-1")
    assert p.baseline_valid
    assert p.x0 == 10 and len(p.b) == 80
    assert np.allclose(p.b, 100.0)
    assert p.valid_columns.all()
    assert not p.interpolated.any()
    assert p.bag_shift_px is None  # 没给 bag_mask ⇒ 未核偏移，不是失败


def test_translation_maps_baseline_in_original_pixels():
    """ref→moving 平移 (+7, −5)：袋顶在 moving 帧里更靠上 5 px、靠右 7 px。"""
    p = map_bag_top(line(), alignment(dx=7, dy=-5), slot_id="slot-1")
    assert p.baseline_valid
    assert p.x0 == 17
    assert np.allclose(p.b, 95.0)


def test_failed_alignment_yields_no_baseline():
    p = map_bag_top(line(), alignment(status="view_changed"), slot_id="slot-1")
    assert not p.baseline_valid
    assert p.b is None
    assert "view_changed" in p.reasons


def test_slot_missing_is_rejected_not_borrowed():
    p = map_bag_top(line(), alignment(), slot_id="slot-9",
                    template_slot_ids=["slot-1", "slot-2"])
    assert not p.baseline_valid and p.b is None
    assert p.reasons == ("slot_missing",)


def test_bag_shift_uses_observed_bag_top_not_camera_shift():
    """对齐是 0 位移，但袋口实际偏了 50 px ⇒ 必须按袋口偏移拒绝。"""
    p = map_bag_top(line(), alignment(), slot_id="slot-1",
                    bag_mask=bag_mask(top=150))
    assert not p.baseline_valid and p.b is None
    assert p.bag_shift_px == pytest.approx(50.0)
    assert "bag_shift_exceeded" in p.reasons


def test_bag_shift_within_limit_keeps_baseline():
    p = map_bag_top(line(), alignment(), slot_id="slot-1",
                    bag_mask=bag_mask(top=110))
    assert p.baseline_valid
    assert p.bag_shift_px == pytest.approx(10.0)


def test_external_occlusion_is_interpolated_but_not_counted_as_visible():
    p = map_bag_top(line(), alignment(), slot_id="slot-1",
                    occluded=occlusion(cols=range(40, 50)))
    assert p.baseline_valid
    assert int(p.valid_columns.sum()) == 70
    assert int(p.interpolated.sum()) == 10
    assert p.interpolated_ratio == pytest.approx(0.125)
    assert np.allclose(p.b, 100.0)  # 插值列仍有值，只是不算"可见证据"


def test_visibility_boundary_exactly_eight_columns_and_twenty_percent():
    """span=40：可见 8 列 ⇒ 同时压线 8/0.2，必须判可用；少一列即不可用。"""
    narrow = line(10, 50)
    ok = map_bag_top(narrow, alignment(), slot_id="s", occluded=occlusion(cols=range(18, 50)))
    assert int(ok.valid_columns.sum()) == 8
    assert ok.coverage == pytest.approx(0.2)
    assert ok.baseline_valid

    bad = map_bag_top(narrow, alignment(), slot_id="s", occluded=occlusion(cols=range(17, 50)))
    assert int(bad.valid_columns.sum()) == 7
    assert not bad.baseline_valid
    assert "too_few_columns" in bad.reasons


def test_baseline_never_extrapolates_outside_slot_span():
    p = map_bag_top(line(10, 50), alignment(), slot_id="s")
    assert p.x0 == 10
    assert len(p.b) == 40
    assert np.isfinite(p.b).all()


def test_columns_outside_the_frame_have_no_baseline():
    p = map_bag_top(line(-5, 15), alignment(w=300), slot_id="s")
    assert p.x0 == -5
    assert np.isnan(p.b[:5]).all()
    assert np.isfinite(p.b[5:]).all()
    assert int(p.valid_columns.sum()) == 15
    assert p.coverage == pytest.approx(1.0)  # 分母只算帧内列


def test_same_input_twice_is_bit_identical():
    a = map_bag_top(line(), alignment(dx=3.5, dy=-2), slot_id="s",
                    bag_mask=bag_mask(top=108))
    b = map_bag_top(line(), alignment(dx=3.5, dy=-2), slot_id="s",
                    bag_mask=bag_mask(top=108))
    assert np.array_equal(a.b, b.b)
    assert a.reasons == b.reasons


def test_config_thresholds_are_configurable():
    p = map_bag_top(line(10, 50), alignment(), slot_id="s",
                    occluded=occlusion(cols=range(18, 50)),
                    config=BaselineConfig(min_baseline_columns=9))
    assert not p.baseline_valid
    assert "too_few_columns" in p.reasons
