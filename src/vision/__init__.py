"""
CLIP图像处理模块

负责蘑菇图像的CLIP推理、图像编码、特征提取、图像向量化、多模态融合等功能。

模块组件：
- clip_inference: CLIP模型推理核心
- clip_inference_scheduler: CLIP推理调度器
- clip_app: CLIP应用接口
- get_env_status: 环境状态获取
- mushroom_image_encoder: 蘑菇图像编码器
- mushroom_image_processor: 图像处理器
- recent_image_processor: 最近图像处理器
"""

# 导入核心组件
from .clip_inference import *
from .clip_inference_scheduler import *
from .clip_app import *
from .get_env_status import *
from .fine_tuning import *

# 导入图像处理组件（从utils迁移过来）
try:
    from .mushroom_image_encoder import *
    from .mushroom_image_processor import *
    from .recent_image_processor import *
except ImportError:
    # 迁移过程中可能暂时无法导入
    pass

__all__ = [
    # CLIP推理相关
    'clip_inference',
    'clip_inference_scheduler', 
    'clip_app',
    'get_env_status',
    'fine_tuning',
    
    # 图像处理相关
    'mushroom_image_encoder',
    'mushroom_image_processor',
    'recent_image_processor',
]
