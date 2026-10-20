"""`measure.debag`：去袋前后宽度必须不同，缺袋掩码不许伪造。"""

import numpy as np
import pytest

from measure.debag import debag, measure_region, region_metrics


def scene():
    """底部 100×40 的袋 + 上方 40×40 的簇（簇比袋窄）。

    `cluster` 按模型的常见形态**包含袋面**：这正是"未去袋的宽度量的是袋"的由来。
    """
    h, w = 120, 200
    bag = np.zeros((h, w), bool)
    bag[60:, 30:130] = True
    cluster = np.zeros((h, w), bool)
    cluster[20:60, 60:100] = True
    cluster |= bag
    return cluster, bag


def test_raw_width_is_bag_width_clean_width_is_cluster_width():
    cluster, bag = scene()
    raw = region_metrics(cluster | bag, mask_source="cluster_raw")
    clean = measure_region(cluster, [bag])

    assert raw.cluster_width_px == 100      # 最宽行是袋
    assert clean.metrics.cluster_width_px == 40
    assert clean.metrics.mask_source == "cluster_minus_bag"
    assert clean.metrics.removed_area_ratio > 0.5


def test_clean_metrics_are_geometry_not_bag():
    cluster, bag = scene()
    got = measure_region(cluster, [bag]).metrics
    assert got.area_px == 40 * 40
    assert got.cluster_h_px == 40
    assert got.bbox_px == (60, 20, 100, 60)
    assert got.eq_diameter_px == pytest.approx(2 * np.sqrt(1600 / np.pi))


def test_missing_bag_mask_yields_null_region_not_a_guess():
    cluster, _ = scene()
    got = measure_region(cluster, [])
    assert got.metrics is None
    assert got.mask_source == "cluster_raw"
    assert got.reasons == ("bag_mask_missing",)


def test_bag_covering_whole_cluster_yields_no_region():
    cluster, bag = scene()
    inside = np.zeros_like(cluster)
    inside[70:80, 40:50] = True
    got = measure_region(inside, [bag])
    assert got.metrics is None
    assert "no_region_after_debag" in got.reasons


def test_multiple_bag_masks_are_unioned_not_split_per_bag():
    cluster, bag = scene()
    other = np.zeros_like(bag)
    other[60:, 130:180] = True
    d = debag(cluster, [bag, other])
    assert d.mask.ndim == 2 and d.mask.dtype == bool
    assert int(d.mask.sum()) == 40 * 40  # 只留簇，不按袋拆成两份
    assert not hasattr(d, "per_bag")


def test_mask_source_is_required_and_validated():
    cluster, _ = scene()
    with pytest.raises(ValueError):
        region_metrics(cluster, mask_source="")
    with pytest.raises(ValueError):
        region_metrics(cluster, mask_source="bag_removed_by_magic")


def test_shape_mismatch_is_rejected():
    cluster, bag = scene()
    with pytest.raises(ValueError):
        debag(cluster, [bag[:-1]])


def test_empty_cluster_returns_none():
    cluster = np.zeros((20, 20), bool)
    bag = np.ones((20, 20), bool)
    assert region_metrics(cluster, mask_source="cluster_minus_bag") is None
    assert measure_region(cluster, [bag]).metrics is None
