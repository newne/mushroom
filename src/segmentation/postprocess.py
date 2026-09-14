"""mask 后处理与实例提取。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


@dataclass(frozen=True)
class MaskPostprocessConfig:
    """后处理超参数。"""

    min_area_px: int = 32
    max_area_px: int = 10_000_000
    connectivity: int = 8


@dataclass(frozen=True)
class InstanceMask:
    """实例级 mask 与几何信息。"""

    instance_id: int
    class_id: int
    mask: np.ndarray
    area_px: int
    bbox_xyxy: Tuple[int, int, int, int]
    confidence: float = 1.0


def _neighbors(y: int, x: int, h: int, w: int, connectivity: int):
    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w:
            yield ny, nx
    if connectivity == 8:
        for dy, dx in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                yield ny, nx


def _connected_components(
    binary_mask: np.ndarray, connectivity: int
) -> List[np.ndarray]:
    h, w = binary_mask.shape
    visited = np.zeros_like(binary_mask, dtype=np.uint8)
    components: List[np.ndarray] = []

    ys, xs = np.where(binary_mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue

        q = deque([(sy, sx)])
        visited[sy, sx] = 1
        pixels = []

        while q:
            cy, cx = q.popleft()
            pixels.append((cy, cx))
            for ny, nx in _neighbors(cy, cx, h, w, connectivity):
                if visited[ny, nx] or not binary_mask[ny, nx]:
                    continue
                visited[ny, nx] = 1
                q.append((ny, nx))

        comp_mask = np.zeros_like(binary_mask, dtype=np.uint8)
        py, px = zip(*pixels)
        comp_mask[np.array(py), np.array(px)] = 1
        components.append(comp_mask)

    return components


def extract_instances_from_mask(
    class_mask: np.ndarray,
    class_id: int,
    config: MaskPostprocessConfig | None = None,
) -> List[InstanceMask]:
    """从语义 mask 中提取实例，并进行面积过滤。"""
    cfg = config or MaskPostprocessConfig()
    if class_mask.ndim != 2:
        raise ValueError("class_mask 必须是二维数组")

    binary = (class_mask > 0).astype(np.uint8)
    components = _connected_components(binary, connectivity=cfg.connectivity)

    instances: List[InstanceMask] = []
    instance_id = 1
    for comp in components:
        ys, xs = np.where(comp > 0)
        area = len(xs)
        if area < cfg.min_area_px or area > cfg.max_area_px:
            continue

        x1, x2 = int(xs.min()), int(xs.max())
        y1, y2 = int(ys.min()), int(ys.max())
        instances.append(
            InstanceMask(
                instance_id=instance_id,
                class_id=class_id,
                mask=comp,
                area_px=area,
                bbox_xyxy=(x1, y1, x2, y2),
                confidence=1.0,
            )
        )
        instance_id += 1

    return instances
