"""生长时间序列建模：线性、指数、Logistic。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal

import numpy as np

ModelType = Literal["linear", "exponential", "logistic"]


@dataclass(frozen=True)
class GrowthFitResult:
    model_type: ModelType
    params: Dict[str, float]
    mse: float


def _mse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean((y_true - y_pred) ** 2))


def fit_linear(t: Iterable[float], y: Iterable[float]) -> GrowthFitResult:
    t_arr = np.asarray(list(t), dtype=np.float64)
    y_arr = np.asarray(list(y), dtype=np.float64)
    if len(t_arr) < 2:
        return GrowthFitResult("linear", {"a": 0.0, "b": float(y_arr.mean())}, 0.0)

    a, b = np.polyfit(t_arr, y_arr, deg=1)
    pred = a * t_arr + b
    return GrowthFitResult("linear", {"a": float(a), "b": float(b)}, _mse(y_arr, pred))


def fit_exponential(t: Iterable[float], y: Iterable[float]) -> GrowthFitResult:
    """拟合 y = a * exp(b * t)。"""
    t_arr = np.asarray(list(t), dtype=np.float64)
    y_arr = np.asarray(list(y), dtype=np.float64)

    y_shift = np.maximum(y_arr, 1e-6)
    b, log_a = np.polyfit(t_arr, np.log(y_shift), deg=1)
    a = float(np.exp(log_a))
    pred = a * np.exp(b * t_arr)
    return GrowthFitResult(
        "exponential",
        {"a": a, "b": float(b)},
        _mse(y_arr, pred),
    )


def fit_logistic_grid_search(t: Iterable[float], y: Iterable[float]) -> GrowthFitResult:
    """简单网格搜索拟合 y = K / (1 + exp(-r*(t-t0)))。"""
    t_arr = np.asarray(list(t), dtype=np.float64)
    y_arr = np.asarray(list(y), dtype=np.float64)

    k_min = max(float(y_arr.max()), 1e-6)
    k_max = max(k_min * 2.0, k_min + 1.0)
    r_candidates = np.linspace(0.01, 1.2, 40)
    k_candidates = np.linspace(k_min, k_max, 30)
    t0_candidates = np.linspace(float(t_arr.min()), float(t_arr.max()), 30)

    best = GrowthFitResult("logistic", {"K": k_min, "r": 0.1, "t0": 0.0}, float("inf"))

    for k in k_candidates:
        for r in r_candidates:
            for t0 in t0_candidates:
                pred = k / (1.0 + np.exp(-r * (t_arr - t0)))
                mse = _mse(y_arr, pred)
                if mse < best.mse:
                    best = GrowthFitResult(
                        "logistic",
                        {"K": float(k), "r": float(r), "t0": float(t0)},
                        float(mse),
                    )

    return best


def auto_select_growth_model(t: Iterable[float], y: Iterable[float]) -> GrowthFitResult:
    """自动选择误差最小的生长模型。"""
    candidates = [
        fit_linear(t, y),
        fit_exponential(t, y),
        fit_logistic_grid_search(t, y),
    ]
    return min(candidates, key=lambda item: item.mse)
