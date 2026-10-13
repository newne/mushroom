"""`measure.imageops` 的单元测试：纯 numpy 原语必须自己是对的。

这些原语没有 cv2/scipy 兜底，错一个就整条测量链错，所以每个都拿"手算得到"的小例子钉住。
"""

import numpy as np
import pytest

from measure import imageops as io


def test_label_components_counts_and_sizes():
    mask = np.zeros((10, 12), bool)
    mask[2:5, 1:4] = True        # 3x3
    mask[2:4, 8:10] = True       # 2x2
    mask[8, 0] = True            # 1
    labels, sizes = io.label_components(mask)
    assert set(np.unique(labels)) == {0, 1, 2, 3}
    assert sorted(sizes[1:]) == [1, 4, 9]
    # sizes 必须能直接按 labels 索引
    for lab in (1, 2, 3):
        assert int((labels == lab).sum()) == int(sizes[lab])


def test_label_components_diagonal_is_one_component_8_neighbour():
    mask = np.zeros((6, 6), bool)
    mask[1, 1] = mask[2, 2] = mask[3, 3] = True
    labels, sizes = io.label_components(mask)
    assert int((labels > 0).sum()) == 3
    assert int(sizes[1]) == 3


def test_label_components_empty_and_full():
    labels, sizes = io.label_components(np.zeros((4, 4), bool))
    assert labels.sum() == 0 and len(sizes) == 1
    labels, sizes = io.label_components(np.ones((4, 4), bool))
    assert int(sizes[1]) == 16


def test_opening_removes_isolated_pixel():
    mask = np.zeros((9, 9), bool)
    mask[1:8, 1:8] = True
    mask[4, 4] = True
    noisy = mask.copy()
    noisy[0, 0] = True
    out = io.opening(noisy, 1)
    assert not out[0, 0]
    assert out[4, 4] and out[3, 3]


def test_fill_holes_fills_only_interior():
    ring = np.zeros((9, 9), bool)
    ring[1:8, 1:8] = True
    ring[3:6, 3:6] = False
    filled = io.fill_holes(ring)
    assert int(filled.sum() - ring.sum()) == 9          # 只填 3x3 的孔
    assert not filled[0, 0]                             # 外部背景没被吞
    assert bool((ring & ~filled).sum() == 0)            # 原掩膜必须保留


def test_fill_holes_returns_mask_unchanged_when_no_hole():
    mask = np.zeros((6, 6), bool)
    mask[1:4, 1:4] = True
    assert np.array_equal(io.fill_holes(mask), mask)


def brute_force_distance(mask: np.ndarray) -> np.ndarray:
    """O(n²) 参考实现：每个前景像素到所有背景像素的最小欧氏距离。"""
    ys, xs = np.nonzero(mask)
    bys, bxs = np.nonzero(~mask)
    out = np.zeros(mask.shape, np.float64)
    for y, x in zip(ys, xs):
        d = np.sqrt((bys - y) ** 2 + (bxs - x) ** 2)
        out[y, x] = float(d.min()) if len(d) else 0.0
    return out


def test_distance_transform_matches_hand_computed():
    mask = np.zeros((7, 7), bool)
    mask[3, 3] = True
    dist = io.distance_transform(mask)
    assert dist[3, 3] == pytest.approx(1.0)             # 距最近背景 1
    # 3x3 实心块：中心距背景 2
    block = np.zeros((9, 9), bool)
    block[3:6, 3:6] = True
    d2 = io.distance_transform(block)
    assert d2[4, 4] == pytest.approx(2.0)
    assert d2[3, 4] == pytest.approx(1.0)               # 边上只隔 1


def test_distance_transform_matches_brute_force_on_random_mask():
    rng = np.random.default_rng(7)
    mask = rng.random((24, 24)) > 0.55
    got = io.distance_transform(mask)
    want = brute_force_distance(mask)
    assert np.allclose(got, want, atol=1e-4)


def test_distance_transform_degenerate_inputs():
    assert io.distance_transform(np.zeros((5, 5), bool)).max() == 0.0
    assert io.distance_transform(np.ones((5, 5), bool)).max() == 0.0


def test_pick_seeds_finds_single_peak_of_circle():
    yy, xx = np.mgrid[0:80, 0:80]
    circle = (yy - 40) ** 2 + (xx - 40) ** 2 <= 25 * 25
    dist = io.distance_transform(circle)
    seeds = io.pick_seeds(dist, min_distance=12, abs_min=4.0)
    assert len(seeds) == 1
    assert seeds[0, 0] == pytest.approx(40, abs=1)
    assert seeds[0, 1] == pytest.approx(40, abs=1)
    assert seeds[0, 2] == pytest.approx(25, abs=1.5)


def test_paint_seeds_assigns_nearest():
    """两个竖直排列的种子：上半的像素归 0、下半归 1。"""
    seeds = np.asarray([[10, 10, 5], [50, 10, 5]], np.float32)
    owner = io.paint_seeds((60, 20), seeds)
    assert owner[5, 10] == 0
    assert owner[55, 10] == 1
    assert set(np.unique(owner)) == {0, 1}


def test_shift_does_not_mutate_input():
    arr = np.zeros((4, 4), bool)
    arr[1, 1] = True
    before = arr.copy()
    io.shift(arr, 1, 1)
    assert np.array_equal(arr, before)
