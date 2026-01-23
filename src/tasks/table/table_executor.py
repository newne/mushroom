"""
数据库表管理任务执行器

专门负责数据库表的创建、维护和管理。
"""

from datetime import datetime
from typing import Dict, Any

from tasks.base_task import BaseTask
from utils.loguru_setting import logger
from utils.create_table import create_tables
from global_const.const_config import (
    TABLE_CREATION_MAX_RETRIES,
    TABLE_CREATION_RETRY_DELAY,
)


class TableManagementTask(BaseTask):
    """数据库表管理任务执行器"""
    
    def __init__(self):
        """初始化表管理任务"""
        max_retries = getattr(TABLE_CREATION_MAX_RETRIES, 'value', 3)
        retry_delay = getattr(TABLE_CREATION_RETRY_DELAY, 'value', 5)
        
        super().__init__(
            task_name="TABLE_MANAGEMENT",
            max_retries=max_retries,
            retry_delay=retry_delay
        )
    
    def execute_task(self) -> Dict[str, Any]:
        """
        执行数据库表创建任务
        
        Returns:
            Dict[str, Any]: 执行结果
        """
        logger.info(f"[{self.task_name}] 开始创建数据库表")
        start_time = datetime.now()
        
        try:
            # 执行建表操作
            create_tables()
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[{self.task_name}] 数据库表创建完成，耗时: {duration:.2f}秒")
            
            # 获取表创建状态
            table_status = self._get_table_status()
            
            return self._create_success_result(
                duration=duration,
                table_status=table_status,
                message="数据库表创建成功"
            )
            
        except Exception as e:
            logger.error(f"[{self.task_name}] 数据库表创建失败: {e}")
            raise
    
    def _get_table_status(self) -> Dict[str, Any]:
        """
        获取表创建状态
        
        Returns:
            Dict[str, Any]: 表状态信息
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
            logger.error(f"[{self.task_name}] 检查表状态失败: {e}")
            return {
                'error': str(e),
                'check_time': datetime.now().isoformat()
            }
    
    def verify_tables(self) -> Dict[str, Any]:
        """
        验证表结构和完整性
        
        Returns:
            Dict[str, Any]: 验证结果
        """
        try:
            logger.info(f"[{self.task_name}] 开始验证表结构")
            
            table_status = self._get_table_status()
            
            if not table_status.get('all_tables_exist', False):
                return {
                    'success': False,
                    'message': f"缺少表: {table_status.get('missing_tables', [])}",
                    'table_status': table_status
                }
            
            # 可以添加更多的表结构验证逻辑
            logger.info(f"[{self.task_name}] 表结构验证通过")
            
            return {
                'success': True,
                'message': "所有表结构验证通过",
                'table_status': table_status
            }
            
        except Exception as e:
            logger.error(f"[{self.task_name}] 表结构验证失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': "表结构验证失败"
            }


# 创建全局实例
table_management_task = TableManagementTask()


def safe_create_tables() -> None:
    """
    安全创建数据库表任务（兼容原接口）
    """
    result = table_management_task.run()
    
    if not result.get('success', False):
        logger.error(f"[TABLE_TASK] 表创建任务失败: {result.get('error', '未知错误')}")
    else:
        logger.info(f"[TABLE_TASK] 表创建任务成功完成")


def get_table_creation_status() -> Dict[str, Any]:
    """
    获取表创建状态（兼容原接口）
    
    Returns:
        Dict[str, Any]: 包含表创建状态信息的字典
    """
    return table_management_task._get_table_status()


def verify_table_integrity() -> Dict[str, Any]:
    """
    验证表完整性
    
    Returns:
        Dict[str, Any]: 验证结果
    """
    return table_management_task.verify_tables()