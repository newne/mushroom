"""
CLIP 微调训练脚本
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import torch
from loguru import logger
from torch.cuda.amp import GradScaler, autocast
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, DistributedSampler

from utils.loguru_setting import loguru_setting

from .config import apply_overrides, build_experiment_config, load_yaml_config
from .data import ImageAugmentor, ImageTextPairDataset, TextAugmentor, load_synonym_map
from .distributed import cleanup, get_device, init_distributed, is_main_process
from .losses import InfoNCELoss
from .model import CLIPFineTuner
from .optim import build_optimizer, build_warmup_cosine_scheduler


def run_training(config_path: str, overrides: Optional[List[str]] = None) -> Dict[str, float]:
    """执行微调训练流程，返回最佳验证集损失。"""
    overrides = overrides or []
    base_config = load_yaml_config(config_path)
    config_dict = apply_overrides(base_config, overrides)
    config = build_experiment_config(config_dict)
    loguru_setting(production=False)

    rank, world_size = init_distributed(config.distributed.backend)
    device = get_device()
    torch.manual_seed(config.train.seed + rank)

    output_dir = Path(config.train.output_dir)
    if is_main_process():
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

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
    ).to(device)

    if world_size > 1:
        model = DDP(model, device_ids=[device.index] if device.type == "cuda" else None)

    synonym_map = load_synonym_map(config.augment.synonym_map_path)
    image_augmentor = ImageAugmentor(
        image_size=config.augment.image_size,
        random_crop_scale=config.augment.random_crop_scale,
        color_jitter=config.augment.color_jitter,
        hflip_prob=config.augment.hflip_prob,
    )
    text_augmentor = TextAugmentor(
        synonym_prob=config.augment.text_synonym_prob,
        max_replacements=config.augment.text_max_replacements,
        synonym_map=synonym_map,
    )
    train_dataset = ImageTextPairDataset(
        annotations_path=config.data.train_annotations,
        image_root=config.data.image_root,
        image_augmentor=image_augmentor,
        text_augmentor=text_augmentor,
        max_samples=config.train.max_train_samples,
    )
    val_dataset = None
    if config.data.val_annotations:
        val_dataset = ImageTextPairDataset(
            annotations_path=config.data.val_annotations,
            image_root=config.data.image_root,
            image_augmentor=None,
            text_augmentor=None,
            max_samples=config.train.max_val_samples,
        )

    train_sampler = DistributedSampler(train_dataset, shuffle=True) if world_size > 1 else None
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.data.batch_size,
        sampler=train_sampler,
        shuffle=train_sampler is None,
        num_workers=config.data.num_workers,
        collate_fn=lambda batch: batch,
        drop_last=True,
    )
    val_loader = None
    if val_dataset:
        val_sampler = DistributedSampler(val_dataset, shuffle=False) if world_size > 1 else None
        val_loader = DataLoader(
            val_dataset,
            batch_size=config.eval.batch_size,
            sampler=val_sampler,
            shuffle=False,
            num_workers=config.eval.num_workers,
            collate_fn=lambda batch: batch,
        )

    optimizer = build_optimizer(
        params=[p for p in model.parameters() if p.requires_grad],
        lr=config.optim.lr,
        weight_decay=config.optim.weight_decay,
        betas=config.optim.betas,
    )
    total_steps = config.scheduler.total_steps
    if total_steps <= 0:
        total_steps = (len(train_loader) * config.train.epochs) // config.train.grad_accum_steps
    scheduler = build_warmup_cosine_scheduler(
        optimizer,
        warmup_steps=config.scheduler.warmup_steps,
        total_steps=total_steps,
        min_lr=config.scheduler.cosine_min_lr,
    )

    loss_fn = InfoNCELoss()
    scaler = GradScaler(enabled=config.train.amp and device.type == "cuda")

    start_epoch = 0
    global_step = 0
    best_val_loss = float("inf")
    patience = 0

    if config.train.resume_from:
        checkpoint = torch.load(config.train.resume_from, map_location="cpu")
        model.load_state_dict(checkpoint["model_state_dict"], strict=False)
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
        scaler.load_state_dict(checkpoint.get("scaler_state_dict", {}))
        start_epoch = checkpoint.get("epoch", 0) + 1
        global_step = checkpoint.get("step", 0)
        best_val_loss = checkpoint.get("best_val_loss", best_val_loss)

    train_log_path = output_dir / "train_metrics.jsonl"

    for epoch in range(start_epoch, config.train.epochs):
        if train_sampler:
            train_sampler.set_epoch(epoch)
        model.train()
        running_loss = 0.0
        for step, batch in enumerate(train_loader):
            images = [item["image"] for item in batch]
            texts = [item["text"] for item in batch]
            inputs = model.module.processor if isinstance(model, DDP) else model.processor
            encoded = inputs(
                images=images,
                text=texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=config.data.max_text_length,
            ).to(device)
            with autocast(enabled=config.train.amp and device.type == "cuda"):
                image_features, text_features, logit_scale = model(
                    encoded["pixel_values"], encoded["input_ids"], encoded["attention_mask"]
                )
                loss = loss_fn(image_features, text_features, logit_scale)
                loss = loss / config.train.grad_accum_steps
            scaler.scale(loss).backward()
            if (step + 1) % config.train.grad_accum_steps == 0:
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                scheduler.step()
                global_step += 1
            running_loss += loss.item() * config.train.grad_accum_steps

            if is_main_process() and global_step % config.train.log_interval == 0:
                log_record = {
                    "epoch": epoch,
                    "step": global_step,
                    "loss": running_loss / (step + 1),
                    "lr": scheduler.get_last_lr()[0],
                }
                _append_jsonl(train_log_path, log_record)
                logger.info(
                    f"[TRAIN] epoch={epoch} step={global_step} loss={log_record['loss']:.6f} lr={log_record['lr']:.6e}"
                )

        if val_loader and (epoch + 1) % config.train.eval_interval == 0:
            val_loss = _evaluate_loss(model, val_loader, loss_fn, device, config)
            if is_main_process():
                logger.info(f"[VAL] epoch={epoch} loss={val_loss:.6f}")
                _append_jsonl(train_log_path, {"epoch": epoch, "val_loss": val_loss})
                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    patience = 0
                    _save_checkpoint(
                        output_dir / "checkpoints" / "best.pt",
                        model,
                        optimizer,
                        scheduler,
                        scaler,
                        epoch,
                        global_step,
                        best_val_loss,
                    )
                else:
                    patience += 1
                    if patience >= config.train.early_stopping_patience:
                        logger.warning("触发早停机制")
                        break

        if is_main_process() and (epoch + 1) % config.train.save_every == 0:
            _save_checkpoint(
                output_dir / "checkpoints" / f"epoch_{epoch}.pt",
                model,
                optimizer,
                scheduler,
                scaler,
                epoch,
                global_step,
                best_val_loss,
            )
            _save_checkpoint(
                output_dir / "checkpoints" / "last.pt",
                model,
                optimizer,
                scheduler,
                scaler,
                epoch,
                global_step,
                best_val_loss,
            )

    cleanup()
    return {"best_val_loss": best_val_loss}


def _evaluate_loss(model, loader, loss_fn, device, config) -> float:
    """在验证集上计算 InfoNCE 损失。"""
    model.eval()
    losses = []
    with torch.no_grad():
        for batch in loader:
            images = [item["image"] for item in batch]
            texts = [item["text"] for item in batch]
            inputs = model.module.processor if isinstance(model, DDP) else model.processor
            encoded = inputs(
                images=images,
                text=texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=config.data.max_text_length,
            ).to(device)
            image_features, text_features, logit_scale = model(
                encoded["pixel_values"], encoded["input_ids"], encoded["attention_mask"]
            )
            loss = loss_fn(image_features, text_features, logit_scale)
            losses.append(loss.item())
    model.train()
    return float(sum(losses) / max(1, len(losses)))


def _save_checkpoint(
    path: Path,
    model,
    optimizer,
    scheduler,
    scaler,
    epoch: int,
    step: int,
    best_val_loss: float,
) -> None:
    """保存训练 checkpoint，支持断点续训。"""
    state = {
        "model_state_dict": model.module.state_dict() if isinstance(model, DDP) else model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict(),
        "scaler_state_dict": scaler.state_dict(),
        "epoch": epoch,
        "step": step,
        "best_val_loss": best_val_loss,
    }
    torch.save(state, path)


def _append_jsonl(path: Path, record: Dict) -> None:
    """追加写入 JSONL 训练日志。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    """训练脚本 CLI 入口。"""
    parser = argparse.ArgumentParser(description="CLIP Fine-tuning Training")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--override", action="append", default=[], help="覆盖配置项，例如 train.epochs=5")
    args = parser.parse_args()
    run_training(args.config, args.override)


if __name__ == "__main__":
    main()
