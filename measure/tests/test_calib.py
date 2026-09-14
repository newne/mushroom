import numpy as np
import pytest
from measure.calib import apply_scale, cap_diameter_mm, fit_circle, stipe_length_mm


def circle_points(r=50.0, n=36, cx=200.0, cy=150.0):
    return [(cx + r * np.cos(2 * np.pi * i / n), cy + r * np.sin(2 * np.pi * i / n))
            for i in range(n)]


def test_fit_circle_on_synthetic_circle():
    cx, cy, r = fit_circle(circle_points())
    assert abs(cx - 200.0) < 1e-6
    assert abs(cy - 150.0) < 1e-6
    assert abs(r - 50.0) < 1e-6


def test_cap_diameter_mm_with_scale():
    # 半径 50px，标定 0.1 mm/px → 直径 10 mm
    assert cap_diameter_mm(circle_points(), mm_per_px=0.1) == pytest.approx(10.0)


def test_fit_circle_needs_3_points():
    with pytest.raises(ValueError):
        fit_circle([(0, 0), (1, 1)])


def test_apply_scale():
    assert apply_scale([(10, 20)], 0.5) == [(5.0, 10.0)]


def test_stipe_length_mm_45deg_view():
    # 投影 10√2 mm，45° 视角 → 实际长度 20 mm
    base = (0.0, 0.0)
    tip = (100.0, 100.0)  # 100√2 px
    assert stipe_length_mm(base, tip, mm_per_px=0.1, view_angle_deg=45.0) == pytest.approx(20.0)


def test_stipe_length_mm_invalid_angle():
    with pytest.raises(ValueError):
        stipe_length_mm((0, 0), (1, 1), 0.1, view_angle_deg=0)
