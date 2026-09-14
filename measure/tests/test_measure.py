import numpy as np
import pytest
from measure.pipeline import DummyDetector, aggregate_box, aggregate_round
from measure.quality import (
    frame_quality,
    is_blurry,
    is_overexposed,
    laplacian_variance,
    overexposed_fraction,
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
