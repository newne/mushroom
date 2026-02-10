"""
LoRA 低秩适配模块

提供线性层 LoRA 注入与合并功能。
"""

from __future__ import annotations

import math
from typing import Iterable, List

import torch
from torch import nn


class LoRALinear(nn.Module):
    """线性层 LoRA 包装。"""
    def __init__(
        self,
        base_layer: nn.Linear,
        r: int,
        alpha: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.base_layer = base_layer
        self.r = r
        self.alpha = alpha
        self.scaling = alpha / r if r > 0 else 1.0
        self.dropout = nn.Dropout(dropout)
        self.lora_A = nn.Parameter(torch.zeros((r, base_layer.in_features)))
        self.lora_B = nn.Parameter(torch.zeros((base_layer.out_features, r)))
        self.reset_parameters()

    def reset_parameters(self) -> None:
        """初始化 LoRA 参数。"""
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        nn.init.zeros_(self.lora_B)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """执行 LoRA 前向计算。"""
        result = self.base_layer(x)
        if self.r == 0:
            return result
        lora_out = self.dropout(x) @ self.lora_A.t()
        lora_out = lora_out @ self.lora_B.t()
        return result + lora_out * self.scaling

    def merge(self) -> None:
        """合并 LoRA 权重到原始线性层。"""
        if self.r == 0:
            return
        delta = (self.lora_B @ self.lora_A) * self.scaling
        self.base_layer.weight.data += delta.to(self.base_layer.weight.data.dtype)
        self.lora_A.data.zero_()
        self.lora_B.data.zero_()


def apply_lora(
    module: nn.Module,
    target_modules: List[str],
    r: int,
    alpha: int,
    dropout: float,
) -> nn.Module:
    """在目标模块上注入 LoRA 层。"""
    for name, child in list(module.named_modules()):
        if not isinstance(child, nn.Linear):
            continue
        if target_modules and not any(target in name for target in target_modules):
            continue
        parent, attr_name = _locate_parent(module, name)
        if parent is None:
            continue
        setattr(parent, attr_name, LoRALinear(child, r=r, alpha=alpha, dropout=dropout))
    return module


def merge_lora(module: nn.Module) -> None:
    """合并模块内所有 LoRA 权重。"""
    for child in module.modules():
        if isinstance(child, LoRALinear):
            child.merge()


def lora_parameters(module: nn.Module) -> Iterable[nn.Parameter]:
    """返回模块内 LoRA 参数迭代器。"""
    for child in module.modules():
        if isinstance(child, LoRALinear):
            yield from child.parameters()


def _locate_parent(root: nn.Module, module_name: str):
    """定位子模块的父模块与属性名。"""
    parts = module_name.split(".")
    parent = root
    for part in parts[:-1]:
        if not hasattr(parent, part):
            return None, ""
        parent = getattr(parent, part)
    return parent, parts[-1]
