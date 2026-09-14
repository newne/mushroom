"""`measure.framing`：亮区质心偏移。

合成图（一块亮矩形摆在已知位置）就是最好的夹具——偏移的**真值**已知，
所以既能测"算得准不准"，也能测"什么时候必须拒绝算"。
"""

from __future__ import annotations

import numpy as np
import pytest
from measure.framing import Offset, bright_fraction, bright_region_offset, to_gray


def frame(center_x: float, center_y: float, *, size=(480, 640), half=(60, 60),
          bright=200.0, dark=20.0) -> np.ndarray:
    """暗底上摆一块亮矩形，返回灰度图。"""
    h, w = size
    g = np.full((h, w), dark, dtype=float)
    x0, x1 = int(center_x - half[0]), int(center_x + half[0])
    y0, y1 = int(center_y - half[1]), int(center_y + half[1])
    g[max(0, y0):y1, max(0, x0):x1] = bright
    return g


def test_centred_target_reports_zero_offset():
    got = bright_region_offset(frame(320, 240))
    assert got.found is True
    assert got.dx_px == pytest.approx(0.0, abs=1.0)
    assert got.dy_px == pytest.approx(0.0, abs=1.0)


def test_target_right_and_below_gives_positive_offsets():
    """dx>0 = 偏右、dy>0 = 偏**下**（光栅 y 向下）——`patrol.framing` 依赖这个约定。"""
    got = bright_region_offset(frame(380, 290))
    assert got.dx_px == pytest.approx(60.0, abs=1.0)
    assert got.dy_px == pytest.approx(50.0, abs=1.0)


def test_dark_frame_is_not_a_target():
    got = bright_region_offset(np.full((480, 640), 15.0))
    assert got.found is False
    assert "对比度" in got.reason


def test_blown_out_frame_says_overexposed():
    """整幅照亮 = 过曝：提示要和"全黑/没对比度"分开——现场处理方式完全不同。"""
    got = bright_region_offset(np.full((480, 640), 250.0))
    assert got.found is False
    assert "过曝" in got.reason


def test_mostly_bright_frame_is_refused():
    g = np.full((100, 100), 250.0)
    g[:10, :] = 20.0                      # 亮区 90% > MAX_FRACTION
    got = bright_region_offset(g)
    assert got.found is False and "过曝" in got.reason


def test_tiny_bright_spot_is_refused():
    g = np.full((400, 400), 10.0)
    g[200:203, 200:203] = 250.0           # 亮区 0.006% < MIN_FRACTION
    got = bright_region_offset(g)
    assert got.found is False and "没打亮" in got.reason


def test_tiny_image_is_refused():
    got = bright_region_offset(np.zeros((4, 4)))
    assert got.found is False and "太小" in got.reason


def test_confidence_falls_away_from_the_ideal_fraction():
    ideal = bright_region_offset(frame(320, 240, half=(60, 60)))     # 约 4.7%
    huge = bright_region_offset(frame(320, 240, half=(200, 150)))    # 更大一块
    assert 0.0 <= huge.confidence < ideal.confidence <= 1.0


def test_explicit_level_is_honoured():
    g = frame(320, 240, bright=120.0, dark=100.0)
    got = bright_region_offset(g, level=110)
    assert got.found is True and abs(got.dx_px) < 1.0


def test_offset_dataclass_matches_the_patrol_protocol_fields():
    """`patrol.framing.FramingError` 只按字段取用，形状必须对得上。"""
    o = Offset(dx_px=1.0, dy_px=2.0, found=True, confidence=0.9, reason="x")
    assert (o.dx_px, o.dy_px, o.found, o.confidence) == (1.0, 2.0, True, 0.9)


def test_bright_fraction_helper():
    # 亮块 120×120 摆在 480×640 的画面上
    assert bright_fraction(frame(320, 240)) == pytest.approx(120 * 120 / (480 * 640), rel=0.05)


def test_to_gray_uses_rec601_and_passes_through_gray():
    rgb = np.zeros((2, 2, 3))
    rgb[..., 0] = 255.0                    # 纯红
    assert to_gray(rgb)[0, 0] == pytest.approx(255 * 0.299)
    gray = np.arange(4, dtype=float).reshape(2, 2)
    assert np.array_equal(to_gray(gray), gray)


def test_to_gray_rejects_odd_shapes():
    with pytest.raises(ValueError):
        to_gray(np.zeros((2, 2, 2)))
