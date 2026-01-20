"""
监控任务模块
负责设定点变更监控等监控相关任务
"""

import time
from datetime import datetime, timedelta

from utils.loguru_setting import logger


def safe_hourly_setpoint_monitoring() -> None:
    """每小时设定点变更监控任务（带数据库连接重试）"""
    max_retries = 3
    retry_delay = 5  # 秒
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[TASK] 开始执行设定点变更监控 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            # 导入批量监控函数
            from utils.setpoint_change_monitor import batch_monitor_setpoint_changes
            
            # 设定监控时间范围（最近1小时）
            end_time = datetime.now()
            monitor_start_time = end_time - timedelta(hours=1)
            
            logger.info(f"[TASK] 监控时间范围: {monitor_start_time} ~ {end_time}")
            
            # 执行批量监控
            result = batch_monitor_setpoint_changes(
                start_time=monitor_start_time,
                end_time=end_time,
                store_results=True
            )
            
            # 记录执行结果
            if result['success']:
                logger.info(f"[TASK] 设定点监控完成: 处理 {result['successful_rooms']}/{result['total_rooms']} 个库房")
                logger.info(f"[TASK] 检测到 {result['total_changes']} 个设定点变更，存储 {result['stored_records']} 条记录")
                
                # 记录有变更的库房
                changed_rooms = [room_id for room_id, count in result['changes_by_room'].items() if count > 0]
                if changed_rooms:
                    logger.info(f"[TASK] 有变更的库房: {changed_rooms}")
                
                if result['error_rooms']:
                    logger.warning(f"[TASK] 处理失败的库房: {result['error_rooms']}")
            else:
                logger.error("[TASK] 设定点监控执行失败")
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[TASK] 设定点变更监控完成，耗时: {duration:.2f}秒")
            
            # 成功执行，退出重试循环
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TASK] 设定点变更监控失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            # 检查是否是数据库连接错误
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[TASK] 设定点监控任务失败，已达到最大重试次数 ({max_retries})")
                # 不再抛出异常，避免调度器崩溃
                return
            else:
                # 非连接错误，不重试
                logger.error(f"[TASK] 设定点监控任务遇到非连接错误，不再重试")
                return