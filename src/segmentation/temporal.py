"""视频时序一致性：匹配、平滑、置信度融合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from segmentation.postprocess import InstanceMask

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass(frozen=True)
class TemporalConfig:
    """时序平滑配置。"""

    iou_match_threshold: float = 0.3
    ema_alpha: float = 0.65
    confidence_decay: float = 0.9


def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    iw = max(0, ix2 - ix1 + 1)
    ih = max(0, iy2 - iy1 + 1)
    inter = iw * ih

    area_a = max(0, ax2 - ax1 + 1) * max(0, ay2 - ay1 + 1)
    area_b = max(0, bx2 - bx1 + 1) * max(0, by2 - by1 + 1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return float(inter / union)


def match_instances_iou(
    prev_instances: List[InstanceMask],
    cur_instances: List[InstanceMask],
    iou_threshold: float = 0.3,
) -> Dict[int, int]:
    """使用 IoU 进行 tracking-by-matching。返回 prev_id -> cur_id。"""
    matches: Dict[int, int] = {}
    used_cur: set[int] = set()

    for prev in prev_instances:
        best_iou = 0.0
        best_cur_id = -1
        for cur in cur_instances:
            if cur.instance_id in used_cur:
                continue
            iou = _bbox_iou(prev.bbox_xyxy, cur.bbox_xyxy)
            if iou > best_iou:
                best_iou = iou
                best_cur_id = cur.instance_id

        if best_iou >= iou_threshold and best_cur_id > 0:
            matches[prev.instance_id] = best_cur_id
            used_cur.add(best_cur_id)

    return matches


class TemporalSmoother:
    """对分割概率图和实例置信度做时序平滑。"""

    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or TemporalConfig()
        self._prev_logits: np.ndarray | None = None
        self._track_conf: Dict[int, float] = {}

    def smooth_logits(self, logits: np.ndarray) -> np.ndarray:
        """EMA 平滑，缓解 segmentation flickering。"""
        if self._prev_logits is None or self._prev_logits.shape != logits.shape:
            self._prev_logits = logits.astype(np.float32)
            return logits

        alpha = self.config.ema_alpha
        smoothed = alpha * logits.astype(np.float32) + (1 - alpha) * self._prev_logits
        self._prev_logits = smoothed
        return smoothed

    def smooth_instance_confidence(
        self,
        prev_instances: List[InstanceMask],
        cur_instances: List[InstanceMask],
    ) -> Dict[int, float]:
        """根据匹配关系平滑实例置信度。"""
        mapping = match_instances_iou(
            prev_instances,
            cur_instances,
            iou_threshold=self.config.iou_match_threshold,
        )

        cur_conf: Dict[int, float] = {}
        for cur in cur_instances:
            matched_prev_ids = [k for k, v in mapping.items() if v == cur.instance_id]
            if not matched_prev_ids:
                cur_conf[cur.instance_id] = float(cur.confidence)
                continue

            prev_id = matched_prev_ids[0]
            prev_conf = self._track_conf.get(prev_id, 1.0)
            blended = (
                self.config.ema_alpha * float(cur.confidence)
                + (1 - self.config.ema_alpha) * prev_conf
            )
            cur_conf[cur.instance_id] = blended

        for inst in prev_instances:
            if inst.instance_id not in mapping:
                old = self._track_conf.get(inst.instance_id, float(inst.confidence))
                self._track_conf[inst.instance_id] = old * self.config.confidence_decay

        self._track_conf = cur_conf
        return cur_conf


def align_logits_with_optical_flow(
    prev_frame: np.ndarray,
    cur_frame: np.ndarray,
    prev_logits: np.ndarray,
) -> np.ndarray:
    """使用光流将上一帧 logits 对齐到当前帧。

    若未安装 OpenCV，则直接返回原 logits（平稳降级）。
    """
    if cv2 is None:
        return prev_logits

    if prev_frame.ndim == 3:
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    else:
        prev_gray = prev_frame
    if cur_frame.ndim == 3:
        cur_gray = cv2.cvtColor(cur_frame, cv2.COLOR_BGR2GRAY)
    else:
        cur_gray = cur_frame

    flow = cv2.calcOpticalFlowFarneback(
        prev_gray.astype(np.uint8),
        cur_gray.astype(np.uint8),
        None,
        0.5,
        3,
        15,
        3,
        5,
        1.2,
        0,
    )

    h, w = prev_gray.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
    map_x = (grid_x + flow[..., 0]).astype(np.float32)
    map_y = (grid_y + flow[..., 1]).astype(np.float32)

    aligned = np.zeros_like(prev_logits, dtype=np.float32)
    for c in range(prev_logits.shape[0]):
        aligned[c] = cv2.remap(
            prev_logits[c].astype(np.float32),
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

    return aligned
