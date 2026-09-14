"""EUPE 风格分割流水线骨架：滑窗、多尺度、时序稳定。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import numpy as np

from segmentation.geometry import (
    BagReference,
    MushroomSizeMetrics,
    estimate_mm_per_pixel_from_bag,
    extract_mushroom_size_metrics,
    growth_rate,
)
from segmentation.postprocess import (
    InstanceMask,
    MaskPostprocessConfig,
    extract_instances_from_mask,
)

_temporal_mod = importlib.import_module("segmentation.temporal")
TemporalConfig = _temporal_mod.TemporalConfig
TemporalSmoother = _temporal_mod.TemporalSmoother


@dataclass(frozen=True)
class SegmentationConfig:
    """分割推理配置。"""

    class_to_id: Dict[str, int]
    crop_size: int = 1024
    stride: int = 768
    scales: Tuple[float, ...] = (1.0,)
    bag_reference_mm: Tuple[float, float] = (240.0, 140.0)
    min_stem_area_px: int = 24
    min_cap_area_px: int = 32


class SegmentationPipeline:
    """可直接集成到业务系统的分割与生长评估管线。"""

    def __init__(
        self,
        model_predictor: Callable[[np.ndarray], np.ndarray],
        config: SegmentationConfig,
        temporal_config: TemporalConfig | None = None,
    ):
        """初始化。

        Args:
            model_predictor: 输入 HxWxC 图像块，输出 CxHxW logits
            config: 分割配置
            temporal_config: 时序平滑配置
        """
        self.model_predictor = model_predictor
        self.config = config
        self.temporal = TemporalSmoother(temporal_config)

    def _sliding_windows(self, image: np.ndarray):
        h, w = image.shape[:2]
        crop = self.config.crop_size
        stride = self.config.stride

        y_positions = list(range(0, max(1, h - crop + 1), stride))
        x_positions = list(range(0, max(1, w - crop + 1), stride))
        if y_positions[-1] != max(0, h - crop):
            y_positions.append(max(0, h - crop))
        if x_positions[-1] != max(0, w - crop):
            x_positions.append(max(0, w - crop))

        for y in y_positions:
            for x in x_positions:
                y2, x2 = min(y + crop, h), min(x + crop, w)
                patch = image[y:y2, x:x2]
                yield y, x, y2, x2, patch

    def _infer_single_scale(self, image: np.ndarray) -> np.ndarray:
        class_count = len(self.config.class_to_id)
        h, w = image.shape[:2]
        logits_sum = np.zeros((class_count, h, w), dtype=np.float32)
        votes = np.zeros((h, w), dtype=np.float32)

        for y1, x1, y2, x2, patch in self._sliding_windows(image):
            patch_logits = self.model_predictor(patch)
            if patch_logits.shape[1:] != patch.shape[:2]:
                raise ValueError("model_predictor 输出尺寸需与输入 patch 一致")
            logits_sum[:, y1:y2, x1:x2] += patch_logits
            votes[y1:y2, x1:x2] += 1.0

        return logits_sum / np.maximum(votes, 1.0)

    def infer_logits(self, image: np.ndarray) -> np.ndarray:
        """多尺度 + 滑窗融合。"""
        logits_acc: np.ndarray | None = None

        for scale in self.config.scales:
            if abs(scale - 1.0) > 1e-6:
                raise NotImplementedError(
                    "当前实现支持 scale=1.0；多尺度缩放需在部署侧接入 cv2/torch 插值后启用"
                )
            scale_logits = self._infer_single_scale(image)
            logits_acc = (
                scale_logits if logits_acc is None else logits_acc + scale_logits
            )

        assert logits_acc is not None
        logits = logits_acc / max(len(self.config.scales), 1)
        return self.temporal.smooth_logits(logits)

    def infer_semantic_mask(self, image: np.ndarray) -> np.ndarray:
        logits = self.infer_logits(image)
        return logits.argmax(axis=0).astype(np.uint8)

    def build_instances(
        self, semantic_mask: np.ndarray
    ) -> Dict[str, List[InstanceMask]]:
        class_map = self.config.class_to_id
        stem_id = class_map["stem"]
        cap_id = class_map["cap"]

        stem_cfg = MaskPostprocessConfig(min_area_px=self.config.min_stem_area_px)
        cap_cfg = MaskPostprocessConfig(min_area_px=self.config.min_cap_area_px)

        stems = extract_instances_from_mask(
            (semantic_mask == stem_id), stem_id, stem_cfg
        )
        caps = extract_instances_from_mask((semantic_mask == cap_id), cap_id, cap_cfg)
        return {"stem": stems, "cap": caps}

    def compute_frame_metrics(self, semantic_mask: np.ndarray) -> MushroomSizeMetrics:
        class_map = self.config.class_to_id
        bag_id = class_map["bag"]
        stem_id = class_map["stem"]
        cap_id = class_map["cap"]

        bag_mask = (semantic_mask == bag_id).astype(np.uint8)
        stem_masks = [(semantic_mask == stem_id).astype(np.uint8)]
        cap_masks = [(semantic_mask == cap_id).astype(np.uint8)]

        bag_ref = BagReference(
            width_mm=self.config.bag_reference_mm[0],
            height_mm=self.config.bag_reference_mm[1],
        )
        mm_per_px = estimate_mm_per_pixel_from_bag(bag_mask, bag_ref)
        return extract_mushroom_size_metrics(stem_masks, cap_masks, mm_per_px)

    def compare_growth(
        self,
        current_metrics: MushroomSizeMetrics,
        previous_metrics: MushroomSizeMetrics,
        delta_hours: float,
    ) -> Dict[str, float]:
        """输出可直接用于生产调参的增长速度指标。"""
        return {
            "d_stem_length_mm_per_h": growth_rate(
                current_metrics.stem_length_mm,
                previous_metrics.stem_length_mm,
                delta_hours,
            ),
            "d_cap_diameter_mm_per_h": growth_rate(
                current_metrics.cap_diameter_mm,
                previous_metrics.cap_diameter_mm,
                delta_hours,
            ),
            "d_stem_area_mm2_per_h": growth_rate(
                current_metrics.stem_area_mm2,
                previous_metrics.stem_area_mm2,
                delta_hours,
            ),
            "d_cap_area_mm2_per_h": growth_rate(
                current_metrics.cap_area_mm2,
                previous_metrics.cap_area_mm2,
                delta_hours,
            ),
        }


def create_mushroom_segmentation_pipeline(
    model_predictor: Callable[[np.ndarray], np.ndarray],
    config: SegmentationConfig | None = None,
) -> SegmentationPipeline:
    """工厂函数：创建蘑菇分割管线实例。"""
    default_config = SegmentationConfig(class_to_id={"bag": 0, "stem": 1, "cap": 2})
    return SegmentationPipeline(
        model_predictor=model_predictor,
        config=config or default_config,
    )
