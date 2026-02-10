"""
CLIP 微调模型封装

提供模型加载、冻结策略与 LoRA 注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import torch
from loguru import logger
from torch import nn
from transformers import CLIPModel, CLIPProcessor

from .lora import apply_lora


class CLIPFineTuner(nn.Module):
    """CLIP 微调封装，支持冻结与 LoRA。"""
    def __init__(
        self,
        model_name_or_path: str,
        cache_dir: Optional[str],
        use_lora: bool,
        lora_r: int,
        lora_alpha: int,
        lora_dropout: float,
        lora_target_modules,
        freeze_vision: bool,
        freeze_text: bool,
        freeze_projection: bool,
        logit_scale_init: Optional[float],
    ) -> None:
        super().__init__()
        model_path = resolve_clip_path(model_name_or_path)
        self.processor = CLIPProcessor.from_pretrained(model_path, cache_dir=cache_dir)
        self.clip = CLIPModel.from_pretrained(model_path, cache_dir=cache_dir)
        if logit_scale_init is not None:
            self.clip.logit_scale.data.fill_(logit_scale_init)
        if freeze_vision:
            self._freeze_module(self.clip.vision_model)
        if freeze_text:
            self._freeze_module(self.clip.text_model)
        if freeze_projection:
            self._freeze_module(self.clip.visual_projection)
            self._freeze_module(self.clip.text_projection)
        if use_lora:
            apply_lora(self.clip, lora_target_modules, r=lora_r, alpha=lora_alpha, dropout=lora_dropout)

    def encode_image(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """生成归一化图像特征。"""
        features = self.clip.get_image_features(pixel_values=pixel_values)
        return features / features.norm(dim=-1, keepdim=True)

    def encode_text(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        """生成归一化文本特征。"""
        features = self.clip.get_text_features(input_ids=input_ids, attention_mask=attention_mask)
        return features / features.norm(dim=-1, keepdim=True)

    def forward(
        self,
        pixel_values: torch.Tensor,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """前向计算并返回特征与 logit_scale。"""
        image_features = self.encode_image(pixel_values)
        text_features = self.encode_text(input_ids, attention_mask)
        logit_scale = self.clip.logit_scale.exp()
        return image_features, text_features, logit_scale

    @staticmethod
    def _freeze_module(module: nn.Module) -> None:
        """冻结指定模块参数。"""
        for param in module.parameters():
            param.requires_grad = False


def resolve_clip_path(model_name_or_path: str) -> str:
    """解析本地或远程 CLIP 模型路径。"""
    container_model_path = Path("/app/models/clip-vit-base-patch32")
    local_model_path = Path(__file__).parent.parent.parent.parent / "models" / "clip-vit-base-patch32"
    if model_name_or_path != "openai/clip-vit-base-patch32":
        return model_name_or_path
    if container_model_path.exists():
        logger.debug(f"使用容器模型路径: {container_model_path}")
        return str(container_model_path)
    if local_model_path.exists():
        logger.debug(f"使用本地模型路径: {local_model_path}")
        return str(local_model_path)
    logger.debug("使用默认 HuggingFace CLIP 模型")
    return model_name_or_path
