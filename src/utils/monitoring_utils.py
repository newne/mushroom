"""
监控工具类

提供系统监控、性能分析、健康检查等通用功能。
"""

import psutil
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from utils.loguru_setting import logger
from utils.database_utils import get_database_manager


class SystemMonitor:
    """系统监控器"""
    
    def __init__(self):
        """初始化系统监控器"""
        self.db_manager = get_database_manager()
    
    def get_system_info(self) -> Dict[str, Any]:
        """
        获取系统信息
        
        Returns:
            Dict[str, Any]: 系统信息
        """
        try:
            # CPU信息
            cpu_info = {
                'usage_percent': psutil.cpu_percent(interval=1),
                'count': psutil.cpu_count(),
                'count_logical': psutil.cpu_count(logical=True)
            }
            
            # 内存信息
            memory = psutil.virtual_memory()
            memory_info = {
                'total': memory.total,
                'available': memory.available,
                'used': memory.used,
                'usage_percent': memory.percent
            }
            
            # 磁盘信息
            disk = psutil.disk_usage('/')
            disk_info = {
                'total': disk.total,
                'used': disk.used,
                'free': disk.free,
                'usage_percent': (disk.used / disk.total) * 100
            }
            
            # 网络信息
            network = psutil.net_io_counters()
            network_info = {
                'bytes_sent': network.bytes_sent,
                'bytes_recv': network.bytes_recv,
                'packets_sent': network.packets_sent,
                'packets_recv': network.packets_recv
            }
            
            return {
                'timestamp': datetime.now().isoformat(),
                'cpu': cpu_info,
                'memory': memory_info,
                'disk': disk_info,
                'network': network_info
            }
            
        except Exception as e:
            logger.error(f"[SYS_MONITOR] 获取系统信息失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
    
    def get_process_info(self, process_name: str = None) -> Dict[str, Any]:
        """
        获取进程信息
        
        Args:
            process_name: 进程名称（可选）
            
        Returns:
            Dict[str, Any]: 进程信息
        """
        try:
            current_process = psutil.Process()
            
            process_info = {
                'pid': current_process.pid,
                'name': current_process.name(),
                'status': current_process.status(),
                'cpu_percent': current_process.cpu_percent(),
                'memory_info': current_process.memory_info()._asdict(),
                'memory_percent': current_process.memory_percent(),
                'create_time': datetime.fromtimestamp(current_process.create_time()).isoformat(),
                'num_threads': current_process.num_threads()
            }
            
            # 如果指定了进程名称，查找相关进程
            if process_name:
                related_processes = []
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                    try:
                        if process_name.lower() in proc.info['name'].lower():
                            related_processes.append(proc.info)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                
                process_info['related_processes'] = related_processes
            
            return {
                'timestamp': datetime.now().isoformat(),
                'current_process': process_info
            }
            
        except Exception as e:
            logger.error(f"[SYS_MONITOR] 获取进程信息失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
    
    def get_disk_usage(self, paths: List[str] = None) -> Dict[str, Any]:
        """
        获取磁盘使用情况
        
        Args:
            paths: 要检查的路径列表
            
        Returns:
            Dict[str, Any]: 磁盘使用情况
        """
        try:
            if paths is None:
                paths = ['/']
            
            disk_usage = {}
            
            for path in paths:
                if Path(path).exists():
                    usage = psutil.disk_usage(path)
                    disk_usage[path] = {
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'usage_percent': (usage.used / usage.total) * 100
                    }
                else:
                    disk_usage[path] = {'error': 'Path does not exist'}
            
            return {
                'timestamp': datetime.now().isoformat(),
                'disk_usage': disk_usage
            }
            
        except Exception as e:
            logger.error(f"[SYS_MONITOR] 获取磁盘使用情况失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }


class TaskMonitor:
    """任务监控器"""
    
    def __init__(self):
        """初始化任务监控器"""
        self.db_manager = get_database_manager()
    
    def get_task_execution_stats(self, hours: int = 24) -> Dict[str, Any]:
        """
        获取任务执行统计
        
        Args:
            hours: 统计时间范围（小时）
            
        Returns:
            Dict[str, Any]: 任务执行统计
        """
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            stats = {
                'period': f"{start_time} to {end_time}",
                'hours': hours,
                'table_management': self._get_table_stats(),
                'env_stats': self._get_env_stats(start_time, end_time),
                'setpoint_monitoring': self._get_setpoint_stats(start_time, end_time),
                'clip_inference': self._get_clip_stats(start_time, end_time),
                'decision_analysis': self._get_decision_stats(start_time, end_time)
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取任务执行统计失败: {e}")
            return {
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    def _get_table_stats(self) -> Dict[str, Any]:
        """获取表管理统计"""
        try:
            important_tables = [
                'mushroom_embedding',
                'mushroom_env_daily_stats',
                'device_setpoint_changes',
                'decision_analysis_static_config',
                'decision_analysis_dynamic_result'
            ]
            
            table_stats = {}
            total_records = 0
            
            for table_name in important_tables:
                table_info = self.db_manager.get_table_info(table_name)
                record_count = table_info.get('record_count', 0)
                table_stats[table_name] = {
                    'exists': table_info['exists'],
                    'record_count': record_count
                }
                total_records += record_count
            
            return {
                'table_count': len([t for t in table_stats.values() if t['exists']]),
                'total_records': total_records,
                'table_details': table_stats
            }
            
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取表统计失败: {e}")
            return {'error': str(e)}
    
    def _get_env_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """获取环境统计任务统计"""
        try:
            query = """
                SELECT 
                    COUNT(*) as record_count,
                    COUNT(DISTINCT room_id) as room_count,
                    AVG(temp_median) as avg_temp,
                    AVG(humidity_median) as avg_humidity
                FROM mushroom_env_daily_stats 
                WHERE stat_date BETWEEN :start_date AND :end_date
            """
            
            result = self.db_manager.execute_query(
                query, 
                {
                    'start_date': start_time.date(),
                    'end_date': end_time.date()
                },
                fetch_all=False
            )
            
            if result:
                return {
                    'record_count': result[0],
                    'room_count': result[1],
                    'avg_temperature': round(result[2], 2) if result[2] else None,
                    'avg_humidity': round(result[3], 2) if result[3] else None
                }
            else:
                return {'record_count': 0}
                
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取环境统计失败: {e}")
            return {'error': str(e)}
    
    def _get_setpoint_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """获取设定点监控统计"""
        try:
            query = """
                SELECT 
                    COUNT(*) as change_count,
                    COUNT(DISTINCT room_id) as affected_rooms,
                    COUNT(DISTINCT device_name) as affected_devices
                FROM device_setpoint_changes 
                WHERE change_time BETWEEN :start_time AND :end_time
            """
            
            result = self.db_manager.execute_query(
                query,
                {
                    'start_time': start_time,
                    'end_time': end_time
                },
                fetch_all=False
            )
            
            if result:
                return {
                    'change_count': result[0],
                    'affected_rooms': result[1],
                    'affected_devices': result[2]
                }
            else:
                return {'change_count': 0}
                
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取设定点统计失败: {e}")
            return {'error': str(e)}
    
    def _get_clip_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """获取CLIP推理统计"""
        try:
            query = """
                SELECT 
                    COUNT(*) as inference_count,
                    COUNT(DISTINCT mushroom_id) as room_count,
                    COUNT(CASE WHEN image_embedding IS NOT NULL THEN 1 END) as with_embedding,
                    AVG(image_quality_index) as avg_quality
                FROM mushroom_embedding 
                WHERE created_at BETWEEN :start_time AND :end_time
            """
            
            result = self.db_manager.execute_query(
                query,
                {
                    'start_time': start_time,
                    'end_time': end_time
                },
                fetch_all=False
            )
            
            if result:
                return {
                    'inference_count': result[0],
                    'room_count': result[1],
                    'with_embedding': result[2],
                    'embedding_rate': (result[2] / result[0]) * 100 if result[0] > 0 else 0,
                    'avg_quality': round(result[3], 3) if result[3] else None
                }
            else:
                return {'inference_count': 0}
                
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取CLIP统计失败: {e}")
            return {'error': str(e)}
    
    def _get_decision_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """获取决策分析统计"""
        try:
            query = """
                SELECT 
                    COUNT(DISTINCT batch_id) as analysis_batches,
                    COUNT(*) as total_results,
                    COUNT(DISTINCT room_id) as room_count,
                    COUNT(CASE WHEN action_type = 'adjust' THEN 1 END) as adjust_actions
                FROM decision_analysis_dynamic_result 
                WHERE analysis_time BETWEEN :start_time AND :end_time
            """
            
            result = self.db_manager.execute_query(
                query,
                {
                    'start_time': start_time,
                    'end_time': end_time
                },
                fetch_all=False
            )
            
            if result:
                return {
                    'analysis_batches': result[0],
                    'total_results': result[1],
                    'room_count': result[2],
                    'adjust_actions': result[3],
                    'avg_results_per_batch': result[1] / result[0] if result[0] > 0 else 0
                }
            else:
                return {'analysis_batches': 0}
                
        except Exception as e:
            logger.error(f"[TASK_MONITOR] 获取决策分析统计失败: {e}")
            return {'error': str(e)}


class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        """初始化健康检查器"""
        self.system_monitor = SystemMonitor()
        self.task_monitor = TaskMonitor()
        self.db_manager = get_database_manager()
    
    def perform_health_check(self) -> Dict[str, Any]:
        """
        执行全面健康检查
        
        Returns:
            Dict[str, Any]: 健康检查结果
        """
        try:
            logger.info("[HEALTH_CHECK] 开始执行系统健康检查")
            
            health_status = {
                'timestamp': datetime.now().isoformat(),
                'overall_healthy': True,
                'checks': {}
            }
            
            # 1. 系统资源检查
            logger.info("[HEALTH_CHECK] 检查系统资源...")
            system_info = self.system_monitor.get_system_info()
            
            system_healthy = True
            if 'error' not in system_info:
                # 检查CPU使用率
                if system_info['cpu']['usage_percent'] > 90:
                    system_healthy = False
                
                # 检查内存使用率
                if system_info['memory']['usage_percent'] > 90:
                    system_healthy = False
                
                # 检查磁盘使用率
                if system_info['disk']['usage_percent'] > 90:
                    system_healthy = False
            else:
                system_healthy = False
            
            health_status['checks']['system'] = {
                'healthy': system_healthy,
                'details': system_info
            }
            
            # 2. 数据库连接检查
            logger.info("[HEALTH_CHECK] 检查数据库连接...")
            db_healthy = self.db_manager.check_connection()
            
            health_status['checks']['database'] = {
                'healthy': db_healthy,
                'connection_status': 'OK' if db_healthy else 'FAILED'
            }
            
            # 3. 任务执行状态检查
            logger.info("[HEALTH_CHECK] 检查任务执行状态...")
            task_stats = self.task_monitor.get_task_execution_stats(hours=1)
            
            task_healthy = True
            if 'error' in task_stats:
                task_healthy = False
            
            health_status['checks']['tasks'] = {
                'healthy': task_healthy,
                'details': task_stats
            }
            
            # 4. 文件系统检查
            logger.info("[HEALTH_CHECK] 检查文件系统...")
            important_paths = [
                '/tmp',
                './logs',
                './output',
                './data'
            ]
            
            disk_usage = self.system_monitor.get_disk_usage(important_paths)
            
            filesystem_healthy = True
            if 'error' in disk_usage:
                filesystem_healthy = False
            
            health_status['checks']['filesystem'] = {
                'healthy': filesystem_healthy,
                'details': disk_usage
            }
            
            # 更新总体健康状态
            health_status['overall_healthy'] = all([
                system_healthy,
                db_healthy,
                task_healthy,
                filesystem_healthy
            ])
            
            logger.info(f"[HEALTH_CHECK] 健康检查完成，总体状态: {'健康' if health_status['overall_healthy'] else '异常'}")
            
            return health_status
            
        except Exception as e:
            logger.error(f"[HEALTH_CHECK] 健康检查失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'overall_healthy': False,
                'error': str(e)
            }
    
    def get_performance_metrics(self, hours: int = 1) -> Dict[str, Any]:
        """
        获取性能指标
        
        Args:
            hours: 统计时间范围（小时）
            
        Returns:
            Dict[str, Any]: 性能指标
        """
        try:
            metrics = {
                'timestamp': datetime.now().isoformat(),
                'period_hours': hours,
                'system_metrics': self.system_monitor.get_system_info(),
                'task_metrics': self.task_monitor.get_task_execution_stats(hours),
                'database_metrics': {
                    'connection_healthy': self.db_manager.check_connection()
                }
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"[HEALTH_CHECK] 获取性能指标失败: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }


# 创建全局实例
system_monitor = SystemMonitor()
task_monitor = TaskMonitor()
health_checker = HealthChecker()


def get_system_monitor() -> SystemMonitor:
    """获取系统监控器实例"""
    return system_monitor


def get_task_monitor() -> TaskMonitor:
    """获取任务监控器实例"""
    return task_monitor


def get_health_checker() -> HealthChecker:
    """获取健康检查器实例"""
    return health_checker


def quick_health_check() -> bool:
    """
    快速健康检查
    
    Returns:
        bool: 系统是否健康
    """
    try:
        # 检查数据库连接
        db_ok = get_database_manager().check_connection()
        
        # 检查系统资源
        system_info = system_monitor.get_system_info()
        system_ok = 'error' not in system_info
        
        return db_ok and system_ok
        
    except Exception as e:
        logger.error(f"[QUICK_HEALTH] 快速健康检查失败: {e}")
        return False