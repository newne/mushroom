"""
基础任务执行类

提供所有定时任务的基础功能和通用接口。
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional
from functools import wraps

from utils.loguru_setting import logger


class BaseTask(ABC):
    """基础任务执行类"""
    
    def __init__(self, task_name: str, max_retries: int = 3, retry_delay: int = 5):
        """
        初始化基础任务
        
        Args:
            task_name: 任务名称
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.task_name = task_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.connection_error_keywords = [
            'timeout', 'connection', 'connect', 'database', 'server'
        ]
    
    @abstractmethod
    def execute_task(self) -> Dict[str, Any]:
        """
        执行具体任务逻辑（子类必须实现）
        
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        pass
    
    def run(self) -> Dict[str, Any]:
        """
        运行任务（带重试机制）
        
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"[{self.task_name}] 开始执行任务 (尝试 {attempt}/{self.max_retries})")
                start_time = datetime.now()
                
                # 执行具体任务
                result = self.execute_task()
                
                duration = (datetime.now() - start_time).total_seconds()
                logger.info(f"[{self.task_name}] 任务执行完成，耗时: {duration:.2f}秒")
                
                # 添加执行信息到结果
                if isinstance(result, dict):
                    result['execution_time'] = duration
                    result['attempt'] = attempt
                
                return result
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[{self.task_name}] 任务执行失败 (尝试 {attempt}/{self.max_retries}): {error_msg}")
                
                # 检查是否是连接错误
                is_connection_error = any(
                    keyword in error_msg.lower() 
                    for keyword in self.connection_error_keywords
                )
                
                if is_connection_error and attempt < self.max_retries:
                    logger.warning(f"[{self.task_name}] 检测到连接错误，{self.retry_delay}秒后重试...")
                    time.sleep(self.retry_delay)
                elif attempt >= self.max_retries:
                    logger.error(f"[{self.task_name}] 任务失败，已达到最大重试次数 ({self.max_retries})")
                    return self._create_error_result(error_msg, attempt)
                else:
                    logger.error(f"[{self.task_name}] 任务遇到非连接错误，不再重试")
                    return self._create_error_result(error_msg, attempt)
        
        return self._create_error_result("未知错误", self.max_retries)
    
    def _create_error_result(self, error_msg: str, attempt: int) -> Dict[str, Any]:
        """
        创建错误结果
        
        Args:
            error_msg: 错误信息
            attempt: 尝试次数
            
        Returns:
            Dict[str, Any]: 错误结果
        """
        return {
            'success': False,
            'error': error_msg,
            'attempt': attempt,
            'task_name': self.task_name,
            'timestamp': datetime.now().isoformat()
        }
    
    def _create_success_result(self, **kwargs) -> Dict[str, Any]:
        """
        创建成功结果
        
        Args:
            **kwargs: 额外的结果数据
            
        Returns:
            Dict[str, Any]: 成功结果
        """
        result = {
            'success': True,
            'task_name': self.task_name,
            'timestamp': datetime.now().isoformat()
        }
        result.update(kwargs)
        return result


class TaskExecutor:
    """任务执行器 - 提供任务执行的统一接口"""
    
    @staticmethod
    def execute_with_retry(
        task_func,
        task_name: str,
        max_retries: int = 3,
        retry_delay: int = 5,
        **kwargs
    ) -> Dict[str, Any]:
        """
        执行任务（带重试机制）
        
        Args:
            task_func: 任务函数
            task_name: 任务名称
            max_retries: 最大重试次数
            retry_delay: 重试延迟
            **kwargs: 任务函数参数
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        connection_error_keywords = [
            'timeout', 'connection', 'connect', 'database', 'server'
        ]
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[{task_name}] 开始执行任务 (尝试 {attempt}/{max_retries})")
                start_time = datetime.now()
                
                # 执行任务
                result = task_func(**kwargs)
                
                duration = (datetime.now() - start_time).total_seconds()
                logger.info(f"[{task_name}] 任务执行完成，耗时: {duration:.2f}秒")
                
                return {
                    'success': True,
                    'result': result,
                    'execution_time': duration,
                    'attempt': attempt,
                    'task_name': task_name
                }
                
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
                    return {
                        'success': False,
                        'error': error_msg,
                        'attempt': attempt,
                        'task_name': task_name
                    }
                else:
                    logger.error(f"[{task_name}] 任务遇到非连接错误，不再重试")
                    return {
                        'success': False,
                        'error': error_msg,
                        'attempt': attempt,
                        'task_name': task_name
                    }
        
        return {
            'success': False,
            'error': "未知错误",
            'attempt': max_retries,
            'task_name': task_name
        }


def task_wrapper(task_name: str, max_retries: int = 3, retry_delay: int = 5):
    """
    任务装饰器 - 为任务函数添加重试机制
    
    Args:
        task_name: 任务名称
        max_retries: 最大重试次数
        retry_delay: 重试延迟
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return TaskExecutor.execute_with_retry(
                func, task_name, max_retries, retry_delay, *args, **kwargs
            )
        return wrapper
    return decorator