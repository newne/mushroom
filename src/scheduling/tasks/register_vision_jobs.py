"""视觉类任务注册。"""

from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler

from vision.tasks import (
    safe_daily_top_quality_clip_inference,
    safe_hourly_text_quality_inference,
)

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs


def get_vision_job_definitions() -> list[CronJobDefinition]:
    """返回视觉任务定义。"""
    return [
        CronJobDefinition(
            job_id="hourly_text_quality_inference",
            lock_id="hourly_text_quality_inference",
            func=safe_hourly_text_quality_inference,
            cron_kwargs={"minute": 25},
            log_message="[SCHEDULER] 每小时文本/质量任务已添加 (每小时第25分钟执行)",
        ),
        CronJobDefinition(
            job_id="daily_top_quality_clip_inference",
            lock_id="daily_top_quality_clip_inference",
            func=safe_daily_top_quality_clip_inference,
            cron_kwargs={"hour": 2, "minute": 10, "second": 0},
            log_message="[SCHEDULER] 每日Top质量图像编码任务已添加 (02:10执行)",
        ),
    ]


def register_vision_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册视觉处理任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_vision_job_definitions(),
    )
