"""
可视化工具

提供 t-SNE 与注意力热图生成。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import torch
from PIL import Image
from sklearn.manifold import TSNE


def visualize_tsne(
    embeddings: np.ndarray,
    labels: List[str],
    output_html: str,
    perplexity: int,
    random_state: int,
) -> str:
    """生成 t-SNE 二维可视化并输出 HTML。"""
    tsne = TSNE(n_components=2, perplexity=perplexity, random_state=random_state, init="random")
    reduced = tsne.fit_transform(embeddings)
    fig = px.scatter(x=reduced[:, 0], y=reduced[:, 1], color=labels, title="t-SNE Embeddings")
    output_path = Path(output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path))
    return str(output_path)


def generate_attention_heatmap(
    model,
    processor,
    image: Image.Image,
    output_path: str,
    layer: int = -1,
) -> str:
    """生成 CLIP 视觉注意力热图并写入图像文件。"""
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model.clip.vision_model(
            pixel_values=inputs["pixel_values"],
            output_attentions=True,
            return_dict=True,
        )
    attentions = outputs.attentions[layer]
    attention = attentions.mean(dim=1)[0]
    grid_size = int(math.sqrt(attention.size(-1)))
    cls_attention = attention[0, 1:].reshape(grid_size, grid_size)
    cls_attention = cls_attention / cls_attention.max()
    heatmap = _colorize_heatmap(cls_attention.cpu().numpy())
    heatmap = heatmap.resize(image.size, Image.BICUBIC)
    blended = Image.blend(image.convert("RGB"), heatmap, alpha=0.5)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blended.save(output_path)
    return str(output_path)


def _colorize_heatmap(heatmap: np.ndarray) -> Image.Image:
    """将注意力权重矩阵转为可视化热图。"""
    heatmap = np.clip(heatmap, 0, 1)
    colored = np.zeros((*heatmap.shape, 3), dtype=np.uint8)
    colored[..., 0] = (255 * heatmap).astype(np.uint8)
    colored[..., 1] = (255 * (1 - heatmap)).astype(np.uint8)
    colored[..., 2] = 128
    return Image.fromarray(colored)


def plot_training_curves(log_path: str, output_html: str) -> str:
    """根据训练日志输出 loss 与学习率曲线 HTML。"""
    records = []
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    if not records:
        return ""
    steps = [item.get("step") for item in records if "loss" in item]
    losses = [item.get("loss") for item in records if "loss" in item]
    lrs = [item.get("lr") for item in records if "lr" in item]
    val_losses = [item.get("val_loss") for item in records if "val_loss" in item]

    fig = go.Figure()
    if steps and losses:
        fig.add_trace(go.Scatter(x=steps, y=losses, mode="lines", name="train_loss"))
    if steps and lrs:
        fig.add_trace(go.Scatter(x=steps[: len(lrs)], y=lrs, mode="lines", name="lr", yaxis="y2"))
    if val_losses:
        fig.add_trace(go.Scatter(x=list(range(len(val_losses))), y=val_losses, mode="lines", name="val_loss"))
    fig.update_layout(title="Training Curves", yaxis2=dict(overlaying="y", side="right"))
    output_path = Path(output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path))
    return str(output_path)
