"""`measure.align` 的单元测试：合成帧带**已知平移真值**，方向/上限/残差门都要钉住。

只用合成图，**不读生产帧**（票 11 §5）。真值来自"同一张大画布上取两个窗口"，
与被测实现无关——不是把代码的算法再算一遍。

方向口径（票 11 §2）：`to_reference` = 第二帧 → 第一帧；`to_moving` = 第一帧 → 第二帧。
"""

from __future__ import annotations

import numpy as np
import pytest

from measure.align import AlignConfig, estimate_translation


# ---------- 合成帧 ----------

def synth_background(h: int, w: int, *, seed: int = 0, layout: int | None = None) -> np.ndarray:
    """静态背景（货架/管线风格）：**低频结构**为主，另加轻噪声。

    为什么不做 1 px 白噪声底：那种纹理的梯度场是随机的，ECC 的捕获范围只剩几像素
    （实测：13 px 平移直接不收敛）。真机帧是货架背板、横梁、立柱、管线——低频结构，
    捕获范围也来自它们。合成图必须保持同一性格，否则测的是假难度。

    `layout` 决定横梁/立柱的**位置**（默认 = `seed`）：换一个 layout 才是"另一个画面"，
    只换 `seed`（噪声/斑驳）仍然是同一场景——同一货架上重拍，本来就该配上。
    """
    rng = np.random.default_rng(seed)
    lay = np.random.default_rng(seed if layout is None else layout)
    beam_y = 0.20 + 0.45 * lay.random(2)
    pipe_x = 0.08 + 0.84 * lay.random(3)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 0.45 + 0.12 * np.sin(xx / 170.0) + 0.10 * np.cos(yy / 90.0)
    img += 0.20 * np.exp(-((yy - beam_y[0] * h) ** 2) / (2.0 * 14.0 ** 2))   # 横梁
    img += 0.18 * np.exp(-((yy - beam_y[1] * h) ** 2) / (2.0 * 10.0 ** 2))
    img += 0.22 * np.exp(-((xx - pipe_x[0] * w) ** 2) / (2.0 * 9.0 ** 2))    # 立柱/管线
    img += 0.20 * np.exp(-((xx - pipe_x[1] * w) ** 2) / (2.0 * 12.0 ** 2))
    img += 0.15 * np.exp(-((xx - pipe_x[2] * w) ** 2) / (2.0 * 7.0 ** 2))
    coarse = rng.normal(0.0, 0.05, (h // 8 + 1, w // 8 + 1)).astype(np.float32)
    img += np.kron(coarse, np.ones((8, 8), np.float32))[:h, :w]         # 背板斑驳（低频）
    img += rng.normal(0.0, 0.015, (h, w)).astype(np.float32)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def translated_pair(h: int, w: int, dx: int, dy: int, *, seed: int = 0, pad: int = 4,
                    layout: int | None = None):
    """同一画布取两窗：第一帧内容 `(x, y)` 处的纹理出现在第二帧 `(x+dx, y+dy)`。

    返回 `(reference, moving)`（同一物理场景、机位平移 `(dx, dy)`）。
    """
    big = synth_background(h + abs(dy) + pad, w + abs(dx) + pad, seed=seed, layout=layout)
    ky, kx = max(0, dy), max(0, dx)
    ref = big[ky:ky + h, kx:kx + w]
    mov = big[ky - dy:ky - dy + h, kx - dx:kx - dx + w]
    assert ref.shape == (h, w) and mov.shape == (h, w)
    return np.ascontiguousarray(ref), np.ascontiguousarray(mov)


def background_mask(h: int, w: int, *, frac: float = 0.6) -> np.ndarray:
    """静态背景掩码：上半屏是货架/管线；下半屏留给"在长的菌簇"，不参与配准。"""
    mask = np.zeros((h, w), bool)
    mask[: int(h * frac), :] = True
    return mask


def test_known_translation_is_recovered_and_maps_back_within_one_pixel():
    ref, mov = translated_pair(360, 480, 13, -7)
    al = estimate_translation(ref, mov, background_mask(360, 480))
    assert al.status == "ok", al.reasons
    assert al.transform_moving_to_ref is not None
    # 第二帧 (x+13, y-7) → 第一帧 (x, y)
    for (x, y) in [(100, 50), (240, 180), (430, 300)]:
        back = al.to_reference(x + 13, y - 7)
        assert back[0] == pytest.approx(x, abs=1.0)
        assert back[1] == pytest.approx(y, abs=1.0)
    # 方向另一头也要对：第一帧 → 第二帧
    fwd = al.to_moving(240.0, 180.0)
    assert fwd[0] == pytest.approx(253.0, abs=1.0)
    assert fwd[1] == pytest.approx(173.0, abs=1.0)
    assert al.shift_px[0] == pytest.approx(13.0, abs=1.0)
    assert al.shift_px[1] == pytest.approx(-7.0, abs=1.0)

def _bilinear_view(canvas: np.ndarray, y0: float, x0: float, h: int, w: int) -> np.ndarray:
    """`canvas[y0+dy, x0+dx]` 的双线性视图（亚像素平移的真值生成器，边界夹取）。"""
    ys = np.arange(h, dtype=np.float64) + y0
    xs = np.arange(w, dtype=np.float64) + x0
    yi = np.clip(np.floor(ys).astype(np.int64), 0, canvas.shape[0] - 2)
    xi = np.clip(np.floor(xs).astype(np.int64), 0, canvas.shape[1] - 2)
    wy = (ys - yi)[:, None]
    wx = (xs - xi)[None, :]
    a = canvas[np.ix_(yi, xi)]
    b = canvas[np.ix_(yi, xi + 1)]
    c = canvas[np.ix_(yi + 1, xi)]
    d = canvas[np.ix_(yi + 1, xi + 1)]
    return (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy


def fractional_pair(h: int, w: int, dx: float, dy: float, *, seed: int = 0, pad: int = 8):
    """亚像素平移对：第二帧内容相对第一帧平移 `(dx, dy)`（可为小数）。"""
    big = synth_background(h + int(abs(dy)) + pad, w + int(abs(dx)) + pad, seed=seed)
    ky, kx = max(0.0, dy), max(0.0, dx)
    return (_bilinear_view(big, ky, kx, h, w),
            _bilinear_view(big, ky - dy, kx - dx, h, w))


# ---------- 上限门：位移超限必须明确拒绝 ----------

def test_shift_beyond_limit_is_rejected_as_view_changed():
    ref, mov = translated_pair(360, 480, 13, -7)
    al = estimate_translation(ref, mov, background_mask(360, 480),
                              config=AlignConfig(max_shift_px=10.0))
    assert al.status == "view_changed"
    assert al.transform_ref_to_moving is None and al.transform_moving_to_ref is None
    # 失败态也要把"卡在哪"讲清楚，且不许硬给一个能用的变换
    assert any("shift_px" in r for r in al.reasons), al.reasons
    assert al.shift_px is not None and np.hypot(*al.shift_px) == pytest.approx(np.hypot(13, 7), abs=1.0)
    with pytest.raises(ValueError):
        al.to_reference(200.0, 100.0)


def test_shift_within_limit_is_accepted_with_same_pair():
    """同一对帧、只把上限放宽 ⇒ 必须 ok：证明拒绝来自**门**，不是这次配准本身不行。"""
    ref, mov = translated_pair(360, 480, 13, -7)
    al = estimate_translation(ref, mov, background_mask(360, 480),
                              config=AlignConfig(max_shift_px=32.0))
    assert al.ok, al.reasons


# ---------- 残差门：内容对不上必须明确拒绝 ----------

def test_unrelated_frames_never_return_a_transform():
    """两个**不同画面**（横梁/立柱位置都不同）不许被配到一起。"""
    ref, _ = translated_pair(360, 480, 0, 0, seed=1, layout=1)
    other, _ = translated_pair(360, 480, 0, 0, seed=99, layout=99)
    al = estimate_translation(ref, other, background_mask(360, 480))
    assert not al.ok
    assert al.status in ("registration_unreliable", "registration_failed")
    assert al.transform_ref_to_moving is None
    assert al.transform_moving_to_ref is None


def test_residual_gate_rejects_when_score_below_threshold():
    """好帧 + 极严的残差门 ⇒ 卡在 registration_unreliable（门本身可被验证）。"""
    ref, mov = translated_pair(360, 480, 13, -7)
    strict = AlignConfig(min_score=0.9999999)
    al = estimate_translation(ref, mov, background_mask(360, 480), config=strict)
    assert al.status == "registration_unreliable"
    assert al.score is not None and al.score < strict.min_score
    assert al.transform_ref_to_moving is None
    assert any("ecc_score" in r for r in al.reasons), al.reasons


# ---------- 退化输入：不抛异常，给 registration_failed ----------

def test_blank_frames_are_registration_failed_not_an_exception():
    flat = np.full((360, 480), 0.5, np.float32)
    al = estimate_translation(flat, flat, background_mask(360, 480))
    assert al.status == "registration_failed"
    assert al.transform_ref_to_moving is None
    assert al.reasons


# ---------- 平移优先：不许偷偷上旋转/缩放/透视 ----------

def test_transform_is_pure_translation_no_rotation_scale_or_perspective():
    ref, mov = translated_pair(360, 480, 13, -7)
    al = estimate_translation(ref, mov, background_mask(360, 480))
    assert al.ok, al.reasons
    m = np.asarray(al.transform_ref_to_moving, np.float64)
    assert m.shape == (2, 3)
    assert np.allclose(m[:, :2], np.eye(2), atol=1e-6)      # 无旋转、无缩放、无错切

# ---------- 掩码纪律：不许拿菌簇配准 ----------

def with_cluster(img: np.ndarray, cy: int, cx: int, r: int) -> np.ndarray:
    """在下半屏（掩码之外）画一团"在长的菌簇"。"""
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    out = img.copy()
    out[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = 0.98
    return out


def test_growing_cluster_outside_mask_does_not_move_the_estimate():
    """菌簇本身在长：只改掩码外那团东西，配准结果必须**不动**（≤1 px）。"""
    ref, mov = translated_pair(360, 480, 13, -7)
    mask = background_mask(360, 480)
    base = estimate_translation(ref, mov, mask)
    grown = estimate_translation(ref, with_cluster(mov, 300, 240, 55), mask)
    assert base.ok and grown.ok, (base.reasons, grown.reasons)
    assert grown.shift_px[0] == pytest.approx(base.shift_px[0], abs=1.0)
    assert grown.shift_px[1] == pytest.approx(base.shift_px[1], abs=1.0)
    # 真值仍是背景位移，不是菌簇的位置
    assert grown.shift_px[0] == pytest.approx(13.0, abs=1.0)
    assert grown.shift_px[1] == pytest.approx(-7.0, abs=1.0)


def test_mask_too_small_is_unreliable_not_a_silent_guess():
    ref, mov = translated_pair(360, 480, 13, -7)
    tiny = np.zeros((360, 480), bool)
    tiny[:6, :6] = True                      # 36 px ≈ 0.02% ⇒ 低于 min_mask_frac
    al = estimate_translation(ref, mov, tiny)
    assert al.status == "registration_unreliable"
    assert al.transform_ref_to_moving is None
    assert any("background_mask_too_small" in r for r in al.reasons), al.reasons


# ---------- 低分辨率估、原图出 ----------

def test_low_resolution_estimate_maps_back_to_original_pixels():
    h, w = 408, 1440
    ref, mov = translated_pair(h, w, 37, -21)
    mask = background_mask(h, w)
    al = estimate_translation(ref, mov, mask, config=AlignConfig(max_estimate_edge_px=240))
    assert al.ok, al.reasons
    assert al.estimate_scale == 6                       # 长边 1440 → 240
    assert al.shift_px[0] == pytest.approx(37.0, abs=1.0)   # 原图像素，不是估计图像素
    assert al.shift_px[1] == pytest.approx(-21.0, abs=1.0)
    back = al.to_reference(37.0 + 300.0, -21.0 + 200.0)
    assert back[0] == pytest.approx(300.0, abs=1.0)
    assert back[1] == pytest.approx(200.0, abs=1.0)
    assert al.moving_hw == (h, w)


def test_subpixel_shift_is_recovered_within_one_pixel():
    ref, mov = fractional_pair(360, 480, 13.4, -7.3)
    al = estimate_translation(ref, mov, background_mask(360, 480))
    assert al.ok, al.reasons
    assert al.shift_px[0] == pytest.approx(13.4, abs=1.0)
    assert al.shift_px[1] == pytest.approx(-7.3, abs=1.0)


# ---------- map_roi：参考帧 ROI → 本帧整数框（原图坐标、可切片） ----------

def _assert_roi_covers(al, box, *, expect, contained=True):
    """契约：与理想整框差 ≤1 px；理想框**落在本帧内**时还必须盖住精确映射（不切内容）。"""
    got = al.map_roi(box)
    x0, y0, x1, y1 = box
    dx, dy = al.shift_px
    if contained:
        assert got[0] <= x0 + dx and got[1] <= y0 + dy, (got, al.shift_px)
        assert got[2] >= x1 + dx and got[3] >= y1 + dy, (got, al.shift_px)
    for g, e in zip(got, expect):
        assert abs(g - e) <= 1, (got, expect)
    return got


def test_map_roi_maps_reference_box_into_moving_frame_in_original_pixels():
    ref, mov = translated_pair(360, 480, 13, -7)
    al = estimate_translation(ref, mov, background_mask(360, 480))
    assert al.ok, al.reasons
    _assert_roi_covers(al, (100, 50, 220, 140), expect=(113, 43, 233, 133))
    # 越界要夹到本帧边界
    _assert_roi_covers(al, (450, 320, 480, 360), expect=(463, 313, 480, 353), contained=False)
    _assert_roi_covers(al, (0, 0, 50, 30), expect=(13, 0, 63, 23), contained=False)
    got = al.map_roi((450, 320, 480, 360))
    h, w = al.moving_hw
    assert 0 <= got[0] < got[2] <= w and 0 <= got[1] < got[3] <= h


# ---------- 入参校验：形状不对是**程序错误**，不是数据失败 ----------

def test_mismatched_frame_shapes_raise_value_error():
    ref, _ = translated_pair(360, 480, 0, 0)
    other, _ = translated_pair(356, 480, 0, 0)
    with pytest.raises(ValueError):
        estimate_translation(ref, other, background_mask(360, 480))


def test_mask_shape_must_match_frame():
    ref, mov = translated_pair(360, 480, 0, 0)
    with pytest.raises(ValueError):
        estimate_translation(ref, mov, background_mask(120, 160))

# ---------- 预算：spec §6.1 单帧 ≤3.75 s ----------

def test_full_hd_frame_alignment_fits_the_single_frame_budget():
    """真机帧 2880×1616 上量 **L1 对齐自身**（下游还要在同 3.75 s 里分时间）。"""
    h, w = 1616, 2880
    ref, mov = translated_pair(h, w, 23, -11, pad=16)
    al = estimate_translation(ref, mov, background_mask(h, w))
    assert al.ok, al.reasons
    assert al.estimate_scale == 4                     # 长边 2880 → 720（低分辨率估变换）
    assert al.shift_px[0] == pytest.approx(23.0, abs=1.0)
    assert al.shift_px[1] == pytest.approx(-11.0, abs=1.0)
    assert al.elapsed_s <= 3.75, al.elapsed_s
