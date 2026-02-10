"""
CLIP 微调评估与推理脚本
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch
from loguru import logger
from torch.utils.data import DataLoader

from utils.loguru_setting import loguru_setting

from .config import apply_overrides, build_experiment_config, load_yaml_config
from .data import ImageTextPairDataset
from PIL import Image
from .distributed import get_device, init_distributed, is_main_process
from .metrics import accuracy, retrieval_metrics
from .model import CLIPFineTuner
from .report import generate_comparison_report, generate_report
from .visualization import generate_attention_heatmap, plot_training_curves, visualize_tsne


def run_evaluation(
    config_path: str,
    checkpoint_path: Optional[str] = None,
    overrides: Optional[List[str]] = None,
    baseline_report: Optional[str] = None,
) -> Dict[str, float]:
    """执行评估流程，输出检索、分类与复杂度指标。"""
    overrides = overrides or []
    base_config = load_yaml_config(config_path)
    config_dict = apply_overrides(base_config, overrides)
    config = build_experiment_config(config_dict)
    loguru_setting(production=False)

    init_distributed(config.distributed.backend)
    device = get_device()

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

    if checkpoint_path:
        state = torch.load(checkpoint_path, map_location="cpu")
        model.load_state_dict(state["model_state_dict"], strict=False)

    dataset_path = config.data.test_annotations or config.data.val_annotations
    if not dataset_path:
        raise ValueError("评估阶段需要提供 test_annotations 或 val_annotations")
    dataset = ImageTextPairDataset(
        annotations_path=dataset_path,
        image_root=config.data.image_root,
        image_augmentor=None,
        text_augmentor=None,
    )
    loader = DataLoader(
        dataset,
        batch_size=config.eval.batch_size,
        shuffle=False,
        num_workers=config.eval.num_workers,
        collate_fn=lambda batch: batch,
    )

    image_embeddings, text_embeddings, labels = _extract_embeddings(model, loader, device, config)
    similarity = torch.from_numpy(image_embeddings) @ torch.from_numpy(text_embeddings).t()
    metrics = retrieval_metrics(similarity, config.eval.recall_k)

    if config.eval.classification_labels:
        classification_accuracy = _zero_shot_classification(
            model,
            config.eval.classification_labels,
            config.eval.zero_shot_prompts,
            image_embeddings,
            labels,
            device,
        )
        metrics["zero_shot_accuracy"] = classification_accuracy

    complexity = _model_complexity(model, config, device)
    metrics.update(complexity)

    output_dir = Path(config.train.output_dir)
    figures = []
    if is_main_process():
        if labels:
            tsne_labels = labels[: config.eval.tsne_samples]
        else:
            tsne_labels = ["sample"] * min(config.eval.tsne_samples, len(image_embeddings))
        tsne_output = Path(config.visualization.tsne_output_dir)
        tsne_path = visualize_tsne(
            embeddings=image_embeddings[: config.eval.tsne_samples],
            labels=tsne_labels,
            output_html=str(tsne_output / "tsne.html"),
            perplexity=config.visualization.tsne_perplexity,
            random_state=config.visualization.tsne_random_state,
        )
        figures.append(tsne_path)

        attention_root = Path(config.visualization.attention_output_dir)
        attention_paths = _generate_attention_examples(model, dataset, attention_root, config)
        figures.extend(attention_paths)

        train_log = output_dir / "train_metrics.jsonl"
        if train_log.exists():
            curves_path = plot_training_curves(str(train_log), str(output_dir / "training_curves.html"))
            if curves_path:
                figures.append(curves_path)

        report_path = generate_report(str(output_dir), metrics, figures)
        logger.info(f"评估报告已生成: {report_path}")

        if baseline_report:
            baseline_metrics = _load_metrics(baseline_report)
            compare_path = generate_comparison_report(
                str(output_dir), baseline_metrics=baseline_metrics, finetuned_metrics=metrics
            )
            logger.info(f"对比报告已生成: {compare_path}")

    return metrics


def run_inference(config_path: str, checkpoint_path: str, image_path: str, text: str) -> Dict[str, float]:
    """执行单次图文相似度推理。"""
    base_config = load_yaml_config(config_path)
    config = build_experiment_config(base_config)
    loguru_setting(production=False)

    device = get_device()
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
    state = torch.load(checkpoint_path, map_location="cpu")
    model.load_state_dict(state["model_state_dict"], strict=False)

    image = Image.open(image_path).convert("RGB")
    inputs = model.processor(images=image, text=[text], return_tensors="pt", padding=True).to(device)
    with torch.no_grad():
        image_features, text_features, logit_scale = model(
            inputs["pixel_values"], inputs["input_ids"], inputs["attention_mask"]
        )
        score = float((image_features @ text_features.t()).squeeze().item())
    return {"similarity": score, "logit_scale": float(logit_scale.item())}


def _extract_embeddings(model, loader, device, config):
    """批量提取图像与文本嵌入。"""
    model.eval()
    image_features = []
    text_features = []
    labels = []
    with torch.no_grad():
        for batch in loader:
            images = [item["image"] for item in batch]
            texts = [item["text"] for item in batch]
            batch_labels = [item.get("label") for item in batch]
            encoded = model.processor(
                images=images,
                text=texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=config.data.max_text_length,
            ).to(device)
            img_feat, txt_feat, _ = model(
                encoded["pixel_values"], encoded["input_ids"], encoded["attention_mask"]
            )
            image_features.append(img_feat.cpu().numpy())
            text_features.append(txt_feat.cpu().numpy())
            labels.extend(batch_labels)
    return np.concatenate(image_features), np.concatenate(text_features), labels


def _zero_shot_classification(
    model,
    labels: List[str],
    prompts: List[str],
    image_embeddings: np.ndarray,
    image_labels: List[str],
    device,
) -> float:
    """使用文本提示实现 zero-shot 分类评估。"""
    prompt_texts = [prompt.format(label=label) for label in labels for prompt in prompts]
    label_map = []
    for label in labels:
        for _ in prompts:
            label_map.append(label)
    encoded = model.processor(text=prompt_texts, return_tensors="pt", padding=True).to(device)
    with torch.no_grad():
        text_features = model.encode_text(encoded["input_ids"], encoded["attention_mask"]).cpu().numpy()
    text_features = text_features / np.linalg.norm(text_features, axis=1, keepdims=True)
    image_features = image_embeddings / np.linalg.norm(image_embeddings, axis=1, keepdims=True)
    logits = image_features @ text_features.T
    pred_indices = logits.argmax(axis=1)
    predictions = [label_map[idx] for idx in pred_indices]
    if not image_labels:
        return 0.0
    valid_indices = [idx for idx, label in enumerate(image_labels) if label is not None]
    if not valid_indices:
        return 0.0
    valid_preds = [predictions[idx] for idx in valid_indices]
    valid_labels = [image_labels[idx] for idx in valid_indices]
    return accuracy(np.array(valid_preds), np.array(valid_labels))


def _model_complexity(model, config, device) -> Dict[str, float]:
    """统计参数量、FLOPs 与推理速度。"""
    params = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    latency = _measure_latency(model, device, config)
    flops = _estimate_flops(model, device)
    return {
        "params_total": float(params),
        "params_trainable": float(trainable),
        "inference_latency_ms": latency,
        "flops": flops,
    }


def _measure_latency(model, device, config) -> float:
    """测量单次推理延迟。"""
    dummy_image = torch.randn(1, 3, config.augment.image_size, config.augment.image_size, device=device)
    dummy_text = model.processor(text=["test"], return_tensors="pt", padding=True).to(device)
    runs = config.eval.complexity_num_runs
    for _ in range(5):
        model(dummy_image, dummy_text["input_ids"], dummy_text["attention_mask"])
    if device.type == "cuda":
        torch.cuda.synchronize()
    start = time.time()
    for _ in range(runs):
        model(dummy_image, dummy_text["input_ids"], dummy_text["attention_mask"])
    if device.type == "cuda":
        torch.cuda.synchronize()
    return (time.time() - start) * 1000.0 / runs


def _estimate_flops(model, device) -> float:
    """估算模型 FLOPs。"""
    try:
        with torch.profiler.profile(with_flops=True) as prof:
            dummy_image = torch.randn(1, 3, 224, 224, device=device)
            dummy_text = model.processor(text=["test"], return_tensors="pt", padding=True).to(device)
            model(dummy_image, dummy_text["input_ids"], dummy_text["attention_mask"])
        return float(sum(event.flops for event in prof.key_averages()))
    except Exception:
        return 0.0


def _generate_attention_examples(model, dataset, output_dir: Path, config) -> List[str]:
    """生成注意力热图示例。"""
    outputs = []
    num_examples = min(5, len(dataset))
    for idx in range(num_examples):
        sample = dataset[idx]
        path = output_dir / "attention" / f"attention_{idx}.png"
        outputs.append(
            generate_attention_heatmap(
                model=model,
                processor=model.processor,
                image=sample["image"],
                output_path=str(path),
            )
        )
    return outputs


def _load_metrics(path: str) -> Dict[str, float]:
    """加载评估报告中的指标。"""
    report_path = Path(path)
    if report_path.suffix == ".json":
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data.get("metrics", {})
    if report_path.suffix == ".html":
        return {}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="CLIP Fine-tuning Evaluation")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--checkpoint", default=None, help="模型 checkpoint 路径")
    parser.add_argument("--override", action="append", default=[], help="覆盖配置项")
    parser.add_argument("--baseline-report", default=None, help="基线评估报告路径，用于对比")
    parser.add_argument("--mode", choices=["evaluate", "infer"], default="evaluate")
    parser.add_argument("--image", help="推理模式下的图像路径")
    parser.add_argument("--text", help="推理模式下的文本输入")
    args = parser.parse_args()

    if args.mode == "infer":
        if not args.image or not args.text or not args.checkpoint:
            raise ValueError("推理模式需要 --image --text --checkpoint")
        result = run_inference(args.config, args.checkpoint, args.image, args.text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        run_evaluation(args.config, args.checkpoint, args.override, args.baseline_report)


if __name__ == "__main__":
    main()
