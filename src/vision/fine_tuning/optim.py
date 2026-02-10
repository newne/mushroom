"""
优化器与学习率调度器模块
"""

from __future__ import annotations

import math
from typing import Iterable

import torch
from torch.optim import Optimizer


def build_optimizer(
    params: Iterable[torch.nn.Parameter],
    lr: float,
    weight_decay: float,
    betas,
) -> Optimizer:
    """构建 AdamW 优化器。"""
    return torch.optim.AdamW(params, lr=lr, weight_decay=weight_decay, betas=tuple(betas))


def build_warmup_cosine_scheduler(
    optimizer: Optimizer,
    warmup_steps: int,
    total_steps: int,
    min_lr: float,
):
    """构建 Warmup + Cosine 学习率调度器。"""
    def lr_lambda(step: int):
        if step < warmup_steps:
            return float(step) / float(max(1, warmup_steps))
        progress = (step - warmup_steps) / float(max(1, total_steps - warmup_steps))
        cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
        return max(min_lr / optimizer.defaults["lr"], cosine)

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
