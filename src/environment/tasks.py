"""
环境相关定时任务
"""

from datetime import date, datetime, timedelta

from global_const.const_config import MUSHROOM_ROOM_IDS
from utils import build_task_run_id, log_task_event
from utils.task_common import (
    TaskResult,
    create_task_result,
    ensure_database_connection,
    execute_task_with_retry,
    log_task_summary,
)

from .processor import process_daily_env_stats


def safe_daily_env_stats() -> TaskResult:
    """
    每日环境统计任务

    功能:
    1. 遍历所有蘑菇房
    2. 计算前一天的环境统计数据
    3. 存储到数据库
    """
    task_run_id = build_task_run_id("ENV_TASK")
    ensure_database_connection("[ENV_TASK] 数据库不可达，任务终止（按配置不启用容错）")

    def _run_daily_env_stats(_attempt: int, _max_retries: int) -> TaskResult:
        start_time = datetime.now()
        stat_date = date.today() - timedelta(days=1)
        log_task_event(
            "ENV_TASK",
            "TASK_START",
            "开始执行每日环境统计任务",
            task_type="environment",
            task_run_id=task_run_id,
            status="running",
            stat_date=stat_date.isoformat(),
        )

        success_count = 0
        failed_count = 0
        error_rooms: list[str] = []

        for room_id in MUSHROOM_ROOM_IDS:
            try:
                result = process_daily_env_stats(room_id, stat_date)
                if result.get("success", False):
                    success_count += 1
                else:
                    failed_count += 1
                    error_rooms.append(room_id)
                    log_task_event(
                        "ENV_TASK",
                        "ROOM_FAILURE",
                        "库房环境统计处理返回失败结果",
                        level="ERROR",
                        task_type="environment",
                        task_run_id=task_run_id,
                        room_id=room_id,
                        status="failed",
                        error_code="env_room_result_failed",
                    )
            except Exception as exc:
                failed_count += 1
                error_rooms.append(room_id)
                log_task_event(
                    "ENV_TASK",
                    "ROOM_FAILURE",
                    "库房环境统计处理异常",
                    level="ERROR",
                    task_type="environment",
                    task_run_id=task_run_id,
                    room_id=room_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error_code="env_room_processing_failed",
                    error_message=str(exc),
                )

        duration = (datetime.now() - start_time).total_seconds()
        finish_status = "success" if failed_count == 0 else "partial"
        finish_level = "INFO" if failed_count == 0 else "WARNING"
        log_task_event(
            "ENV_TASK",
            "TASK_FINISH",
            (
                "每日环境统计任务执行完成"
                f" | rooms={len(MUSHROOM_ROOM_IDS)}"
                f" success={success_count}"
                f" failed={failed_count}"
            ),
            level=finish_level,
            task_type="environment",
            task_run_id=task_run_id,
            status=finish_status,
            total_items=len(MUSHROOM_ROOM_IDS),
            successful_items=success_count,
            failed_items=failed_count,
            duration_ms=round(duration * 1000, 2),
        )

        return create_task_result(
            success=failed_count == 0,
            total_items=len(MUSHROOM_ROOM_IDS),
            successful_items=success_count,
            failed_items=failed_count,
            error_items=error_rooms,
            processing_time=duration,
            additional_data={
                "task_run_id": task_run_id,
                "stat_date": stat_date.isoformat(),
                "room_ids": MUSHROOM_ROOM_IDS,
            },
        )

    def _on_failure(error_msg: str, _attempt: int, _total_attempts: int) -> TaskResult:
        return create_task_result(
            success=False,
            total_items=len(MUSHROOM_ROOM_IDS),
            failed_items=len(MUSHROOM_ROOM_IDS),
            error_items=[error_msg],
            additional_data={"task_run_id": task_run_id},
        )

    result = execute_task_with_retry(
        task_name="ENV_TASK",
        task_func=_run_daily_env_stats,
        max_retries=1,
        task_context={"task_type": "environment", "task_run_id": task_run_id},
        on_non_retryable=_on_failure,
        on_exhausted=_on_failure,
    )
    log_task_summary("ENV_TASK", result)
    return result
