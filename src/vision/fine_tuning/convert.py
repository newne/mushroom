"""
模型转换工具

支持将微调权重合并并导出到 HuggingFace 格式。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

from .config import load_experiment_config
from .lora import merge_lora
from .model import CLIPFineTuner


def convert_checkpoint(config_path: str, checkpoint_path: str, output_dir: str, merge: bool = True) -> str:
    """将微调权重导出为 HuggingFace 兼容格式。"""
    config = load_experiment_config(config_path)
    model = CLIPFineTuner(
        model_name_or_path=config.model.model_name_or_path,
        cache_dir=config.model.cache_dir,
        use_lora=config.model.use_lora,
        lora_r=config.model.lora_r,
        lora_alpha=config.model.lora_alpha,
        lora_dropout=config.model.lora_dropout,
        lora_target_modules=config.model.lora_target_modules,
        freeze_vision=config.model.freeze_vision,
        freeze_text=config.model.freeze_text,
        freeze_projection=config.model.freeze_projection,
        logit_scale_init=config.model.logit_scale_init,
    )
    state = torch.load(checkpoint_path, map_location="cpu")
    model.load_state_dict(state["model_state_dict"], strict=False)
    if merge:
        merge_lora(model)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    model.clip.save_pretrained(output_path)
    model.processor.save_pretrained(output_path)
    return str(output_path)


def main() -> None:
    """转换脚本 CLI 入口。"""
    parser = argparse.ArgumentParser(description="CLIP 微调权重转换")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--checkpoint", required=True, help="微调 checkpoint 路径")
    parser.add_argument("--output-dir", required=True, help="导出目录")
    parser.add_argument("--no-merge", action="store_true", help="不合并 LoRA 权重")
    args = parser.parse_args()

    convert_checkpoint(args.config, args.checkpoint, args.output_dir, merge=not args.no_merge)


if __name__ == "__main__":
    main()
