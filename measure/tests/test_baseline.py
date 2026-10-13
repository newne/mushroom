"""`measure.baseline` 的单元测试：合成图有真值，形状/截断/兜底都要钉住。"""

import numpy as np
import pytest

from measure.baseline import (
    BaselineConfig,
    BaselineDetector,
    detect_blobs,
    otsu_threshold,
    rgb_value_sat,
)

MM_PER_PX = 0.5


def circle_frame(h: int, w: int, circles, *, bg: int = 30, fg: int = 235) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w, 3), bg, np.uint8)
    for cy, cx, r in circles:
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = fg
    return img


def cfg(**kw) -> BaselineConfig:
    base = dict(min_area_px_floor=100)
    base.update(kw)
    return BaselineConfig(**base)


def test_rgb_value_sat_grayscale_has_zero_saturation():
    img = circle_frame(40, 40, [(20, 20, 10)])
    val, sat = rgb_value_sat(img)
    assert val.shape == (40, 40)
    assert pytest.approx(float(sat.max()), abs=1e-6) == 0.0


def test_otsu_separates_bimodal():
    img = circle_frame(200, 200, [(100, 100, 40)])
    val, _ = rgb_value_sat(img)
    thr = otsu_threshold(val)
    assert 30 / 255 < thr < 235 / 255


def test_single_circle_measured_with_known_truth():
    """r=55px、mm/px=0.5 ⇒ 直径 55.0mm（等面积圆径口径）。"""
    img = circle_frame(300, 300, [(150, 150, 55)])
    dets, diag = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105", config=cfg())
    assert diag["reason"] == ""
    assert len(dets) == 1
    d = dets[0]
    m = d.metrics
    assert m.area_px == pytest.approx(9473, abs=60)
    assert m.cluster_w_px == pytest.approx(109, abs=2)
    assert m.cluster_h_px == pytest.approx(109, abs=2)
    assert m.eq_diameter_px * MM_PER_PX == pytest.approx(55.0, abs=1.0)
    # 单档平视：菌盖直径与菌杆长度都不可测 ⇒ 恒 None
    assert d.cap_mm is None
    assert d.len_mm is None
    assert d.flags == ""


def test_two_separate_circles_two_instances():
    img = circle_frame(400, 600, [(150, 150, 55), (150, 450, 55)])
    dets, diag = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105", config=cfg())
    assert len(dets) == 2
    assert diag["n_split_regions"] == 0
    xs = sorted(round(d.metrics.bbox_px[0]) for d in dets)
    assert xs == [96, 396]


def test_small_noise_blob_is_dropped():
    img = circle_frame(400, 600, [(150, 150, 55), (40, 580, 6)])
    dets, _ = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105", config=cfg())
    assert len(dets) == 1


def test_dark_frame_has_no_foreground():
    dets, diag = detect_blobs(np.full((200, 200, 3), 20, np.uint8),
                              mm_per_px=MM_PER_PX, box_id="B105", config=cfg())
    assert dets == []
    assert diag["reason"] == "no_foreground"


def test_fixed_threshold_override_is_recorded():
    img = circle_frame(300, 300, [(150, 150, 55)])
    dets, diag = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105",
                              config=cfg(val_min=0.55))
    assert diag["val_min_source"] == "fixed"
    assert diag["val_min"] == pytest.approx(0.55)
    assert len(dets) == 1


def test_whole_frame_fallback_flags_low_confidence():
    """亮区铺满整幅（>> max_area_frac）时：整块兜底 + low_confidence + 不冒充朵数。"""
    img = np.full((300, 300, 3), 30, np.uint8)
    img[10:290, 10:290] = 235          # 占 87% 画面
    dets, diag = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105",
                              config=cfg(max_area_frac=0.35))
    assert diag["reason"] == "whole_frame_fallback"
    assert len(dets) == 1
    assert dets[0].flags == "low_confidence"
    assert dets[0].cap_mm is None and dets[0].len_mm is None

    off, diag2 = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105",
                              config=cfg(max_area_frac=0.35, whole_frame_fallback=False))
    assert off == []
    assert diag2["reason"] == "fg_too_large"


def test_detector_implements_protocol_and_keeps_diag():
    det = BaselineDetector(cfg())
    img = circle_frame(300, 300, [(150, 150, 55)])
    out = det.detect(img, mm_per_px=MM_PER_PX, box_id="B105")
    assert len(out) == 1
    assert det.last_diag["n_kept"] == 1
    assert det.last_detections == out


def test_projected_diameter_is_not_a_cap_measurement():
    """回归：不许拿平视投影当菌盖直径（票 11 §9）。"""
    img = circle_frame(300, 300, [(150, 150, 55)])
    dets, _ = detect_blobs(img, mm_per_px=MM_PER_PX, box_id="B105", config=cfg())
    assert dets[0].cap_mm is None
    assert dets[0].metrics.eq_diameter_px > 0     # 数据仍在（像素口径），只是不进 cap 列
