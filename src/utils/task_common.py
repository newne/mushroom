"""
任务公共组件模块

提供所有定时任务共用的工具函数和组件。
"""

import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from functools import wraps

from utils.loguru_setting import logger


def task_retry_wrapper(
    task_name: str,
    max_retries: int = 3,
    retry_delay: int = 5,
    connection_error_keywords: list = None
):
    """
    任务重试装饰器
    
    Args:
        task_name: 任务名称
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）
        connection_error_keywords: 连接错误关键词列表
    """
    if connection_error_keywords is None:
        connection_error_keywords = ['timeout', 'connection', 'connect', 'database', 'server']
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"[{task_name}] 开始执行任务 (尝试 {attempt}/{max_retries})")
                    start_time = datetime.now()
                    
                    # 执行任务
                    result = func(*args, **kwargs)
                    
                    duration = (datetime.now() - start_time).total_seconds()
                    logger.info(f"[{task_name}] 任务执行完成，耗时: {duration:.2f}秒")
                    
                    return result
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"[{task_name}] 任务执行失败 (尝试 {attempt}/{max_retries}): {error_msg}")
                    
                    # 检查是否是连接错误
                    is_connection_error = any(
                        keyword in error_msg.lower() 
                        for keyword in connection_error_keywords
                    )
                    
                    if is_connection_error and attempt < max_retries:
                        logger.warning(f"[{task_name}] 检测到连接错误，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                    elif attempt >= max_retries:
                        logger.error(f"[{task_name}] 任务失败，已达到最大重试次数 ({max_retries})")
                        return None
                    else:
                        logger.error(f"[{task_name}] 任务遇到非连接错误，不再重试")
                        return None
            
            return None
        return wrapper
    return decorator


def create_task_result(
    success: bool = False,
    total_items: int = 0,
    successful_items: int = 0,
    failed_items: int = 0,
    error_items: list = None,
    processing_time: float = 0.0,
    additional_data: dict = None
) -> Dict[str, Any]:
    """
    创建标准化的任务执行结果
    
    Args:
        success: 任务是否成功
        total_items: 总处理项目数
        successful_items: 成功处理项目数
        failed_items: 失败处理项目数
        error_items: 错误项目列表
        processing_time: 处理时间
        additional_data: 额外数据
        
    Returns:
        Dict[str, Any]: 标准化的任务结果
    """
    result = {
        'success': success,
        'total_items': total_items,
        'successful_items': successful_items,
        'failed_items': failed_items,
        'error_items': error_items or [],
        'processing_time': processing_time,
        'timestamp': datetime.now().isoformat()
    }
    
    if additional_data:
        result.update(additional_data)
    
    return result


def log_task_summary(task_name: str, result: Dict[str, Any]) -> None:
    """
    记录任务执行摘要
    
    Args:
        task_name: 任务名称
        result: 任务执行结果
    """
    if result.get('success'):
        logger.info(f"[{task_name}] ✅ 任务执行成功")
        logger.info(f"[{task_name}]   成功: {result.get('successful_items', 0)}/{result.get('total_items', 0)}")
        logger.info(f"[{task_name}]   耗时: {result.get('processing_time', 0):.2f}秒")
        
        if result.get('error_items'):
            logger.warning(f"[{task_name}]   失败项目: {result['error_items']}")
    else:
        logger.error(f"[{task_name}] ❌ 任务执行失败")
        if result.get('error_items'):
            logger.error(f"[{task_name}]   失败项目: {result['error_items']}")


def check_database_connection() -> bool:
    """
    检查数据库连接状态
    
    Returns:
        bool: 连接是否正常
    """
    try:
        from global_const.global_const import pgsql_engine
        from sqlalchemy import text
        
        with pgsql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        logger.debug("[TASK_COMMON] 数据库连接检查通过")
        return True
        
    except Exception as e:
        logger.error(f"[TASK_COMMON] 数据库连接检查失败: {e}")
        return False


def get_time_range_for_task(hours_back: int = 1) -> tuple:
    """
    获取任务的时间范围
    
    Args:
        hours_back: 往前推的小时数
        
    Returns:
        tuple: (start_time, end_time)
    """
    from datetime import timedelta
    
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours_back)
    
    return start_time, end_time


def validate_room_ids(room_ids: list) -> list:
    """
    验证库房ID列表
    
    Args:
        room_ids: 库房ID列表
        
    Returns:
        list: 有效的库房ID列表
    """
    from global_const.const_config import MUSHROOM_ROOM_IDS
    
    valid_rooms = []
    for room_id in room_ids:
        if room_id in MUSHROOM_ROOM_IDS:
            valid_rooms.append(room_id)
        else:
            logger.warning(f"[TASK_COMMON] 无效的库房ID: {room_id}")
    
    return valid_rooms


class TaskExecutionContext:
    """任务执行上下文管理器"""
    
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.start_time = None
        self.end_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        logger.info(f"[{self.task_name}] 🚀 任务开始执行")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()
        
        if exc_type is None:
            logger.info(f"[{self.task_name}] ✅ 任务执行完成，耗时: {duration:.2f}秒")
        else:
            logger.error(f"[{self.task_name}] ❌ 任务执行异常，耗时: {duration:.2f}秒")
            logger.error(f"[{self.task_name}] 异常信息: {exc_val}")
    
    def get_duration(self) -> float:
        """获取执行时长"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0