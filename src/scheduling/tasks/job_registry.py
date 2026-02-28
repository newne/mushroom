"""
任务注册模块
"""

import hashlib
from datetime import timezone
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from decision_analysis.tasks import (
    safe_batch_decision_analysis,
    safe_refresh_control_strategy_cluster_kb,
)

# 任务模块导入 (重构后)
from environment.tasks import safe_daily_env_stats
from global_const.const_config import (
    CONTROL_KB_MIN_SAMPLES_PER_POINT,
    CONTROL_KB_REFRESH_CHECK_HOUR,
    CONTROL_KB_REFRESH_CHECK_MINUTE,
    CONTROL_KB_REFRESH_INTERVAL_DAYS,
    DECISION_ANALYSIS_SCHEDULE_TIMES,
    MUSHROOM_ROOM_IDS,
)
from global_const.global_const import pgsql_engine
from monitoring.tasks import safe_hourly_setpoint_monitoring
from tasks.batch_yield import safe_daily_batch_yield_init
from tasks.table import (
    safe_create_tables,  # This one might stay in tasks/table or move?
)
from utils.loguru_setting import logger
from vision.tasks import (
    safe_daily_top_quality_clip_inference,
    safe_hourly_text_quality_inference,
)


def _lock_key(job_id: str) -> int:
    """将任务ID映射为 PostgreSQL advisory lock 的 bigint key。"""
    digest = hashlib.sha256(job_id.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=False) & 0x7FFFFFFFFFFFFFFF


def _with_pg_advisory_lock(job_id: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """为任务函数增加跨进程互斥锁，保证同一任务仅一个实例执行。"""
    key = _lock_key(job_id)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        with pgsql_engine.connect() as conn:
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar()

        if not locked:
            logger.warning(
                f"[SCHEDULER_LOCK] 任务 {job_id} 已有实例在执行，跳过本次触发"
            )
            return None

        try:
            return func(*args, **kwargs)
        finally:
            try:
                with pgsql_engine.connect() as conn:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception as unlock_error:
                logger.error(
                    f"[SCHEDULER_LOCK] 任务 {job_id} 释放 advisory lock 失败: {unlock_error}"
                )

    _wrapped.__name__ = f"locked_{job_id}"
    return _wrapped


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
        func=_with_pg_advisory_lock("daily_env_stats", safe_daily_env_stats),
        trigger=CronTrigger(hour=1, minute=3, second=20, timezone=local_timezone),
        id="daily_env_stats",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每日环境统计任务已添加")

    # 每小时设定点监控任务（每小时第5分钟执行）
    scheduler.add_job(
        func=_with_pg_advisory_lock(
            "hourly_setpoint_monitoring", safe_hourly_setpoint_monitoring
        ),
        trigger=CronTrigger(minute=5, timezone=local_timezone),
        id="hourly_setpoint_monitoring",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每小时设定点监控任务已添加（基于静态配置表的优化版）")

    # 每小时文本编码与质量评估任务（每小时第25分钟执行）
    scheduler.add_job(
        func=_with_pg_advisory_lock(
            "hourly_text_quality_inference", safe_hourly_text_quality_inference
        ),
        trigger=CronTrigger(minute=25, timezone=local_timezone),
        id="hourly_text_quality_inference",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每小时文本/质量任务已添加 (每小时第25分钟执行)")

    # 每日Top质量图像编码任务（02:10执行）
    scheduler.add_job(
        func=_with_pg_advisory_lock(
            "daily_top_quality_clip_inference", safe_daily_top_quality_clip_inference
        ),
        trigger=CronTrigger(hour=2, minute=10, second=0, timezone=local_timezone),
        id="daily_top_quality_clip_inference",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每日Top质量图像编码任务已添加 (02:10执行)")

    # 每日批次产量初始化任务（05:00执行）
    scheduler.add_job(
        func=_with_pg_advisory_lock(
            "daily_batch_yield_init", safe_daily_batch_yield_init
        ),
        trigger=CronTrigger(hour=5, minute=0, second=0, timezone=local_timezone),
        id="daily_batch_yield_init",
        replace_existing=True,
    )
    logger.info("[SCHEDULER] 每日批次产量初始化任务已添加 (05:00执行)")

    # ==================== 决策分析定时任务 ====================
    # 提取时间点配置
    hours = [str(hour) for hour, minute in DECISION_ANALYSIS_SCHEDULE_TIMES]
    minutes = [str(minute) for hour, minute in DECISION_ANALYSIS_SCHEDULE_TIMES]

    # 添加决策分析任务
    scheduler.add_job(
        func=_with_pg_advisory_lock("decision_analysis", safe_batch_decision_analysis),
        trigger=CronTrigger(
            hour=",".join(hours),
            minute=",".join(minutes),
            second=0,
            timezone=local_timezone,
        ),
        id="decision_analysis",
        replace_existing=True,
    )

    time_points = [f"{h:02d}:{m:02d}" for h, m in DECISION_ANALYSIS_SCHEDULE_TIMES]
    logger.info(f"[SCHEDULER] 决策分析任务已添加 (每天 {', '.join(time_points)} 执行)")
    logger.info(
        f"[SCHEDULER] 决策分析任务配置: 库房={MUSHROOM_ROOM_IDS}, "
        f"时间点={time_points}, "
        f"功能=多图像分析+结构化参数调整+风险评估, "
        f"存储=仅动态结果表（优化版）"
    )

    # ==================== 聚类控制知识库刷新任务 ====================
    # 每日固定时间检查，实际执行间隔由任务内部按27天控制
    scheduler.add_job(
        func=_with_pg_advisory_lock(
            "refresh_control_strategy_cluster_kb",
            safe_refresh_control_strategy_cluster_kb,
        ),
        trigger=CronTrigger(
            hour=CONTROL_KB_REFRESH_CHECK_HOUR,
            minute=CONTROL_KB_REFRESH_CHECK_MINUTE,
            second=0,
            timezone=local_timezone,
        ),
        kwargs={
            "interval_days": CONTROL_KB_REFRESH_INTERVAL_DAYS,
            "min_samples_per_point": CONTROL_KB_MIN_SAMPLES_PER_POINT,
        },
        id="refresh_control_strategy_cluster_kb",
        replace_existing=True,
    )
    logger.info(
        "[SCHEDULER] 聚类控制知识库刷新任务已添加 "
        f"(每天 {CONTROL_KB_REFRESH_CHECK_HOUR:02d}:{CONTROL_KB_REFRESH_CHECK_MINUTE:02d} 检查, "
        f"每{CONTROL_KB_REFRESH_INTERVAL_DAYS}天执行一次)"
    )


def log_registered_jobs(scheduler: BackgroundScheduler) -> None:
    """记录已注册的任务信息"""
    jobs = scheduler.get_jobs()
    logger.info(f"[SCHEDULER] 总共添加了 {len(jobs)} 个任务")
    for job in jobs:
        try:
            if hasattr(job, "next_run_time") and job.next_run_time:
                next_run = job.next_run_time.isoformat()
            elif hasattr(job, "trigger") and job.trigger:
                next_run = f"触发器: {job.trigger}"
            else:
                next_run = "一次性任务"
        except Exception:
            next_run = "未知"

        logger.info(f"  - {job.id}: {next_run}")
