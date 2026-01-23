"""
@Project ：get_data
@File    ：const_config.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/11/5 18:32
@Desc     : 系统级常量配置
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd
import plotly.express as px


# ===================== 蘑菇房相关常量 =====================

# 蘑菇房ID列表
MUSHROOM_ROOM_IDS: List[str] = ["607", "608", "611", "612"]

# 任务重试配置
TABLE_CREATION_MAX_RETRIES: int = 3
TABLE_CREATION_RETRY_DELAY: int = 5

ENV_STATS_MAX_RETRIES: int = 3
ENV_STATS_RETRY_DELAY: int = 5

MONITORING_MAX_RETRIES: int = 3
MONITORING_RETRY_DELAY: int = 5

# 决策分析执行时间点 (小时, 分钟)
DECISION_ANALYSIS_SCHEDULE_TIMES: List[Tuple[int, int]] = [
    (10, 0),   # 上午10:00
    (12, 0),   # 中午12:00
    (14, 0),   # 下午14:00
]

# 输出目录路径（相对于项目根目录）
OUTPUT_DIR_NAME: str = "output"

# 决策分析输出文件名格式
DECISION_OUTPUT_FILENAME_PATTERN: str = "decision_analysis_{room_id}_{timestamp}.json"

# 定时任务相关常量
DECISION_ANALYSIS_MAX_RETRIES: int = 3
DECISION_ANALYSIS_RETRY_DELAY: int = 5  # 秒

# CLIP推理任务相关常量
CLIP_INFERENCE_MAX_RETRIES: int = 3
CLIP_INFERENCE_RETRY_DELAY: int = 5  # 秒
CLIP_INFERENCE_BATCH_SIZE: int = 20  # 每批处理的图像数量
CLIP_INFERENCE_HOUR_LOOKBACK: int = 1  # 处理最近N小时的图像

# 决策分析配置
DECISION_ANALYSIS_CONFIG = {
    "image_aggregation_window": 30,  # 分钟，图像聚合时间窗口
    "adjustment_thresholds": {
        "temperature": 0.5,    # 温度调整阈值
        "humidity": 2.0,       # 湿度调整阈值
        "co2": 100,           # CO2调整阈值
    },
    "priority_weights": {
        "deviation_severity": 0.4,
        "historical_success": 0.3,
        "risk_level": 0.3,
    },
    "risk_levels": {
        "low": {"threshold": 0.3, "description": "风险较低，可安全调整"},
        "medium": {"threshold": 0.6, "description": "中等风险，需谨慎调整"},
        "high": {"threshold": 0.8, "description": "高风险，建议分步调整"},
        "critical": {"threshold": 1.0, "description": "极高风险，需专家评估"}
    }
}


class DataValidator:
    """Data validation utilities"""

    @staticmethod
    def validate_numeric_columns(df: pd.DataFrame, columns: List[str]) -> bool:
        """Validate if columns are numeric"""
        return all(np.issubdtype(df[col].dtype, np.number) for col in columns)

    @staticmethod
    def remove_outliers(
        df: pd.DataFrame, column: str, threshold: float = 3
    ) -> pd.DataFrame:
        """Remove outliers using z-score method"""
        z_scores = np.abs((df[column] - df[column].mean()) / df[column].std())
        return df[z_scores < threshold]
