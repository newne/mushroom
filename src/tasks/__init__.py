"""
任务模块
包含所有定时任务的业务逻辑实现

增强功能:
- 多图像综合分析
- 结构化参数调整建议
- 风险评估和优先级指导
"""

from .table_tasks import safe_create_tables
from .env_tasks import safe_daily_env_stats
from .monitoring_tasks import safe_hourly_setpoint_monitoring
from .clip_tasks import safe_hourly_clip_inference
from .decision_tasks import (
    # 增强决策分析函数
    safe_enhanced_decision_analysis_for_room,
    safe_enhanced_batch_decision_analysis,
    safe_enhanced_decision_analysis_10_00,
    safe_enhanced_decision_analysis_12_00,
    safe_enhanced_decision_analysis_14_00,
    # 传统决策分析函数（向后兼容）
    safe_decision_analysis_for_room,
    safe_batch_decision_analysis,
    safe_decision_analysis_10_00,
    safe_decision_analysis_12_00,
    safe_decision_analysis_14_00,
)

__all__ = [
    'safe_create_tables',
    'safe_daily_env_stats',
    'safe_hourly_setpoint_monitoring',
    'safe_hourly_clip_inference',
    # 增强决策分析函数
    'safe_enhanced_decision_analysis_for_room',
    'safe_enhanced_batch_decision_analysis',
    'safe_enhanced_decision_analysis_10_00',
    'safe_enhanced_decision_analysis_12_00',
    'safe_enhanced_decision_analysis_14_00',
    # 传统决策分析函数（向后兼容）
    'safe_decision_analysis_for_room',
    'safe_batch_decision_analysis',
    'safe_decision_analysis_10_00',
    'safe_decision_analysis_12_00',
    'safe_decision_analysis_14_00',
]