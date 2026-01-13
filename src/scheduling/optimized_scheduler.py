"""
优化版调度器模块
基于APScheduler + Redis持久化的定时任务管理系统

核心功能：
1. Redis持久化调度器，支持重启恢复
2. 统一的任务管理和配置
3. 完善的错误处理和自动恢复
4. 优雅的退出机制
"""
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import NoReturn, Optional, Dict, Any

import pytz
from apscheduler import events
from apscheduler.job import Job
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.base import MaxInstancesReachedError
from apscheduler.schedulers import SchedulerAlreadyRunningError, SchedulerNotRunningError

# 本地模块导入
from global_const.global_const import settings
from utils.loguru_setting import logger
from utils.create_table import create_tables
from utils.env_data_processor import create_env_data_processor
from utils.exception_listener import exception_listener, set_scheduler_instance


# ===================== 独立任务函数（避免序列化问题） =====================
def safe_create_tables() -> None:
    """建表任务"""
    try:
        logger.info("[TASK] 开始执行建表任务")
        start_time = datetime.now()
        
        create_tables()
        
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"[TASK] 建表任务完成，耗时: {duration:.2f}秒")
        
    except Exception as e:
        logger.error(f"[TASK] 建表任务失败: {e}", exc_info=True)
        raise


def safe_daily_env_stats() -> None:
    """每日环境统计任务"""
    try:
        logger.info("[TASK] 开始执行每日环境统计")
        start_time = datetime.now()
        
        processor = create_env_data_processor()
        yesterday = datetime.now().date() - timedelta(days=1)
        processor.compute_and_store_daily_stats(
            datetime(yesterday.year, yesterday.month, yesterday.day)
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"[TASK] 每日环境统计完成，耗时: {duration:.2f}秒")
        
    except Exception as e:
        logger.error(f"[TASK] 每日环境统计失败: {e}", exc_info=True)
        raise


def safe_hourly_setpoint_monitoring() -> None:
    """每小时设定点变更监控任务"""
    try:
        logger.info("[TASK] 开始执行设定点变更监控")
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
        
    except Exception as e:
        logger.error(f"[TASK] 设定点变更监控失败: {e}", exc_info=True)
        raise


class OptimizedScheduler:
    """优化版调度器类"""
    
    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None
        self.is_shutting_down = False
        self.consecutive_failures = 0
        self.max_failures = 3
        
        # 配置参数
        self.timezone = self._get_local_timezone()
        self.misfire_grace_time = 300
        self.max_job_instances = 1
        self.create_tables_delay = 5
        self.main_loop_interval = 5
        
    def _get_local_timezone(self) -> timezone:
        """获取本地时区"""
        tz_str = os.environ.get('TZ')
        if tz_str:
            try:
                return pytz.timezone(tz_str)
            except pytz.UnknownTimeZoneError:
                logger.warning(f"Unknown timezone: {tz_str}, using system timezone")
        
        try:
            return datetime.now().astimezone().tzinfo or timezone.utc
        except Exception as e:
            logger.warning(f"Failed to get system timezone: {e}, using UTC")
            return timezone.utc
    
    def _create_redis_jobstore(self) -> RedisJobStore:
        """创建Redis任务存储"""
        return RedisJobStore(
            host=settings.redis.host,
            port=settings.redis.port,
            password=settings.redis.password,
            socket_timeout=10,
            socket_connect_timeout=5,
        )
    
    def _init_scheduler(self) -> BackgroundScheduler:
        """初始化调度器"""
        job_stores = {"default": self._create_redis_jobstore()}
        job_defaults = {
            "misfire_grace_time": self.misfire_grace_time,
            "max_instances": self.max_job_instances,
            "coalesce": True,
            "replace_existing": True,
        }
        
        try:
            scheduler = BackgroundScheduler(
                jobstores=job_stores,
                timezone=self.timezone,
                job_defaults=job_defaults,
            )
            
            # 注册事件监听器
            scheduler.add_listener(
                exception_listener,
                events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED
            )
            
            # 注册到健康检查模块
            set_scheduler_instance(scheduler)
            
            logger.info("[SCHEDULER] 调度器初始化完成")
            return scheduler
        except Exception as e:
            logger.error(f"[SCHEDULER] 调度器初始化失败: {e}", exc_info=True)
            raise
    
    def _add_create_tables_job(self) -> Job:
        """添加建表任务"""
        job = self.scheduler.add_job(
            func=safe_create_tables,  # 使用独立函数
            id="create_tables",
            next_run_time=datetime.now(self.timezone),
            replace_existing=True,  # 明确指定替换已存在的任务
        )
        logger.info("[SCHEDULER] 建表任务已添加")
        return job
    
    def _add_business_jobs(self) -> None:
        """添加业务任务"""
        # 每日环境统计任务（01:03:20执行）
        self.scheduler.add_job(
            func=safe_daily_env_stats,  # 使用独立函数
            trigger=CronTrigger(hour=1, minute=3, second=20, timezone=self.timezone),
            id="daily_env_stats",
            replace_existing=True,  # 明确指定替换已存在的任务
        )
        logger.info("[SCHEDULER] 每日环境统计任务已添加")
        
        # 每小时设定点变更监控任务（每小时的第5分钟执行）
        self.scheduler.add_job(
            func=safe_hourly_setpoint_monitoring,  # 使用独立函数
            trigger=CronTrigger(minute=5, timezone=self.timezone),
            id="hourly_setpoint_monitoring",
            replace_existing=True,  # 明确指定替换已存在的任务
        )
        logger.info("[SCHEDULER] 每小时设定点监控任务已添加")
    
    def _setup_jobs(self) -> None:
        """设置所有任务"""
        # 1. 添加建表任务
        self._add_create_tables_job()
        
        # 2. 等待建表完成
        logger.info(f"[SCHEDULER] 等待建表完成，延迟 {self.create_tables_delay} 秒")
        time.sleep(self.create_tables_delay)
        
        # 3. 添加业务任务
        self._add_business_jobs()
        
        # 4. 显示任务信息
        jobs = self.scheduler.get_jobs()
        logger.info(f"[SCHEDULER] 总共添加了 {len(jobs)} 个任务")
        for job in jobs:
            try:
                if hasattr(job, 'next_run_time') and job.next_run_time:
                    next_run = job.next_run_time.isoformat()
                elif hasattr(job, 'trigger') and job.trigger:
                    next_run = f"触发器: {job.trigger}"
                else:
                    next_run = "一次性任务"
            except Exception:
                next_run = "未知"
            
            logger.info(f"  - {job.id}: {next_run}")
    
    def _handle_shutdown(self, signal_num: int, frame) -> NoReturn:
        """处理关闭信号"""
        if self.is_shutting_down:
            logger.warning("[SCHEDULER] 已在关闭过程中")
            return
        
        self.is_shutting_down = True
        signal_name = signal.Signals(signal_num).name
        logger.info(f"[SCHEDULER] 收到退出信号: {signal_name}")
        
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=True)
                logger.info("[SCHEDULER] 调度器已停止")
            except Exception as e:
                logger.error(f"[SCHEDULER] 调度器停止失败: {e}", exc_info=True)
        
        logger.info("[SCHEDULER] 程序退出")
        sys.exit(0)
    
    def _register_signal_handlers(self) -> None:
        """注册信号处理器"""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        logger.info("[SCHEDULER] 信号处理器已注册")
    
    def _start_scheduler(self) -> None:
        """启动调度器"""
        try:
            self.scheduler.start()
            logger.info("[SCHEDULER] 调度器启动成功")
        except SchedulerAlreadyRunningError as e:
            logger.error(f"[SCHEDULER] 调度器启动失败: {e}", exc_info=True)
            raise
    
    def _run_main_loop(self) -> None:
        """运行主循环"""
        logger.info("[SCHEDULER] 进入主循环")
        
        while not self.is_shutting_down:
            try:
                if not self.scheduler.running:
                    self.consecutive_failures += 1
                    logger.error(f"[SCHEDULER] 调度器停止 (失败: {self.consecutive_failures}/{self.max_failures})")
                    
                    if self.consecutive_failures >= self.max_failures:
                        logger.critical("[SCHEDULER] 连续失败过多，退出")
                        break
                    
                    self.scheduler.start()
                    logger.info("[SCHEDULER] 调度器重启成功")
                else:
                    # 重置失败计数
                    if self.consecutive_failures > 0:
                        self.consecutive_failures = 0
                        logger.info("[SCHEDULER] 调度器状态恢复正常")
                
                time.sleep(self.main_loop_interval)
                
            except SchedulerAlreadyRunningError:
                # 调度器已运行，正常情况
                self.consecutive_failures = 0
                time.sleep(self.main_loop_interval)
            except KeyboardInterrupt:
                logger.info("[SCHEDULER] 收到键盘中断")
                self._handle_shutdown(signal.SIGINT, None)
            except Exception as e:
                self.consecutive_failures += 1
                logger.error(f"[SCHEDULER] 主循环异常: {e}", exc_info=True)
                
                if self.consecutive_failures >= self.max_failures:
                    logger.critical("[SCHEDULER] 主循环异常过多，退出")
                    break
                
                time.sleep(self.main_loop_interval)
        
        logger.info("[SCHEDULER] 主循环结束")
        self._handle_shutdown(signal.SIGTERM, None)
    
    def run(self) -> NoReturn:
        """运行调度器"""
        logger.info("[SCHEDULER] === 优化版调度器启动 ===")
        
        try:
            # 初始化调度器
            self.scheduler = self._init_scheduler()
            
            # 注册信号处理器
            self._register_signal_handlers()
            
            # 设置任务
            self._setup_jobs()
            
            # 启动调度器
            self._start_scheduler()
            
            # 运行主循环
            self._run_main_loop()
            
        except KeyboardInterrupt:
            logger.info("[SCHEDULER] 收到键盘中断，程序退出")
        except Exception as e:
            logger.critical(f"[SCHEDULER] 调度器运行异常: {e}", exc_info=True)
            raise
        finally:
            logger.info("[SCHEDULER] 调度器程序结束")


def main() -> NoReturn:
    """程序入口点"""
    scheduler = OptimizedScheduler()
    scheduler.run()


if __name__ == "__main__":
    main()