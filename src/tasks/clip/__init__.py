"""
CLIP推理任务模块

负责蘑菇图像的CLIP推理处理相关的定时任务。
"""

from .clip_tasks import safe_hourly_clip_inference
from .clip_executor import (
    CLIPInferenceTask,
    clip_inference_task,
    get_clip_inference_summary,
    validate_clip_quality
)

__all__ = [
    'safe_hourly_clip_inference',
    'CLIPInferenceTask',
    'clip_inference_task',
    'get_clip_inference_summary',
    'validate_clip_quality'
]