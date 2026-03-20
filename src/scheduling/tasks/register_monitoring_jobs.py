"""监控类任务注册。"""

from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler

from monitoring.tasks import safe_hourly_setpoint_monitoring

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs


def get_monitoring_job_definitions() -> list[CronJobDefinition]:
    """返回监控任务定义。"""
    return [
        CronJobDefinition(
            job_id="hourly_setpoint_monitoring",
            lock_id="hourly_setpoint_monitoring",
            func=safe_hourly_setpoint_monitoring,
            cron_kwargs={"minute": 5},
            log_message="[SCHEDULER] 每小时设定点监控任务已添加（基于静态配置表的优化版）",
        )
    ]


def register_monitoring_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册设定点监控任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_monitoring_job_definitions(),
    )
