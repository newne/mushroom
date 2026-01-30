"""CLIP推理任务模块兼容导出"""

from vision.tasks import safe_hourly_text_quality_inference, safe_daily_top_quality_clip_inference
from vision.executor import (
    CLIPInferenceTask,
    clip_inference_task,
    get_clip_inference_summary,
    validate_clip_quality
)

__all__ = [
    'safe_hourly_text_quality_inference',
    'safe_daily_top_quality_clip_inference',
    'CLIPInferenceTask',
    'clip_inference_task',
    'get_clip_inference_summary',
    'validate_clip_quality'
]