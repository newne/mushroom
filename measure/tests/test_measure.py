import numpy as np
import pytest
from measure.pipeline import DummyDetector, aggregate_box, aggregate_round
from measure.quality import (
    DEFAULT_ABS_LAPVAR_FLOOR,
    DEFAULT_DARK_P99,
    VERDICT_LOW,
    VERDICT_OK,
    VERDICT_REJECT,
    assess_exposure,
    frame_quality,
    frame_stats,
    is_blurry,
    is_dark,
    is_overexposed,
    laplacian_variance,
    overexposed_fraction,
    p99_brightness,
    relative_verdict,
    station_baseline,
)


def rng_gray(seed=1):
    return np.random.default_rng(seed).integers(0, 255, (64, 64), dtype=np.uint8)


# ---------- quality ----------

def test_flat_image_is_blurry():
    flat = np.full((32, 32), 128, dtype=np.uint8)
    assert laplacian_variance(flat) == 0.0
    assert is_blurry(flat)


def test_noisy_image_is_sharp():
    assert not is_blurry(rng_gray())


def test_overexposure():
    mostly_mid = np.full((32, 32), 100, dtype=np.uint8)
    assert not is_overexposed(mostly_mid)
    blown = np.full((32, 32), 100, dtype=np.uint8)
    blown[:10, :] = 255
    assert overexposed_fraction(blown) > 0.05
    assert is_overexposed(blown)


def test_frame_quality_flags():
    ok, flags = frame_quality(rng_gray())
    assert ok and flags == ""
    bad, flags = frame_quality(np.full((32, 32), 255, dtype=np.uint8))
    assert not bad and "overexposed" in flags and "blurry" in flags
    # 标记串用 `+`（票 11 §3），不是逗号
    assert "," not in flags


def test_dark_frame_is_no_light_and_unusable():
    """灯未亮的整帧：标 no_light，处置是**不可用**（不是降权）。"""
    dark = np.full((32, 32), 12, dtype=np.uint8)
    ok, flags = frame_quality(dark)
    assert not ok
    assert "no_light" in flags.split("+")
    from measure import contract

    assert not contract.is_usable(flags)


def test_vignetted_frame_is_not_mistaken_for_no_light():
    """强晕影但中心有亮区 ⇒ 不许判成"灯没亮"（拿 S306 真机帧的形状造）。"""
    yy, xx = np.mgrid[0:200, 0:200]
    dist = np.hypot(yy - 100, xx - 100) / 141.0
    vignette = np.clip(230 * (1.0 - dist ** 2), 8, 255).astype(np.uint8)
    assert not is_dark(vignette)
    ok, flags = frame_quality(vignette, blur_threshold=0.0)   # 只考近黑判据
    assert "no_light" not in flags and ok


# ---------- 相对化判据（票 14） ----------

def _noise_frame(seed: int, size: int = 64) -> np.ndarray:
    """有纹理的帧：拉普拉斯方差高（≈ 数千）。"""
    return np.random.default_rng(seed).integers(0, 255, (size, size), dtype=np.uint8)


def _smooth_frame(seed: int, size: int = 64) -> np.ndarray:
    """几乎无纹理的帧：极缓慢的斜面 + 微噪声（lapvar 远低于绝对下限）。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    base = 120 + 20 * (yy + xx) / (2 * size)
    return np.clip(base + rng.normal(0, 0.4, (size, size)), 0, 255).astype(np.uint8)


def _soft_frame(seed: int, size: int = 64, amplitude: float = 3.0) -> np.ndarray:
    """纹理变弱但还没到绝对下限的帧（失焦的中间态）。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    base = 120 + 20 * (yy + xx) / (2 * size)
    return np.clip(base + rng.normal(0, amplitude, (size, size)), 0, 255).astype(np.uint8)


def test_frame_stats_and_baseline_from_noise_frames():
    stats = [frame_stats(_noise_frame(i)) for i in range(12)]
    base = station_baseline(stats)
    assert base is not None and base.n == 12
    assert base.lapvar_median == pytest.approx(np.median([s.lapvar for s in stats]))
    assert station_baseline([]) is None


def test_relative_verdict_ok_when_frame_matches_baseline():
    stats = [frame_stats(_noise_frame(i)) for i in range(12)]
    base = station_baseline(stats)
    verdict, reasons = relative_verdict(frame_stats(_noise_frame(99)), base)
    assert verdict == VERDICT_OK and reasons == []


def test_relative_verdict_low_when_texture_collapses_but_not_dark():
    """纹理塌了（相对基线低）但亮度正常 ⇒ low_confidence，不是 reject。"""
    stats = [frame_stats(_noise_frame(i)) for i in range(12)]
    base = station_baseline(stats)
    now = frame_stats(_soft_frame(1, amplitude=1.5))   # lapvar ≈ 46：高于绝对下限、远低于基线
    assert now.p99 >= DEFAULT_DARK_P99             # 不是近黑
    assert now.lapvar >= DEFAULT_ABS_LAPVAR_FLOOR  # 也没到绝对下限
    verdict, reasons = relative_verdict(now, base)
    assert verdict == VERDICT_LOW
    assert "lapvar_below_baseline" in reasons


def test_relative_verdict_low_when_baseline_missing():
    """基线不足时**不是 ok**：宁可不发 verified，也不乱发。"""
    verdict, reasons = relative_verdict(frame_stats(_noise_frame(0)), None)
    assert verdict == VERDICT_LOW and reasons == ["baseline_missing"]
    base = station_baseline([frame_stats(_noise_frame(i)) for i in range(5)])
    assert relative_verdict(frame_stats(_noise_frame(1)), base)[0] == VERDICT_LOW


def test_relative_verdict_rejects_absolute_floor_even_if_baseline_agrees():
    """**绝对下限兜底**（Chief 约束）：相机整体失焦 ⇒ 所有帧相对基线都"正常"，
    只有绝对下限能拦住这种系统性漂移。"""
    blind = [_smooth_frame(i) for i in range(12)]          # 一批整体失焦的帧
    base = station_baseline([frame_stats(f) for f in blind])
    assert base is not None
    verdict, reasons = relative_verdict(frame_stats(blind[0]), base)
    assert verdict == VERDICT_REJECT
    assert "below_abs_lapvar_floor" in reasons


def test_relative_verdict_rejects_dark_frame():
    dark = np.full((64, 64), 12, dtype=np.uint8)
    verdict, reasons = relative_verdict(frame_stats(dark), None)
    assert verdict == VERDICT_REJECT and reasons == ["no_light"]


# ---------- 曝光轴：唯一一份定义（Chief 2026-10-13 裁定） ----------

def test_assess_exposure_is_the_single_definition():
    """欠曝端与过曝端在同一个入口判，标记名唯一。"""
    dark = np.full((32, 32), 10, dtype=np.uint8)
    got = assess_exposure(dark)
    assert got.underexposed and not got.overexposed
    assert got.flags == ("no_light",)
    blown = np.full((32, 32), 255, dtype=np.uint8)
    got2 = assess_exposure(blown)
    assert got2.overexposed and not got2.underexposed
    assert got2.flags == ("overexposed",)
    good = np.full((32, 32), 128, dtype=np.uint8)
    assert assess_exposure(good).flags == ()


def test_frame_quality_uses_the_same_exposure_definition():
    """`frame_quality` 的曝光两端必须与 `assess_exposure` 完全一致（不许两处各判一次）。"""
    for frame in (np.full((32, 32), 10, dtype=np.uint8),        # 近黑
                  np.full((32, 32), 255, dtype=np.uint8),       # 全白
                  rng_gray()):                                  # 正常
        _, flags = frame_quality(frame)
        exp = assess_exposure(frame)
        assert [f for f in flags.split("+") if f in ("no_light", "overexposed")] == list(exp.flags)


def test_legacy_underexposed_alias_reads_but_is_never_emitted():
    """历史别名 `underexposed` 只在**读**时认；产出永远是 `no_light`。"""
    from measure.quality import canonical_flag

    assert canonical_flag("underexposed") == "no_light"
    assert canonical_flag("no_light") == "no_light"
    dark = np.full((32, 32), 10, dtype=np.uint8)
    _, flags = frame_quality(dark)
    assert "underexposed" not in flags          # 不产出别名
    # 读兼容：下游用合同解析器读旧串时，别名折回唯一名字
    from measure import contract

    assert contract.parse_flags("underexposed+blurry") == {"no_light", "blurry"}


def test_quality_requires_2d():
    with pytest.raises(ValueError):
        laplacian_variance(np.zeros((4, 4, 3)))


# ---------- aggregation ----------

def test_aggregate_box_drops_flagged():
    from measure.pipeline import Detection

    dets = [
        Detection(box_id="B01", len_mm=50, cap_mm=30),
        Detection(box_id="B01", len_mm=54, cap_mm=32),
        Detection(box_id="B01", len_mm=200, cap_mm=60, quality_ok=False, flags="blurry"),
    ]
    s = aggregate_box(dets)
    assert s.n_total == 3
    assert s.n_used == 2
    assert s.mean_len_mm == pytest.approx(52.0)
    assert "blurry" in s.quality_flags
    assert s.mean_cap_mm == pytest.approx(31.0)


def test_aggregate_box_percentiles():
    from measure.pipeline import Detection

    dets = [Detection(box_id="B01", len_mm=v) for v in range(10, 21)]  # 10..20
    s = aggregate_box(dets)
    assert s.p10_len_mm == pytest.approx(11.0)
    assert s.p90_len_mm == pytest.approx(19.0)


def test_aggregate_box_empty_len():
    from measure.pipeline import Detection

    s = aggregate_box([Detection(box_id="B01", cap_mm=30)])
    assert s.mean_len_mm is None and s.mean_cap_mm == pytest.approx(30.0)


def test_aggregate_round_groups_by_box():
    dets = DummyDetector(n=2).detect(None, mm_per_px=0.1, box_id="B01") + \
        DummyDetector(n=1).detect(None, mm_per_px=0.1, box_id="B02")
    stats = aggregate_round(dets)
    assert set(stats) == {"B01", "B02"}
    assert stats["B01"].n_total == 2
    assert stats["B02"].mean_len_mm == pytest.approx(60.0)
