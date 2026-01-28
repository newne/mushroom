"""
定时任务模块

提供统一的任务接口，供调度器调用。
每个任务模块都包含完整的执行逻辑和错误处理机制。

任务模块：
- base_task: 基础任务执行框架
- table: 数据库表管理任务
- env: 环境统计任务  
- monitoring: 设定点监控任务
- clip: CLIP推理任务
- decision: 决策分析任务
"""

# 导入基础任务框架
from .base_task import BaseTask, TaskExecutor, task_wrapper

# 导入所有任务接口函数（从新模块导入）
from .table import safe_create_tables
from environment.tasks import safe_daily_env_stats
from monitoring.tasks import safe_hourly_setpoint_monitoring
from vision.tasks import safe_hourly_clip_inference
from decision_analysis.tasks import safe_batch_decision_analysis

# 任务接口列表
__all__ = [
    # 基础框架
    'BaseTask',
    'TaskExecutor', 
    'task_wrapper',
    # 任务接口
    'safe_create_tables',
    'safe_daily_env_stats', 
    'safe_hourly_setpoint_monitoring',
    'safe_hourly_clip_inference',
    'safe_batch_decision_analysis',
]