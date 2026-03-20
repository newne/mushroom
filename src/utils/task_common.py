"""
任务公共组件模块

提供所有定时任务共用的工具函数和组件。
"""

import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypedDict

from utils.task_logging import log_task_event

DEFAULT_CONNECTION_ERROR_KEYWORDS = (
    "timeout",
    "connection",
    "connect",
    "database",
    "server",
)


class TaskRetryableError(RuntimeError):
    """可重试任务异常。"""


class TaskNonRetryableError(RuntimeError):
    """不可重试任务异常。"""


class TaskResult(TypedDict, total=False):
    """标准化任务结果结构。"""

    success: bool
    total_items: int
    successful_items: int
    failed_items: int
    skipped_items: int
    success_rate: float
    error_items: list
    processing_time: float
    timestamp: str
    task_run_id: str
    batch_run_id: str
    room_results: dict[str, Any]
    total_changes: int
    changes_by_room: dict[str, Any]
    stored_records: int
    total_dynamic_results: int
    total_change_count: int
    skill_enabled_rooms: int
    skill_hit_rooms: int
    skill_total_matched: int
    skill_total_corrections: int
    skill_total_kb_prior_used: int
    stat_date: str
    room_ids: list[str]
    reprocess: bool
    lookback_hours: int
    target_date: str
    run_id: str
    cluster_meta_count: int
    cluster_rule_count: int
    skipped: bool
    last_generated_at: str
    next_due_at: str


TASK_RESULT_STABLE_FIELDS = (
    "success",
    "total_items",
    "successful_items",
    "failed_items",
    "skipped_items",
    "success_rate",
    "error_items",
    "processing_time",
    "timestamp",
    "task_run_id",
    "batch_run_id",
    "room_results",
    "total_changes",
    "changes_by_room",
    "stored_records",
    "total_dynamic_results",
    "total_change_count",
    "skill_enabled_rooms",
    "skill_hit_rooms",
    "skill_total_matched",
    "skill_total_corrections",
    "skill_total_kb_prior_used",
    "stat_date",
    "room_ids",
    "reprocess",
    "lookback_hours",
    "target_date",
    "run_id",
    "cluster_meta_count",
    "cluster_rule_count",
    "skipped",
    "last_generated_at",
    "next_due_at",
)


def is_connection_error(
    error_msg: str,
    keywords: Optional[tuple[str, ...] | list[str]] = None,
) -> bool:
    """统一判断错误是否属于连接类问题。"""
    normalized = str(error_msg or "").lower()
    check_keywords = keywords or DEFAULT_CONNECTION_ERROR_KEYWORDS
    return any(keyword in normalized for keyword in check_keywords)


def ensure_database_connection(error_msg: str) -> None:
    """数据库不可达时抛出统一异常。"""
    if not check_database_connection():
        log_task_event(
            "TASK_COMMON",
            "DATABASE_CHECK_FAILED",
            error_msg,
            level="ERROR",
            status="failed",
            error_code="database_check_failed",
        )
        raise RuntimeError(error_msg)


def execute_task_with_retry(
    task_name: str,
    task_func: Callable[[int, int], Any],
    max_retries: int = 3,
    retry_delay: int = 5,
    task_context: Optional[dict[str, Any]] = None,
    before_attempt: Optional[Callable[[int, int], None]] = None,
    on_retry: Optional[Callable[[str, int, int], None]] = None,
    on_exhausted: Optional[Callable[[str, int, int], Any]] = None,
    on_non_retryable: Optional[Callable[[str, int, int], Any]] = None,
    connection_error_keywords: Optional[tuple[str, ...] | list[str]] = None,
) -> Any:
    """统一执行带重试的任务。"""
    log_context = task_context or {}
    for attempt in range(1, max_retries + 1):
        if before_attempt:
            before_attempt(attempt, max_retries)

        try:
            return task_func(attempt, max_retries)
        except TaskRetryableError as exc:
            error_msg = str(exc)
            should_retry = True
            error_type = type(exc).__name__
        except TaskNonRetryableError as exc:
            error_msg = str(exc)
            should_retry = False
            error_type = type(exc).__name__
        except Exception as exc:
            error_msg = str(exc)
            should_retry = is_connection_error(error_msg, connection_error_keywords)
            error_type = type(exc).__name__

        log_task_event(
            task_name,
            "TASK_ATTEMPT_FAILED",
            "任务执行失败",
            level="ERROR",
            attempt=attempt,
            max_retries=max_retries,
            status="retrying" if should_retry and attempt < max_retries else "failed",
            error_type=error_type,
            error_code="task_attempt_failed",
            error_message=error_msg,
            **log_context,
        )

        if should_retry and attempt < max_retries:
            if on_retry:
                on_retry(error_msg, attempt, max_retries)
            else:
                log_task_event(
                    task_name,
                    "TASK_RETRY",
                    "检测到连接类错误，准备重试",
                    level="WARNING",
                    attempt=attempt,
                    max_retries=max_retries,
                    status="retrying",
                    retry_delay_sec=retry_delay,
                    **log_context,
                )
            time.sleep(retry_delay)
            continue

        if should_retry and attempt >= max_retries:
            if on_exhausted:
                return on_exhausted(error_msg, attempt, max_retries)
            log_task_event(
                task_name,
                "TASK_RETRY_EXHAUSTED",
                "任务失败，已达到最大重试次数",
                level="ERROR",
                attempt=attempt,
                max_retries=max_retries,
                status="failed",
                error_code="task_retry_exhausted",
                **log_context,
            )
            return None

        if on_non_retryable:
            return on_non_retryable(error_msg, attempt, max_retries)

        log_task_event(
            task_name,
            "TASK_NON_RETRYABLE",
            "任务遇到非连接类错误，不再重试",
            level="ERROR",
            attempt=attempt,
            max_retries=max_retries,
            status="failed",
            error_code="task_non_retryable",
            **log_context,
        )
        return None

    return None


def task_retry_wrapper(
    task_name: str,
    max_retries: int = 3,
    retry_delay: int = 5,
    connection_error_keywords: list = None,
):
    """
    任务重试装饰器

    Args:
        task_name: 任务名称
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）
        connection_error_keywords: 连接错误关键词列表
    """
    if connection_error_keywords is None:
        connection_error_keywords = [
            "timeout",
            "connection",
            "connect",
            "database",
            "server",
        ]

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            def _before_attempt(attempt: int, total_attempts: int) -> None:
                log_task_event(
                    task_name,
                    "TASK_ATTEMPT_START",
                    "开始执行任务",
                    attempt=attempt,
                    max_retries=total_attempts,
                    status="running",
                )

            def _task_runner(_attempt: int, _total_attempts: int):
                start_time = datetime.now()
                result = func(*args, **kwargs)
                duration = (datetime.now() - start_time).total_seconds()
                log_task_event(
                    task_name,
                    "TASK_FINISH",
                    "任务执行完成",
                    status="success",
                    duration_ms=round(duration * 1000, 2),
                )
                return result

            return execute_task_with_retry(
                task_name=task_name,
                task_func=_task_runner,
                max_retries=max_retries,
                retry_delay=retry_delay,
                task_context={"task_type": "task"},
                before_attempt=_before_attempt,
                connection_error_keywords=connection_error_keywords,
            )

        return wrapper

    return decorator


def create_task_result(
    success: bool = False,
    total_items: int = 0,
    successful_items: int = 0,
    failed_items: int = 0,
    error_items: list = None,
    processing_time: float = 0.0,
    additional_data: dict = None,
) -> TaskResult:
    """
    创建标准化的任务执行结果

    Args:
        success: 任务是否成功
        total_items: 总处理项目数
        successful_items: 成功处理项目数
        failed_items: 失败处理项目数
        error_items: 错误项目列表
        processing_time: 处理时间
        additional_data: 额外数据

    Returns:
        Dict[str, Any]: 标准化的任务结果
    """
    result: TaskResult = {
        "success": success,
        "total_items": total_items,
        "successful_items": successful_items,
        "failed_items": failed_items,
        "error_items": error_items or [],
        "processing_time": processing_time,
        "timestamp": datetime.now().isoformat(),
    }

    if additional_data:
        result.update(additional_data)

    return result


def log_task_summary(task_name: str, result: Dict[str, Any]) -> None:
    """
    记录任务执行摘要

    Args:
        task_name: 任务名称
        result: 任务执行结果
    """
    status = "success" if result.get("success") else "failed"
    duration_ms = round(float(result.get("processing_time", 0.0)) * 1000, 2)
    log_task_event(
        task_name,
        "TASK_SUMMARY",
        "任务执行摘要",
        status=status,
        total_items=result.get("total_items", 0),
        successful_items=result.get("successful_items", 0),
        failed_items=result.get("failed_items", 0),
        skipped_items=result.get("skipped_items", 0),
        success_rate=result.get("success_rate", 0.0),
        error_items=result.get("error_items", []),
        duration_ms=duration_ms,
        task_run_id=result.get("task_run_id"),
        batch_run_id=result.get("batch_run_id"),
        total_changes=result.get("total_changes", 0),
        stored_records=result.get("stored_records", 0),
        total_dynamic_results=result.get("total_dynamic_results", 0),
        total_change_count=result.get("total_change_count", 0),
        skill_enabled_rooms=result.get("skill_enabled_rooms", 0),
        skill_hit_rooms=result.get("skill_hit_rooms", 0),
        skill_total_matched=result.get("skill_total_matched", 0),
        skill_total_corrections=result.get("skill_total_corrections", 0),
        skill_total_kb_prior_used=result.get("skill_total_kb_prior_used", 0),
        room_results=result.get("room_results"),
        changes_by_room=result.get("changes_by_room"),
        target_date=result.get("target_date"),
        stat_date=result.get("stat_date"),
        run_id=result.get("run_id"),
    )


def check_database_connection() -> bool:
    """
    检查数据库连接状态

    Returns:
        bool: 连接是否正常
    """
    try:
        from sqlalchemy import text

        from global_const.global_const import pgsql_engine

        with pgsql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        log_task_event(
            "TASK_COMMON", "DATABASE_HEALTHY", "数据库连接检查通过", level="DEBUG"
        )
        return True

    except Exception as e:
        log_task_event(
            "TASK_COMMON",
            "DATABASE_UNAVAILABLE",
            "数据库连接检查失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="database_unavailable",
            error_message=str(e),
        )
        return False


def get_time_range_for_task(hours_back: int = 1) -> tuple:
    """
    获取任务的时间范围

    Args:
        hours_back: 往前推的小时数

    Returns:
        tuple: (start_time, end_time)
    """
    from datetime import timedelta

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours_back)

    return start_time, end_time


def validate_room_ids(room_ids: list) -> list:
    """
    验证库房ID列表

    Args:
        room_ids: 库房ID列表

    Returns:
        list: 有效的库房ID列表
    """
    from global_const.const_config import MUSHROOM_ROOM_IDS

    valid_rooms = []
    for room_id in room_ids:
        if room_id in MUSHROOM_ROOM_IDS:
            valid_rooms.append(room_id)
        else:
            log_task_event(
                "TASK_COMMON",
                "INVALID_ROOM_ID",
                "检测到无效的库房ID",
                level="WARNING",
                room_id=room_id,
                status="invalid",
                error_code="invalid_room_id",
            )

    return valid_rooms


class TaskExecutionContext:
    """任务执行上下文管理器"""

    def __init__(self, task_name: str):
        self.task_name = task_name
        self.task_run_id = build_task_run_id(task_name)
        self.start_time = None
        self.end_time = None
        self._log_context = None

    def __enter__(self):
        self.start_time = datetime.now()
        self._log_context = use_log_context(
            task_name=self.task_name,
            task_type="task",
            task_run_id=self.task_run_id,
        )
        self._log_context.__enter__()
        log_task_event(
            self.task_name,
            "TASK_START",
            "任务开始执行",
            task_run_id=self.task_run_id,
            status="running",
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()

        if exc_type is None:
            log_task_event(
                self.task_name,
                "TASK_FINISH",
                "任务执行完成",
                task_run_id=self.task_run_id,
                status="success",
                duration_ms=round(duration * 1000, 2),
            )
        else:
            log_task_event(
                self.task_name,
                "TASK_FAILURE",
                "任务执行异常",
                level="ERROR",
                task_run_id=self.task_run_id,
                status="failed",
                duration_ms=round(duration * 1000, 2),
                error_type=exc_type.__name__ if exc_type else "UnknownError",
                error_message=str(exc_val),
            )

        if self._log_context is not None:
            self._log_context.__exit__(exc_type, exc_val, exc_tb)

    def get_duration(self) -> float:
        """获取执行时长"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
