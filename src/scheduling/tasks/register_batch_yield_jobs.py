"""批次产量类任务注册。"""

from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler

from tasks.batch_yield import safe_daily_batch_yield_init

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs


def get_batch_yield_job_definitions() -> list[CronJobDefinition]:
    """返回批次产量任务定义。"""
    return [
        CronJobDefinition(
            job_id="daily_batch_yield_init",
            lock_id="daily_batch_yield_init",
            func=safe_daily_batch_yield_init,
            cron_kwargs={"hour": 5, "minute": 0, "second": 0},
            log_message="[SCHEDULER] 每日批次产量初始化任务已添加 (05:00执行)",
        )
    ]


def register_batch_yield_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册批次产量任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_batch_yield_job_definitions(),
    )
