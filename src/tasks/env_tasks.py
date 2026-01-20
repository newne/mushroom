"""
环境统计任务模块
负责每日环境数据的统计和处理
"""

import time
from datetime import datetime, timedelta

from global_const.const_config import DECISION_ANALYSIS_MAX_RETRIES, DECISION_ANALYSIS_RETRY_DELAY
from utils.env_data_processor import create_env_data_processor
from utils.loguru_setting import logger


def safe_daily_env_stats() -> None:
    """每日环境统计任务（带重试机制）"""
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[TASK] 开始执行每日环境统计 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            processor = create_env_data_processor()
            yesterday = datetime.now().date() - timedelta(days=1)
            processor.compute_and_store_daily_stats(
                datetime(yesterday.year, yesterday.month, yesterday.day)
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[TASK] 每日环境统计完成，耗时: {duration:.2f}秒")
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TASK] 每日环境统计失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[TASK] 每日环境统计失败，已达到最大重试次数 ({max_retries})")
                return
            else:
                logger.error(f"[TASK] 每日环境统计遇到非连接错误，不再重试")
                return