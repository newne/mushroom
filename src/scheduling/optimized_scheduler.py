"""
优化版调度器模块
基于APScheduler + Redis持久化的定时任务管理系统

核心功能：
1. Redis持久化调度器，支持重启恢复
2. 统一的任务管理和配置
3. 完善的错误处理和自动恢复
4. 优雅的退出机制

定时任务：
1. 建表任务：启动时立即执行
2. 每日环境统计：每天 01:03:20 执行
3. 每小时设定点监控：每小时第5分钟执行
4. 每小时CLIP推理：每小时第25分钟执行最近1小时图像数据
5. 决策分析任务：每天 10:00, 12:00, 14:00 为每个蘑菇房执行
"""

# 标准库导入
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import NoReturn, Optional

# 第三方库导入
import pytz
import sqlalchemy
from apscheduler import events
from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.base import MaxInstancesReachedError
from apscheduler.schedulers import SchedulerAlreadyRunningError, SchedulerNotRunningError

# 本地模块导入
from global_const.global_const import settings, pgsql_engine
from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    DECISION_ANALYSIS_SCHEDULE_TIMES,
)
from utils.loguru_setting import logger
from utils.exception_listener import exception_listener, set_scheduler_instance

# 任务模块导入
from tasks import (
    safe_create_tables,
    safe_daily_env_stats,
    safe_hourly_setpoint_monitoring,
    safe_hourly_clip_inference,
    safe_enhanced_decision_analysis_10_00,
    safe_enhanced_decision_analysis_12_00,
    safe_enhanced_decision_analysis_14_00,
)


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
    
    def _init_scheduler(self) -> BackgroundScheduler:
        """初始化调度器"""
        # 使用内存存储而非Redis（任务配置固定，无需持久化）
        # 如果需要持久化，可以添加Redis服务并使用 RedisJobStore
        job_defaults = {
            "misfire_grace_time": self.misfire_grace_time,
            "max_instances": self.max_job_instances,
            "coalesce": True,
            "replace_existing": True,
        }
        
        try:
            scheduler = BackgroundScheduler(
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
            
            logger.info("[SCHEDULER] 调度器初始化完成（使用内存存储）")
            return scheduler
        except Exception as e:
            logger.error(f"[SCHEDULER] 调度器初始化失败: {e}", exc_info=True)
            raise
    
    def _add_business_jobs(self) -> None:
        """添加业务任务"""
        # 每日环境统计任务（01:03:20执行）
        self.scheduler.add_job(
            func=safe_daily_env_stats,
            trigger=CronTrigger(hour=1, minute=3, second=20, timezone=self.timezone),
            id="daily_env_stats",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 每日环境统计任务已添加")
        
        # 每小时设定点变更监控任务（每小时的第5分钟执行）
        self.scheduler.add_job(
            func=safe_hourly_setpoint_monitoring,
            trigger=CronTrigger(minute=5, timezone=self.timezone),
            id="hourly_setpoint_monitoring",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 每小时设定点监控任务已添加")
        
        # 每小时CLIP推理任务（每小时第25分钟执行）
        self.scheduler.add_job(
            func=safe_hourly_clip_inference,
            trigger=CronTrigger(minute=25, timezone=self.timezone),
            id="hourly_clip_inference",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 每小时CLIP推理任务已添加 (每小时第25分钟执行)")
        
        # ==================== 增强决策分析定时任务 ====================
        # 每天 10:00, 12:00, 14:00 为所有蘑菇房执行增强决策分析
        # 增强功能: 多图像分析, 结构化参数调整, 风险评估
        
        # 10:00 增强决策分析任务
        self.scheduler.add_job(
            func=safe_enhanced_decision_analysis_10_00,
            trigger=CronTrigger(hour=10, minute=0, second=0, timezone=self.timezone),
            id="enhanced_decision_analysis_10_00",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 增强决策分析任务已添加 (每天 10:00 执行)")
        
        # 12:00 增强决策分析任务
        self.scheduler.add_job(
            func=safe_enhanced_decision_analysis_12_00,
            trigger=CronTrigger(hour=12, minute=0, second=0, timezone=self.timezone),
            id="enhanced_decision_analysis_12_00",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 增强决策分析任务已添加 (每天 12:00 执行)")
        
        # 14:00 增强决策分析任务
        self.scheduler.add_job(
            func=safe_enhanced_decision_analysis_14_00,
            trigger=CronTrigger(hour=14, minute=0, second=0, timezone=self.timezone),
            id="enhanced_decision_analysis_14_00",
            replace_existing=True,
        )
        logger.info("[SCHEDULER] 增强决策分析任务已添加 (每天 14:00 执行)")
        
        logger.info(
            f"[SCHEDULER] 增强决策分析任务配置: 库房={MUSHROOM_ROOM_IDS}, "
            f"时间点={[f'{h:02d}:{m:02d}' for h, m in DECISION_ANALYSIS_SCHEDULE_TIMES]}, "
            f"增强功能=多图像分析+结构化参数调整+风险评估"
        )
    
    def _setup_jobs(self) -> None:
        """设置所有任务"""
        # 添加业务任务
        self._add_business_jobs()
        
        # 显示任务信息
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
        """运行调度器（带数据库连接重试）"""
        logger.info("[SCHEDULER] === 优化版调度器启动 ===")
        
        max_init_retries = 5
        init_retry_delay = 10
        
        # 初始化阶段的重试逻辑
        for init_attempt in range(1, max_init_retries + 1):
            try:
                logger.info(f"[SCHEDULER] 初始化调度器 (尝试 {init_attempt}/{max_init_retries})")
                
                # 在初始化前先测试数据库连接并执行建表
                logger.info("[SCHEDULER] 测试数据库连接...")
                try:
                    with pgsql_engine.connect() as conn:
                        conn.execute(sqlalchemy.text("SELECT 1"))
                    logger.info("[SCHEDULER] 数据库连接测试成功")
                except Exception as db_error:
                    raise Exception(f"数据库连接失败: {db_error}")
                
                # 在调度器启动前执行建表操作（避免调度器启动时立即执行导致超时）
                logger.info("[SCHEDULER] 执行建表操作...")
                try:
                    safe_create_tables()
                    logger.info("[SCHEDULER] 建表操作完成")
                except Exception as table_error:
                    # 建表失败记录警告但不阻止调度器启动
                    logger.warning(f"[SCHEDULER] 建表操作失败: {table_error}")
                    logger.warning("[SCHEDULER] 继续启动调度器，但后续任务可能会失败")
                
                # 初始化调度器
                self.scheduler = self._init_scheduler()
                
                # 注册信号处理器
                self._register_signal_handlers()
                
                # 设置任务（不再包含建表任务）
                self._setup_jobs()
                
                # 启动调度器
                self._start_scheduler()
                
                logger.info("[SCHEDULER] 调度器初始化成功，进入主循环")
                break
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[SCHEDULER] 初始化失败 (尝试 {init_attempt}/{max_init_retries}): {error_msg}")
                
                is_connection_error = any(keyword in error_msg.lower() for keyword in [
                    'timeout', 'connection', 'connect', 'database', 'server'
                ])
                
                if init_attempt < max_init_retries:
                    if is_connection_error:
                        logger.warning(f"[SCHEDULER] 检测到连接错误，{init_retry_delay}秒后重试初始化...")
                    else:
                        logger.warning(f"[SCHEDULER] 初始化失败，{init_retry_delay}秒后重试...")
                    time.sleep(init_retry_delay)
                else:
                    logger.critical(f"[SCHEDULER] 初始化失败，已达到最大重试次数 ({max_init_retries})")
                    logger.critical("[SCHEDULER] 调度器无法启动，程序退出")
                    raise
        
        # 运行主循环
        try:
            self._run_main_loop()
        except KeyboardInterrupt:
            logger.info("[SCHEDULER] 收到键盘中断，程序退出")
        except Exception as e:
            logger.critical(f"[SCHEDULER] 调度器运行异常: {e}")
            logger.exception(e)  # 记录完整堆栈
        finally:
            logger.info("[SCHEDULER] 调度器程序结束")


def main() -> NoReturn:
    """程序入口点"""
    scheduler = OptimizedScheduler()
    scheduler.run()


if __name__ == "__main__":
    main()