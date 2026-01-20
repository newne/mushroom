"""
建表任务模块
负责数据库表的创建和初始化
"""

import time
from datetime import datetime

from global_const.const_config import CLIP_INFERENCE_MAX_RETRIES, CLIP_INFERENCE_RETRY_DELAY
from utils.create_table import create_tables
from utils.loguru_setting import logger


def safe_create_tables() -> None:
    """建表任务（带重试机制）"""
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[TASK] 开始执行建表任务 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            create_tables()
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[TASK] 建表任务完成，耗时: {duration:.2f}秒")
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TASK] 建表任务失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[TASK] 建表任务失败，已达到最大重试次数 ({max_retries})")
                # 建表失败不应该导致调度器崩溃，但需要记录严重错误
                logger.critical("[TASK] 数据库表可能未正确创建，后续任务可能会失败")
                return
            else:
                logger.error(f"[TASK] 建表任务遇到非连接错误，不再重试")
                return