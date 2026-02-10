"""
损失函数模块

实现 InfoNCE 对比学习损失的可配置版本。
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


def info_nce_loss(
    image_features: torch.Tensor,
    text_features: torch.Tensor,
    logit_scale: torch.Tensor,
    label_smoothing: float = 0.0,
) -> torch.Tensor:
    """计算图文对比学习 InfoNCE 损失。"""
    batch_size = image_features.size(0)
    logits = logit_scale * image_features @ text_features.t()
    labels = torch.arange(batch_size, device=logits.device)
    loss_i = F.cross_entropy(logits, labels, label_smoothing=label_smoothing)
    loss_t = F.cross_entropy(logits.t(), labels, label_smoothing=label_smoothing)
    return (loss_i + loss_t) * 0.5


class InfoNCELoss(nn.Module):
    """InfoNCE 损失封装。"""
    def __init__(self, label_smoothing: float = 0.0) -> None:
        super().__init__()
        self.label_smoothing = label_smoothing

    def forward(
        self,
        image_features: torch.Tensor,
        text_features: torch.Tensor,
        logit_scale: torch.Tensor,
    ) -> torch.Tensor:
        """前向计算损失。"""
        return info_nce_loss(
            image_features=image_features,
            text_features=text_features,
            logit_scale=logit_scale,
            label_smoothing=self.label_smoothing,
        )
