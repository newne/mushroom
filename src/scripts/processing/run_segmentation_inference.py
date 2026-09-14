#!/usr/bin/env python3
"""基于 EUPE 权重与当前分割算法的蘑菇分割推理脚本。

说明：
1. 优先读取 models/EUPE-ViT-B.pt 作为视觉主干权重来源。
2. 当前仓库未包含 EUPE 分割头权重时，使用 EUPE patch-embed 特征 + 启发式规则输出语义 logits。
3. 通过 segmentation.pipeline 统一完成滑窗推理与尺寸计算。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

# Bootstrap sys.path to ensure importing from src works in standalone execution.
CURRENT_FILE = Path(__file__).resolve()
SRC_DIR = CURRENT_FILE.parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@dataclass(frozen=True)
class ImageResult:
    image_name: str
    bag_area_px: int
    stem_area_px: int
    cap_area_px: int
    bag_bbox_xyxy: List[int]
    mm_per_px: float | None
    stem_length_mm: float | None
    stem_diameter_mm: float | None
    cap_diameter_mm: float | None
    cap_area_mm2: float | None


class EUPEProxyPredictor:
    """使用 EUPE patch-embed 权重构造轻量 logits 预测器。"""

    def __init__(self, checkpoint_path: Path, device: str = "cpu"):
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"模型不存在: {checkpoint_path}")

        state = torch.load(str(checkpoint_path), map_location="cpu")
        if not isinstance(state, dict) or "patch_embed.proj.weight" not in state:
            raise ValueError(
                "checkpoint 不包含 patch_embed.proj.weight，无法执行当前算法"
            )

        patch_w = state["patch_embed.proj.weight"].float()  # [D,3,K,K]
        patch_b = state.get("patch_embed.proj.bias", None)
        patch_b = patch_b.float() if isinstance(patch_b, torch.Tensor) else None

        norms = patch_w.flatten(1).norm(dim=1)
        topk = int(min(3, patch_w.shape[0]))
        top_idx = torch.topk(norms, k=topk).indices
        self.weight = patch_w[top_idx].contiguous()
        self.bias = patch_b[top_idx].contiguous() if patch_b is not None else None

        self.kernel = int(self.weight.shape[-1])
        self.stride = self.kernel
        self.device = torch.device(device)
        self.weight = self.weight.to(self.device)
        self.bias = self.bias.to(self.device) if self.bias is not None else None

    @staticmethod
    def _norm_map(x: np.ndarray) -> np.ndarray:
        lo, hi = float(x.min()), float(x.max())
        if hi - lo < 1e-6:
            return np.zeros_like(x, dtype=np.float32)
        return ((x - lo) / (hi - lo)).astype(np.float32)

    @staticmethod
    def _largest_component(mask: np.ndarray) -> np.ndarray:
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8
        )
        if num_labels <= 1:
            return mask.astype(np.uint8)
        areas = stats[1:, cv2.CC_STAT_AREA]
        max_id = int(np.argmax(areas)) + 1
        return (labels == max_id).astype(np.uint8)

    @staticmethod
    def _fill_holes(mask: np.ndarray) -> np.ndarray:
        """Fill interior holes to keep large structural regions intact."""
        if mask.size == 0:
            return mask.astype(np.uint8)

        flood = mask.astype(np.uint8).copy()
        h, w = flood.shape
        floodfill_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
        cv2.floodFill(flood, floodfill_mask, (0, 0), 1)
        holes = 1 - flood
        return np.maximum(mask.astype(np.uint8), holes.astype(np.uint8))

    @staticmethod
    def _select_bag_component(mask: np.ndarray) -> np.ndarray:
        """Select the most plausible bag component by area and lower-position bias."""
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8
        )
        if num_labels <= 1:
            return mask.astype(np.uint8)

        h = mask.shape[0]
        best_score = -1.0
        best_id = 0
        for label_id in range(1, num_labels):
            area = float(stats[label_id, cv2.CC_STAT_AREA])
            cy = float(centroids[label_id][1]) / max(h, 1)
            score = area * (0.8 + cy)
            if score > best_score:
                best_score = score
                best_id = label_id

        return (labels == best_id).astype(np.uint8)

    def __call__(self, patch: np.ndarray) -> np.ndarray:
        # patch: HxWxC (uint8)
        h, w = patch.shape[:2]
        rgb = patch.astype(np.float32) / 255.0

        # EUPE patch embedding proxy features
        x = torch.from_numpy(rgb.transpose(2, 0, 1)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            feats = F.conv2d(x, self.weight, self.bias, stride=self.stride, padding=0)
            feats = F.interpolate(
                feats, size=(h, w), mode="bilinear", align_corners=False
            )
        feat_np = feats.squeeze(0).detach().cpu().numpy()
        feat_maps = [self._norm_map(feat_np[i]) for i in range(feat_np.shape[0])]
        while len(feat_maps) < 3:
            feat_maps.append(np.zeros((h, w), dtype=np.float32))

        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
        sat = hsv[..., 1].astype(np.float32) / 255.0
        val = hsv[..., 2].astype(np.float32) / 255.0

        y_grid, x_grid = np.meshgrid(
            np.linspace(0.0, 1.0, h, dtype=np.float32),
            np.linspace(0.0, 1.0, w, dtype=np.float32),
            indexing="ij",
        )

        # 前景（蘑菇）先验：高亮 + 低饱和
        mushroom_prior = ((val > 0.55) & (sat < 0.38)).astype(np.uint8)
        mushroom_prior = cv2.morphologyEx(
            mushroom_prior, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )

        # 基于距离变换把蘑菇前景拆成“粗端(帽)”与“细端(杆)”
        dist = cv2.distanceTransform(mushroom_prior, cv2.DIST_L2, 5)
        dist_n = self._norm_map(dist)
        cap_prior = (mushroom_prior.astype(np.float32) * dist_n).astype(np.float32)
        stem_prior = (mushroom_prior.astype(np.float32) * (1.0 - dist_n)).astype(
            np.float32
        )

        # 结构约束：从帽部种子在前景内传播，强化“帽-柄”连通细长路径。
        cap_seed = (cap_prior > 0.55).astype(np.uint8)
        if int(cap_seed.sum()) == 0:
            cap_seed = (cap_prior > 0.42).astype(np.uint8)

        stem_path = cap_seed.copy()
        grow_kernel = np.ones((3, 3), np.uint8)
        # 多次膨胀并限制在 mushroom_prior 内，得到与帽部连通的潜在柄区域。
        for _ in range(42):
            stem_path = cv2.dilate(stem_path, grow_kernel, iterations=1)
            stem_path = (stem_path & mushroom_prior).astype(np.uint8)

        # 通过细长方向的闭运算减少柄部断裂，避免被背景吞没。
        stem_path = cv2.morphologyEx(
            stem_path,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 13)),
        )

        # 帽部自身不应被当成柄，避免帽柄混淆。
        stem_path = (stem_path & (cap_prior < 0.70)).astype(np.float32)

        # 将结构约束融合到柄先验，提升细长连通结构权重。
        stem_prior = np.maximum(
            stem_prior,
            stem_path * (0.65 + 0.35 * (1.0 - dist_n)),
        )

        # 菌袋先验：把菌袋视为承载主体，而不是“除蘑菇外的剩余区域”。
        # 半透明塑料、低对比度和反光会削弱颜色判别，因此阈值更宽，后续靠结构约束收敛。
        bag_candidate = (
            (val > 0.08)
            & (val < 0.95)
            & (sat > 0.02)
            & (sat < 0.88)
        ).astype(np.uint8)

        # 袋体通常位于图像中下部，且是较大、连续的主体区域。
        lower_body_gate = (y_grid > 0.28).astype(np.uint8)
        bag_candidate = (bag_candidate & lower_body_gate).astype(np.uint8)

        # 绝大多数蘑菇实体不应直接并入菌袋，仅在较低位置保留与柄根相邻的恢复空间。
        bag_candidate = (
            bag_candidate & ((mushroom_prior == 0) | (y_grid > 0.62))
        ).astype(np.uint8)

        # 让袋体保持与柄根部的支撑关系，但不允许直接吞没柄中心线。
        stem_support_zone = cv2.dilate(
            stem_path.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 61)),
            iterations=1,
        )
        stem_support_zone = (stem_support_zone & (y_grid > 0.46)).astype(np.uint8)
        stem_core_zone = cv2.erode(
            stem_path.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 9)),
            iterations=1,
        )

        # 使用更大的闭运算与孔洞填充恢复袋体完整性，抵抗塑料褶皱和背景侵蚀。
        bag_candidate = cv2.morphologyEx(
            bag_candidate,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)),
        )
        bag_candidate = cv2.morphologyEx(
            bag_candidate,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
        )
        bag_candidate = self._fill_holes(bag_candidate)

        # 通过帽/柄附近的支撑区域恢复袋口邻域，避免被背景切断。
        bag_candidate = np.maximum(
            bag_candidate,
            (stem_support_zone & lower_body_gate).astype(np.uint8),
        )
        bag_candidate = self._select_bag_component(bag_candidate)
        bag_candidate = self._fill_holes(bag_candidate)

        # 层级约束：先确定菌袋主体，再在非菌袋区域内恢复菌杆/菌帽。
        ys_bag, xs_bag = np.where(bag_candidate > 0)
        if len(xs_bag) > 0:
            bag_x1, bag_x2 = int(xs_bag.min()), int(xs_bag.max())
            bag_y1, bag_y2 = int(ys_bag.min()), int(ys_bag.max())
            bag_w = max(1, bag_x2 - bag_x1 + 1)
            bag_h = max(1, bag_y2 - bag_y1 + 1)

            expand_x = int(0.35 * bag_w)
            stem_x1 = max(0, bag_x1 - expand_x)
            stem_x2 = min(w - 1, bag_x2 + expand_x)

            # 柄帽通常位于袋体上方或袋口附近，避免被袋体覆盖后全退化为 background。
            mushroom_zone = np.zeros((h, w), dtype=np.uint8)
            y_top = max(0, bag_y1 - int(0.55 * bag_h))
            y_bottom = min(h, bag_y1 + int(0.25 * bag_h))
            mushroom_zone[y_top:y_bottom, stem_x1 : stem_x2 + 1] = 1

            non_bag = (1 - bag_candidate).astype(np.uint8)
            mushroom_nonbag = (mushroom_prior & non_bag & mushroom_zone).astype(np.uint8)

            if int(mushroom_nonbag.sum()) > 0:
                # 在非袋体蘑菇区域内拆分细长柄与顶部帽。
                dist_nonbag = cv2.distanceTransform(mushroom_nonbag, cv2.DIST_L2, 5)
                dist_nonbag_n = self._norm_map(dist_nonbag)

                upper_gate = np.zeros((h, w), dtype=np.float32)
                upper_gate[: min(h, bag_y1 + int(0.05 * bag_h)), :] = 1.0

                stem_refine = mushroom_nonbag.astype(np.float32) * (1.0 - dist_nonbag_n)
                cap_refine = mushroom_nonbag.astype(np.float32) * np.maximum(
                    dist_nonbag_n,
                    0.75 * upper_gate,
                )

                stem_prior = np.maximum(stem_prior, stem_refine)
                cap_prior = np.maximum(cap_prior, cap_refine)

        bag_prior = bag_candidate.astype(np.float32) * (0.55 + 0.45 * y_grid)

        # 仅排斥柄中心细线，保留袋体与柄根部的结构邻接，避免袋体被严重侵蚀。
        bag_prior = bag_prior * (1.0 - 0.32 * stem_core_zone.astype(np.float32))

        # 背景先验
        fg_union = np.clip(
            np.maximum(np.maximum(cap_prior, stem_prior), bag_prior), 0.0, 1.0
        )
        bg_prior = (1.0 - fg_union).astype(np.float32)

        # 在袋体主体区显式压低背景响应，防止大面积袋体回退到 background。
        bg_prior = bg_prior * (1.0 - 0.72 * bag_candidate.astype(np.float32))

        bag_logit = 2.88 * bag_prior + 0.55 * (1.0 - feat_maps[0]) + 0.28 * y_grid
        stem_logit = 2.35 * stem_prior + 0.52 * feat_maps[1]
        cap_logit = 2.45 * cap_prior + 0.6 * feat_maps[2]
        bg_logit = 1.7 * bg_prior

        # Label mapping required by task:
        # 0: background, 1: bag, 2: stem, 3: cap
        logits = np.stack([bg_logit, bag_logit, stem_logit, cap_logit], axis=0).astype(
            np.float32
        )
        return logits


def _overlay_mask(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    # 0-bg, 1-bag, 2-stem, 3-cap
    color = np.zeros_like(image, dtype=np.uint8)
    color[mask == 0] = np.array([0, 0, 0], dtype=np.uint8)
    color[mask == 1] = np.array([38, 180, 255], dtype=np.uint8)  # bag: orange-ish
    color[mask == 2] = np.array([85, 235, 85], dtype=np.uint8)  # stem: green
    color[mask == 3] = np.array([255, 90, 90], dtype=np.uint8)  # cap: red

    alpha = 0.42
    overlay = image.copy()
    fg = mask != 0
    overlay[fg] = (alpha * color[fg] + (1.0 - alpha) * image[fg]).astype(np.uint8)
    return overlay


def _largest_component_bbox(mask: np.ndarray) -> List[int]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return [0, 0, 0, 0]
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def run_inference(args: argparse.Namespace) -> Dict[str, object]:
    from segmentation.geometry import BagReference, estimate_mm_per_pixel_from_bag
    from segmentation.pipeline import (
        SegmentationConfig,
        create_mushroom_segmentation_pipeline,
    )

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    vis_dir = output_dir / "visualization"
    vis_dir.mkdir(parents=True, exist_ok=True)

    img_paths = sorted(
        [
            p
            for p in input_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
        ]
    )
    if not img_paths:
        raise ValueError(f"输入目录无图像: {input_dir}")

    predictor = EUPEProxyPredictor(Path(args.model_path), device=args.device)
    cfg = SegmentationConfig(
        class_to_id={"background": 0, "bag": 1, "stem": 2, "cap": 3},
        crop_size=args.crop_size,
        stride=args.stride,
        scales=(1.0,),
        bag_reference_mm=(args.bag_width_mm, args.bag_height_mm),
        min_stem_area_px=args.min_stem_area_px,
        min_cap_area_px=args.min_cap_area_px,
    )
    pipeline = create_mushroom_segmentation_pipeline(predictor, cfg)

    bag_ref = BagReference(width_mm=args.bag_width_mm, height_mm=args.bag_height_mm)

    per_image: List[ImageResult] = []
    for path in img_paths:
        image = np.array(Image.open(path).convert("RGB"))
        semantic = pipeline.infer_semantic_mask(image)

        bag_mask = (semantic == 1).astype(np.uint8)
        stem_mask = (semantic == 2).astype(np.uint8)
        cap_mask = (semantic == 3).astype(np.uint8)

        mm_per_px: float | None
        metrics = None
        try:
            mm_per_px = estimate_mm_per_pixel_from_bag(bag_mask, bag_ref)
            metrics = pipeline.compute_frame_metrics(semantic)
        except ValueError:
            mm_per_px = None

        overlay = _overlay_mask(image, semantic)
        Image.fromarray(overlay).save(vis_dir / f"{path.stem}_overlay.png")
        Image.fromarray((semantic * 60).astype(np.uint8)).save(
            vis_dir / f"{path.stem}_mask.png"
        )

        result = ImageResult(
            image_name=path.name,
            bag_area_px=int(bag_mask.sum()),
            stem_area_px=int(stem_mask.sum()),
            cap_area_px=int(cap_mask.sum()),
            bag_bbox_xyxy=_largest_component_bbox(bag_mask),
            mm_per_px=mm_per_px,
            stem_length_mm=(metrics.stem_length_mm if metrics else None),
            stem_diameter_mm=(metrics.stem_diameter_mm if metrics else None),
            cap_diameter_mm=(metrics.cap_diameter_mm if metrics else None),
            cap_area_mm2=(metrics.cap_area_mm2 if metrics else None),
        )
        per_image.append(result)

    payload = {
        "model_path": str(args.model_path),
        "algorithm": "EUPE checkpoint patch-embed proxy + current segmentation pipeline",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "bag_reference_mm": {"width": args.bag_width_mm, "height": args.bag_height_mm},
        "images": [r.__dict__ for r in per_image],
    }

    with (output_dir / "segmentation_metrics.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="蘑菇图像分割推理（菌袋/菌杆/菌帽）")
    parser.add_argument("--input-dir", default="data", help="测试图像目录")
    parser.add_argument(
        "--model-path", default="models/EUPE-ViT-B.pt", help="EUPE 权重路径"
    )
    parser.add_argument(
        "--output-dir", default="output/segmentation_infer", help="结果输出目录"
    )
    parser.add_argument(
        "--device", default="cpu", choices=["cpu", "cuda"], help="推理设备"
    )
    parser.add_argument("--crop-size", type=int, default=896, help="滑窗尺寸")
    parser.add_argument("--stride", type=int, default=640, help="滑窗步长")
    parser.add_argument(
        "--bag-width-mm", type=float, default=240.0, help="菌袋标准宽度(mm)"
    )
    parser.add_argument(
        "--bag-height-mm", type=float, default=140.0, help="菌袋标准高度(mm)"
    )
    parser.add_argument("--min-stem-area-px", type=int, default=30, help="最小菌杆面积")
    parser.add_argument("--min-cap-area-px", type=int, default=24, help="最小菌帽面积")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = run_inference(args)

    print("分割推理完成")
    print(f"模型: {payload['model_path']}")
    print(f"输出目录: {payload['output_dir']}")
    print(f"图像数量: {len(payload['images'])}")
    for item in payload["images"]:
        print(
            "- "
            f"{item['image_name']} | bag_px={item['bag_area_px']} | "
            f"stem_len_mm={item['stem_length_mm']} | cap_d_mm={item['cap_diameter_mm']}"
        )


if __name__ == "__main__":
    main()
