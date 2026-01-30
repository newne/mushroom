"""
任务注册模块
"""
from typing import List
from datetime import timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    DECISION_ANALYSIS_SCHEDULE_TIMES,
)
from utils.loguru_setting import logger

# 任务模块导入 (重构后)
from environment.tasks import safe_daily_env_stats
from monitoring.tasks import safe_hourly_setpoint_monitoring
from vision.tasks import safe_hourly_text_quality_inference, safe_daily_top_quality_clip_inference
from decision_analysis.tasks import safe_batch_decision_analysis
from tasks.table import safe_create_tables  # This one might stay in tasks/table or move?

def perform_initial_tasks() -> None:
    """执行初始任务（如建表）"""
    logger.info("[SCHEDULER] 执行建表操作...")
    try:
        safe_create_tables()
        logger.info("[SCHEDULER] 建表操作完成")
    except Exception as table_error:
        # 建表失败记录警告但不阻止调度器启动
        logger.warning(f"[SCHEDULER] 建表操作失败: {table_error}")
        logger.warning("[SCHEDULER] 继续启动调度器，但后续任务可能会失败")

def register_jobs(scheduler: BackgroundScheduler, local_timezone: timezone) -> None:
    """
    注册所有业务任务到调度器
    
    Args:
        scheduler: 调度器实例
        local_timezone: 本地时区
    """
    # 每日环境统计任务（01:03:20执行）
    scheduler.add_job(
        func=safe_daily_env_stats,
        trigger=CronTrigger(hour=1, minute=3, second=20, timezone=local_timezone),
        id="daily_env_stats",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每日环境统计任务已添加")
    
    # 每小时设定点监控任务（每小时第5分钟执行）
    scheduler.add_job(
        func=safe_hourly_setpoint_monitoring,
        trigger=CronTrigger(minute=5, timezone=local_timezone),
        id="hourly_setpoint_monitoring",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每小时设定点监控任务已添加（基于静态配置表的优化版）")
    
    # 每小时文本编码与质量评估任务（每小时第25分钟执行）
    scheduler.add_job(
        func=safe_hourly_text_quality_inference,
        trigger=CronTrigger(minute=25, timezone=local_timezone),
        id="hourly_text_quality_inference",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每小时文本/质量任务已添加 (每小时第25分钟执行)")

    # 每日Top质量图像编码任务（02:10执行）
    scheduler.add_job(
        func=safe_daily_top_quality_clip_inference,
        trigger=CronTrigger(hour=2, minute=10, second=0, timezone=local_timezone),
        id="daily_top_quality_clip_inference",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每日Top质量图像编码任务已添加 (02:10执行)")
    
    # ==================== 决策分析定时任务 ====================
    # 提取时间点配置
    hours = [str(hour) for hour, minute in DECISION_ANALYSIS_SCHEDULE_TIMES]
    minutes = [str(minute) for hour, minute in DECISION_ANALYSIS_SCHEDULE_TIMES]
    
    # 添加决策分析任务
    scheduler.add_job(
        func=safe_batch_decision_analysis,
        trigger=CronTrigger(
            hour=','.join(hours), 
            minute=','.join(minutes), 
            second=0, 
            timezone=local_timezone
        ),
        id="decision_analysis",
        replace_existing=True,
    )
    
    time_points = [f'{h:02d}:{m:02d}' for h, m in DECISION_ANALYSIS_SCHEDULE_TIMES]
    logger.info(f"[SCHEDULER] 决策分析任务已添加 (每天 {', '.join(time_points)} 执行)")
    logger.info(
        f"[SCHEDULER] 决策分析任务配置: 库房={MUSHROOM_ROOM_IDS}, "
        f"时间点={time_points}, "
        f"功能=多图像分析+结构化参数调整+风险评估, "
        f"存储=仅动态结果表（优化版）"
    )

def log_registered_jobs(scheduler: BackgroundScheduler) -> None:
    """记录已注册的任务信息"""
    jobs = scheduler.get_jobs()
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
