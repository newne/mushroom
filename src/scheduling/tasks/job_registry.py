"""
任务注册模块
"""

import hashlib
from datetime import timezone
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from global_const.global_const import pgsql_engine
from scheduling.tasks import TASK_REGISTRARS
from tasks.table import (
    safe_create_tables,  # This one might stay in tasks/table or move?
)
from utils import log_scheduler_event


def _lock_key(job_id: str) -> int:
    """将任务ID映射为 PostgreSQL advisory lock 的 bigint key。"""
    digest = hashlib.sha256(job_id.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=False) & 0x7FFFFFFFFFFFFFFF


def _with_pg_advisory_lock(job_id: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """为任务函数增加跨进程互斥锁，保证同一任务仅一个实例执行。"""
    key = _lock_key(job_id)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        # advisory lock 为会话级锁，必须在同一数据库连接中完成加锁、执行、解锁。
        with pgsql_engine.connect() as conn:
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar()

            if not locked:
                log_scheduler_event(
                    "JOB_LOCK_SKIPPED",
                    "任务已有实例在执行，跳过本次触发",
                    level="WARNING",
                    scheduler_job_id=job_id,
                    status="skipped",
                )
                return None

            try:
                return func(*args, **kwargs)
            finally:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                except Exception as unlock_error:
                    log_scheduler_event(
                        "JOB_UNLOCK_FAILED",
                        "释放 advisory lock 失败",
                        level="ERROR",
                        scheduler_job_id=job_id,
                        status="failed",
                        error_type=type(unlock_error).__name__,
                        error_code="scheduler_unlock_failed",
                        error_message=str(unlock_error),
                    )

    _wrapped.__name__ = f"locked_{job_id}"
    return _wrapped


def perform_initial_tasks() -> None:
    """执行初始任务（如建表）"""
    log_scheduler_event("SCHEDULER_TABLE_INIT_START", "执行建表操作", status="running")
    try:
        safe_create_tables()
        log_scheduler_event(
            "SCHEDULER_TABLE_INIT_SUCCESS", "建表操作完成", status="success"
        )
    except Exception as table_error:
        # 建表失败记录警告但不阻止调度器启动
        log_scheduler_event(
            "SCHEDULER_TABLE_INIT_FAILED",
            "建表操作失败，继续启动调度器",
            level="WARNING",
            status="degraded",
            error_type=type(table_error).__name__,
            error_code="scheduler_table_init_failed",
            error_message=str(table_error),
        )


def register_jobs(scheduler: BackgroundScheduler, local_timezone: timezone) -> None:
    """
    注册所有业务任务到调度器

    Args:
        scheduler: 调度器实例
        local_timezone: 本地时区
    """
    for register_jobs_func in TASK_REGISTRARS:
        register_jobs_func(scheduler, local_timezone, _with_pg_advisory_lock)


def log_registered_jobs(scheduler: BackgroundScheduler) -> None:
    """记录已注册的任务信息"""
    jobs = scheduler.get_jobs()
    log_scheduler_event(
        "SCHEDULER_JOBS_SUMMARY",
        "调度任务注册汇总",
        status="success",
        total_jobs=len(jobs),
    )
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

        log_scheduler_event(
            "JOB_SCHEDULED",
            "任务下次调度时间已确认",
            scheduler_job_id=job.id,
            next_run_time=next_run,
            status="scheduled",
        )
