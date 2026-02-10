"""
评估指标模块

提供检索与分类指标计算。
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import torch


def retrieval_metrics(similarity: torch.Tensor, k_values: List[int]) -> Dict[str, float]:
    """计算图像到文本与文本到图像检索指标。"""
    sim = similarity.detach().cpu()
    image_to_text = _recall_at_k(sim, k_values)
    text_to_image = _recall_at_k(sim.t(), k_values)
    metrics = {}
    for k in k_values:
        metrics[f"i2t_R@{k}"] = image_to_text[k]
        metrics[f"t2i_R@{k}"] = text_to_image[k]
    metrics["i2t_mAP"] = _mean_average_precision(sim)
    metrics["t2i_mAP"] = _mean_average_precision(sim.t())
    return metrics


def _recall_at_k(similarity: torch.Tensor, k_values: List[int]) -> Dict[int, float]:
    """计算 Recall@K。"""
    max_k = max(k_values)
    indices = torch.topk(similarity, k=max_k, dim=-1).indices
    targets = torch.arange(similarity.size(0)).unsqueeze(1)
    recalls = {}
    for k in k_values:
        hit = (indices[:, :k] == targets).any(dim=1).float().mean().item()
        recalls[k] = hit
    return recalls


def _mean_average_precision(similarity: torch.Tensor) -> float:
    """计算 mean Average Precision。"""
    ranks = _rank_of_correct(similarity)
    ap = 1.0 / ranks
    return ap.mean().item()


def _rank_of_correct(similarity: torch.Tensor) -> torch.Tensor:
    """返回正确匹配的排名。"""
    sorted_indices = torch.argsort(similarity, dim=-1, descending=True)
    targets = torch.arange(similarity.size(0)).unsqueeze(1)
    matches = sorted_indices == targets
    ranks = matches.float().argmax(dim=1) + 1
    return ranks


def accuracy(predictions: np.ndarray, labels: np.ndarray) -> float:
    """计算分类准确率。"""
    return float((predictions == labels).mean())
