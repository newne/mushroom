"""
调度任务模块（APScheduler + Redis 持久化）
核心功能：
1. 初始化 Redis 持久化的调度器
2. 管理定时任务的添加/执行/监控
3. 优雅退出与异常处理
"""
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import NoReturn, Optional, Dict, Any
from typing_extensions import Final

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

# ===================== 配置常量 =====================
def get_local_timezone() -> timezone:
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

SCHEDULER_TIMEZONE: Final[timezone] = get_local_timezone()
MISFIRE_GRACE_TIME: Final[int] = 300
MAX_JOB_INSTANCES: Final[int] = 1
CREATE_TABLES_DELAY: Final[int] = 5
MAIN_LOOP_INTERVAL: Final[int] = 5

# 全局状态
_is_shutting_down = False



# ===================== 业务任务函数 =====================
def safe_create_tables() -> None:
    """建表任务"""
    try:
        logger.info("[TASK] 开始执行建表任务")
        start_time = datetime.now(SCHEDULER_TIMEZONE)
        
        create_tables()
        
        duration = (datetime.now(SCHEDULER_TIMEZONE) - start_time).total_seconds()
        logger.info(f"[TASK] 建表任务完成，耗时: {duration:.2f}秒")
        
    except Exception as e:
        logger.error(f"[TASK] 建表任务失败: {e}", exc_info=True)
        raise


def safe_daily_env_stats() -> None:
    """每日环境统计任务"""
    try:
        logger.info("[TASK] 开始执行每日环境统计")
        start_time = datetime.now(SCHEDULER_TIMEZONE)
        
        processor = create_env_data_processor()
        yesterday = datetime.now(SCHEDULER_TIMEZONE).date() - timedelta(days=1)
        processor.compute_and_store_daily_stats(
            datetime(yesterday.year, yesterday.month, yesterday.day)
        )
        
        duration = (datetime.now(SCHEDULER_TIMEZONE) - start_time).total_seconds()
        logger.info(f"[TASK] 每日环境统计完成，耗时: {duration:.2f}秒")
        
    except Exception as e:
        logger.error(f"[TASK] 每日环境统计失败: {e}", exc_info=True)
        raise


# ===================== 调度器管理 =====================
def add_create_tables_job(scheduler: BackgroundScheduler) -> Job:
    """添加建表任务"""
    job = scheduler.add_job(
        func=safe_create_tables,
        id="create_tables",
        misfire_grace_time=MISFIRE_GRACE_TIME,
        next_run_time=datetime.now(SCHEDULER_TIMEZONE),
        replace_existing=True,
    )
    logger.info("[SCHED] 建表任务已添加")
    return job


def add_business_jobs(scheduler: BackgroundScheduler) -> None:
    """添加业务任务"""
    # 每日环境统计任务（01:03:20执行）
    scheduler.add_job(
        func=safe_daily_env_stats,
        trigger=CronTrigger(hour=1, minute=3, second=20, timezone=SCHEDULER_TIMEZONE),
        id="daily_env_stats",
        misfire_grace_time=MISFIRE_GRACE_TIME,
        max_instances=MAX_JOB_INSTANCES,
        replace_existing=True,
    )
    logger.info("[SCHED] 每日环境统计任务已添加")


def init_scheduler() -> BackgroundScheduler:
    """初始化调度器"""
    # Redis任务存储
    job_stores = {
        "default": RedisJobStore(
            host=settings.redis.host,
            port=settings.redis.port,
            password=settings.redis.password,
            socket_timeout=10,
            socket_connect_timeout=5,
        )
    }

    # 任务默认配置
    job_defaults = {
        "misfire_grace_time": MISFIRE_GRACE_TIME,
        "max_instances": MAX_JOB_INSTANCES,
        "coalesce": True,
        "replace_existing": True,
    }

    try:
        scheduler = BackgroundScheduler(
            jobstores=job_stores,
            timezone=SCHEDULER_TIMEZONE,
            job_defaults=job_defaults,
        )
        logger.info("[SCHED] 调度器初始化成功")
        return scheduler
    except SchedulerAlreadyRunningError as e:
        logger.error(f"[SCHED] 调度器初始化失败: {e}", exc_info=True)
        raise


# ===================== 信号处理 =====================
def handle_shutdown(signal_num: int, frame: Optional[Any], scheduler: Optional[BackgroundScheduler] = None) -> NoReturn:
    """处理退出信号"""
    global _is_shutting_down
    
    if _is_shutting_down:
        logger.warning("[SCHED] 已在关闭过程中")
        return
    
    _is_shutting_down = True
    signal_name = signal.Signals(signal_num).name
    logger.info(f"[SCHED] 收到退出信号: {signal_name}")

    if scheduler and scheduler.running:
        try:
            scheduler.shutdown(wait=True)
            logger.info("[SCHED] 调度器已停止")
        except Exception as e:
            logger.error(f"[SCHED] 调度器停止失败: {e}", exc_info=True)

    sys.exit(0)


# ===================== 主程序逻辑 =====================
def setup_scheduler_jobs(scheduler: BackgroundScheduler) -> None:
    """配置调度器任务"""
    # 注册到健康检查
    set_scheduler_instance(scheduler)
    logger.info("[SCHED] 调度器已注册到健康检查")

    # 添加建表任务
    add_create_tables_job(scheduler)
    logger.info(f"[SCHED] 等待建表完成，延迟 {CREATE_TABLES_DELAY} 秒")
    time.sleep(CREATE_TABLES_DELAY)

    # 添加业务任务
    add_business_jobs(scheduler)

    # 注册事件监听器
    scheduler.add_listener(exception_listener, events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED)
    logger.info("[SCHED] 事件监听器已注册")
    
    # 显示任务信息
    jobs = scheduler.get_jobs()
    logger.info(f"[SCHED] 总共添加了 {len(jobs)} 个任务")
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


def start_scheduler_loop(scheduler: BackgroundScheduler) -> NoReturn:
    """启动调度器主循环"""
    try:
        scheduler.start()
        logger.info("[SCHED] 调度器启动成功")
    except SchedulerAlreadyRunningError as e:
        logger.error(f"[SCHED] 调度器启动失败: {e}", exc_info=True)
        raise

    logger.info("[SCHED] 进入主循环")
    consecutive_failures = 0
    max_failures = 3
    
    while not _is_shutting_down:
        try:
            if not scheduler.running:
                consecutive_failures += 1
                logger.error(f"[SCHED] 调度器停止 (失败: {consecutive_failures}/{max_failures})")
                
                if consecutive_failures >= max_failures:
                    logger.critical("[SCHED] 连续失败过多，退出")
                    break
                
                scheduler.start()
                logger.info("[SCHED] 调度器重启成功")
            else:
                consecutive_failures = 0
            
            time.sleep(MAIN_LOOP_INTERVAL)
            
        except SchedulerAlreadyRunningError:
            consecutive_failures = 0
            time.sleep(MAIN_LOOP_INTERVAL)
        except KeyboardInterrupt:
            logger.info("[SCHED] 收到键盘中断")
            handle_shutdown(signal.SIGINT, None, scheduler)
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"[SCHED] 主循环异常: {e}", exc_info=True)
            
            if consecutive_failures >= max_failures:
                logger.critical("[SCHED] 主循环异常过多，退出")
                break
            
            time.sleep(MAIN_LOOP_INTERVAL)
    
    handle_shutdown(signal.SIGTERM, None, scheduler)


def main() -> NoReturn:
    """程序入口"""
    logger.info("[SCHED] === 调度器启动 ===")

    try:
        scheduler = init_scheduler()
        
        # 注册信号处理器
        signal.signal(signal.SIGINT, lambda sig, frame: handle_shutdown(sig, frame, scheduler))
        signal.signal(signal.SIGTERM, lambda sig, frame: handle_shutdown(sig, frame, scheduler))
        logger.info("[SCHED] 信号处理器已注册")

        # 配置并启动
        setup_scheduler_jobs(scheduler)
        start_scheduler_loop(scheduler)
        
    except KeyboardInterrupt:
        logger.info("[SCHED] 键盘中断，退出")
    except Exception as e:
        logger.critical(f"[SCHED] 核心异常: {e}", exc_info=True)
        raise
    finally:
        logger.info("[SCHED] 程序结束")


if __name__ == "__main__":
    main()