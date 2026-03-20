"""调度任务注册公共组件。"""

from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Callable, Iterable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from utils import log_scheduler_event

LockWrapper = Callable[[str, Callable[..., Any]], Callable[..., Any]]


@dataclass(frozen=True)
class CronJobDefinition:
    """Cron 任务声明。"""

    job_id: str
    lock_id: str
    func: Callable[..., Any]
    cron_kwargs: dict[str, Any]
    kwargs: dict[str, Any] = field(default_factory=dict)
    replace_existing: bool = True
    max_instances: int | None = None
    coalesce: bool | None = None
    log_message: str | None = None


def register_cron_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
    job_definitions: Iterable[CronJobDefinition],
) -> None:
    """按统一结构注册 Cron 任务。"""
    for job in job_definitions:
        add_job_kwargs: dict[str, Any] = {
            "func": with_lock(job.lock_id, job.func),
            "trigger": CronTrigger(timezone=local_timezone, **job.cron_kwargs),
            "id": job.job_id,
            "replace_existing": job.replace_existing,
        }

        if job.kwargs:
            add_job_kwargs["kwargs"] = job.kwargs
        if job.max_instances is not None:
            add_job_kwargs["max_instances"] = job.max_instances
        if job.coalesce is not None:
            add_job_kwargs["coalesce"] = job.coalesce

        scheduler.add_job(**add_job_kwargs)

        log_scheduler_event(
            "JOB_REGISTERED",
            job.log_message or "调度任务已注册",
            scheduler_job_id=job.job_id,
            next_run_time=str(add_job_kwargs["trigger"]),
            status="scheduled",
        )
