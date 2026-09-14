"""蘑菇分割与生长分析模块。

该模块面向菌袋/菌杆/菌帽三类分割，并提供：
1. 滑窗推理与多尺度融合
2. 几何后处理与真实尺寸估计
3. 视频时序一致性增强
4. 生长曲线建模输入特征构建
"""

from .geometry import (
    MeasurementResult,
    MushroomSizeMetrics,
    estimate_mm_per_pixel_from_bag,
    extract_mushroom_size_metrics,
)
from .postprocess import (
    InstanceMask,
    MaskPostprocessConfig,
    extract_instances_from_mask,
)

__all__ = [
    "MeasurementResult",
    "MushroomSizeMetrics",
    "estimate_mm_per_pixel_from_bag",
    "extract_mushroom_size_metrics",
    "InstanceMask",
    "MaskPostprocessConfig",
    "extract_instances_from_mask",
]
