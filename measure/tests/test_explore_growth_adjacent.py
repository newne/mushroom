"""`tools/explore_growth_adjacent.py` 的自检：只用合成图，不读生产帧。

钉住三件最容易搞反/搞错的事：
1. 相位相关的**符号口径**（跨天假位移就是从这里来的）；
2. 簇顶包络只取**与底部连通**的亮实体，背景亮弧不算；
3. 成对增量的**对齐方向**（对齐后同一画面应当零增量）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "explore_growth_adjacent.py"
_spec = importlib.util.spec_from_file_location("explore_growth_adjacent", _TOOL)
assert _spec and _spec.loader
eg = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eg          # dataclass 需要按 __module__ 反查
_spec.loader.exec_module(eg)

CFG = eg.Config()


def _synth_cluster(top_y: int, *, arc: bool = True) -> np.ndarray:
    """400×600：背景 60，菌簇 200（底部连通），可选的背景亮弧（不连通）。"""
    img = np.full((400, 600), 60.0, np.float32)
    img[top_y:, 200:400] = 200.0
    if arc:
        img[100:112, 100:500] = 190.0
    return img


def test_phase_shift_sign() -> None:
    """`mov(y,x) = ref(y−5, x−7)` ⇒ 返回 (dy,dx) ≈ (−5,−7)（ref = mov 平移 +5,+7）。"""
    rng = np.random.default_rng(0)
    coarse = rng.normal(100, 25, (380, 580)).astype(np.float32)
    ref = np.pad(coarse, ((10, 10), (10, 10)), mode="edge")
    # 低频化，贴近真实背板纹理
    k = np.ones((5, 5), np.float32) / 25.0
    from numpy.lib.stride_tricks import sliding_window_view
    ref = sliding_window_view(ref, (5, 5)).mean(axis=(-1, -2))
    ref = np.pad(ref, ((2, 2), (2, 2)), mode="edge")
    mov = np.full_like(ref, float(np.median(ref)))
    mov[5:, 7:] = ref[:-5, :-7]

    dy, dx, peak = eg.phase_shift(ref, mov, CFG)
    assert peak > 10
    assert dy == pytest.approx(-5.0, abs=0.8)
    assert dx == pytest.approx(-7.0, abs=0.8)


def test_cluster_top_ignores_disconnected_arc() -> None:
    top, n_valid, _thr, _span = eg.cluster_top(_synth_cluster(250), CFG, CFG.k)
    assert n_valid == 200
    assert np.nanmedian(top) == pytest.approx(250.0, abs=2.0)
    assert np.isfinite(top[:190]).sum() == 0, "背景亮弧不连通，不该被当成簇顶"


def test_pair_delta_is_zero_after_alignment() -> None:
    a = _synth_cluster(250)
    b = _synth_cluster(246)              # 菌簇顶比 A 高 4 px
    top_a, _, _, _ = eg.cluster_top(a, CFG, CFG.k)
    top_b, _, _, _ = eg.cluster_top(b, CFG, CFG.k)

    raw, _ = eg.measure_delta(top_a, top_b, 0.0, 0.0)
    assert raw == pytest.approx(4.0, abs=1.0)

    # B 相对 A 的平移约定：ref = mov 平移 (dy,dx)；这里没有平移，给 4 px 的
    # "机位下移"补偿后应当回到 0（同时验证 t_B_aligned = t_B(x−dx) + dy）。
    aligned, _ = eg.measure_delta(top_a, top_b, 4.0, 0.0)
    assert aligned == pytest.approx(0.0, abs=1.0)


def test_measure_delta_does_not_count_interpolated_gap() -> None:
    """B 的无效段不能被 `np.interp` 补值后当成有效列参与中位数。"""
    ta = np.full(100, 100.0, np.float32)
    tb = np.full(100, 100.0, np.float32)
    tb[40:60] = np.nan
    delta, diag = eg.measure_delta(ta, tb, 0.0, 0.0)
    assert delta == pytest.approx(0.0, abs=1e-6)
    assert diag["n"] == 80, diag


def test_smooth_rows_keeps_shape_and_edges() -> None:
    img = np.zeros((30, 4), np.float32)
    img[10:, :] = 100.0
    sm = eg._smooth_rows(img, 2)
    assert sm.shape == img.shape
    assert np.isfinite(sm).all()
    assert sm[2, 0] == pytest.approx(0.0, abs=1e-6)
    assert sm[-3, 0] == pytest.approx(100.0, abs=1e-6)
