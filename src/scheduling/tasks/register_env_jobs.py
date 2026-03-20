"""环境类任务注册。"""

from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler

from environment.tasks import safe_daily_env_stats

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs


def get_env_job_definitions() -> list[CronJobDefinition]:
    """返回环境任务定义。"""
    return [
        CronJobDefinition(
            job_id="daily_env_stats",
            lock_id="daily_env_stats",
            func=safe_daily_env_stats,
            cron_kwargs={"hour": 1, "minute": 3, "second": 20},
            log_message="[SCHEDULER] 每日环境统计任务已添加",
        )
    ]


def register_env_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册环境统计任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_env_job_definitions(),
    )
