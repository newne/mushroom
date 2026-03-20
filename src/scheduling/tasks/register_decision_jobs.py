"""决策分析类任务注册。"""

from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler

from decision_analysis.tasks import (
    safe_batch_decision_analysis,
    safe_refresh_control_strategy_cluster_kb,
)
from global_const.const_config import (
    CONTROL_KB_MIN_SAMPLES_PER_POINT,
    CONTROL_KB_REFRESH_CHECK_HOUR,
    CONTROL_KB_REFRESH_CHECK_MINUTE,
    CONTROL_KB_REFRESH_INTERVAL_DAYS,
    DECISION_ANALYSIS_SCHEDULE_TIMES,
    MUSHROOM_ROOM_IDS,
)
from utils import log_scheduler_event

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs


def get_decision_job_definitions() -> list[CronJobDefinition]:
    """返回决策分析与知识库任务定义。"""
    decision_jobs = [
        CronJobDefinition(
            job_id=f"decision_analysis_{hour:02d}{minute:02d}",
            lock_id="decision_analysis",
            func=safe_batch_decision_analysis,
            cron_kwargs={"hour": hour, "minute": minute, "second": 0},
            kwargs={"schedule_hour": hour, "schedule_minute": minute},
            max_instances=1,
            coalesce=True,
        )
        for hour, minute in DECISION_ANALYSIS_SCHEDULE_TIMES
    ]
    decision_jobs.append(
        CronJobDefinition(
            job_id="refresh_control_strategy_cluster_kb",
            lock_id="refresh_control_strategy_cluster_kb",
            func=safe_refresh_control_strategy_cluster_kb,
            cron_kwargs={
                "hour": CONTROL_KB_REFRESH_CHECK_HOUR,
                "minute": CONTROL_KB_REFRESH_CHECK_MINUTE,
                "second": 0,
            },
            kwargs={
                "interval_days": CONTROL_KB_REFRESH_INTERVAL_DAYS,
                "min_samples_per_point": CONTROL_KB_MIN_SAMPLES_PER_POINT,
            },
            log_message=(
                "[SCHEDULER] 聚类控制知识库刷新任务已添加 "
                f"(每天 {CONTROL_KB_REFRESH_CHECK_HOUR:02d}:{CONTROL_KB_REFRESH_CHECK_MINUTE:02d} 检查, "
                f"每{CONTROL_KB_REFRESH_INTERVAL_DAYS}天执行一次)"
            ),
        )
    )
    return decision_jobs


def register_decision_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册决策分析与知识库刷新任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_decision_job_definitions(),
    )

    time_points = [f"{h:02d}:{m:02d}" for h, m in DECISION_ANALYSIS_SCHEDULE_TIMES]
    log_scheduler_event(
        "DECISION_JOBS_REGISTERED",
        "决策分析任务已注册",
        status="scheduled",
        configured_times=time_points,
        room_ids=MUSHROOM_ROOM_IDS,
    )
    log_scheduler_event(
        "DECISION_JOBS_CONFIG",
        "决策分析任务配置已生效",
        status="scheduled",
        configured_times=time_points,
        room_ids=MUSHROOM_ROOM_IDS,
        storage_mode="dynamic_results_only",
    )
