"""
数据库表管理任务模块

负责数据库表的创建、维护和管理相关任务。
"""

import time
from datetime import datetime

from utils.loguru_setting import logger
from utils.create_table import create_tables
from global_const.const_config import (
    TABLE_CREATION_MAX_RETRIES,
    TABLE_CREATION_RETRY_DELAY,
)


def safe_create_tables() -> None:
    """
    安全创建数据库表任务（带重试机制）
    
    功能：
    - 创建所有必要的数据库表
    - 包含完整的错误处理和重试机制
    - 确保表创建失败不会影响调度器运行
    """
    max_retries = getattr(globals().get('TABLE_CREATION_MAX_RETRIES'), 'value', 3)
    retry_delay = getattr(globals().get('TABLE_CREATION_RETRY_DELAY'), 'value', 5)
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[TABLE_TASK] 开始创建数据库表 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            # 执行建表操作
            create_tables()
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[TABLE_TASK] 数据库表创建完成，耗时: {duration:.2f}秒")
            
            # 成功执行，退出重试循环
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TABLE_TASK] 数据库表创建失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            # 检查是否是数据库连接错误
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[TABLE_TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[TABLE_TASK] 数据库表创建失败，已达到最大重试次数 ({max_retries})")
                # 不再抛出异常，避免调度器崩溃
                return
            else:
                # 非连接错误，不重试
                logger.error(f"[TABLE_TASK] 数据库表创建遇到非连接错误，不再重试")
                return


def get_table_creation_status() -> dict:
    """
    获取表创建状态
    
    Returns:
        dict: 包含表创建状态信息的字典
    """
    try:
        from global_const.global_const import pgsql_engine
        from sqlalchemy import text
        
        # 检查主要表是否存在
        tables_to_check = [
            'mushroom_embedding',
            'mushroom_env_daily_stats', 
            'device_setpoint_changes',
            'decision_analysis_static_config',
            'decision_analysis_dynamic_result'
        ]
        
        existing_tables = []
        missing_tables = []
        
        with pgsql_engine.connect() as conn:
            for table_name in tables_to_check:
                result = conn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
                ), {"table_name": table_name})
                
                if result.scalar():
                    existing_tables.append(table_name)
                else:
                    missing_tables.append(table_name)
        
        return {
            'total_tables': len(tables_to_check),
            'existing_tables': existing_tables,
            'missing_tables': missing_tables,
            'all_tables_exist': len(missing_tables) == 0,
            'check_time': datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"[TABLE_TASK] 检查表状态失败: {e}")
        return {
            'error': str(e),
            'check_time': datetime.now().isoformat()
        }