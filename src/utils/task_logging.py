"""任务日志公共工具。"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import uuid4

from loguru import logger

TASK_LOG_EXTRA_DEFAULTS = {
    "event": "-",
    "task_name": "-",
    "task_type": "-",
    "task_run_id": "-",
    "batch_id": "-",
    "batch_run_id": "-",
    "decision_id": "-",
    "room_id": "-",
    "attempt": "-",
    "max_retries": "-",
    "status": "-",
    "duration_ms": "-",
    "error_type": "-",
    "error_code": "-",
    "error_message": "-",
    "scheduler_job_id": "-",
    "trigger_time": "-",
    "next_run_time": "-",
    "total_items": 0,
    "successful_items": 0,
    "failed_items": 0,
    "skipped_items": 0,
    "success_rate": 0.0,
    "total_changes": 0,
    "stored_records": 0,
    "run_id": "-",
    "target_date": "-",
    "stat_date": "-",
}

_CURRENT_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "current_task_log_context",
    default={},
)


def build_task_run_id(task_name: str, prefix: str | None = None) -> str:
    """生成统一的任务执行ID。"""
    normalized = (prefix or task_name).strip().lower().replace(" ", "_")
    normalized = normalized.replace("[", "").replace("]", "")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{normalized}-{timestamp}-{uuid4().hex[:6]}"


def get_current_log_context() -> dict[str, Any]:
    """获取当前日志上下文。"""
    return dict(_CURRENT_LOG_CONTEXT.get())


@contextmanager
def use_log_context(**context: Any):
    """在当前执行上下文中注入统一日志字段。"""
    merged_context = get_current_log_context()
    merged_context.update(
        {key: value for key, value in context.items() if value is not None}
    )
    token = _CURRENT_LOG_CONTEXT.set(merged_context)
    try:
        yield merged_context
    finally:
        _CURRENT_LOG_CONTEXT.reset(token)


def log_event(level: str, event: str, message: str, **context: Any) -> None:
    """输出统一结构化事件日志。"""
    normalized_level = str(level or "INFO").upper()
    payload = get_current_log_context()
    payload.update({key: value for key, value in context.items() if value is not None})
    bound_logger = logger.bind(**payload)
    bound_logger.log(normalized_level, message)


def log_task_event(
    task_name: str,
    event: str,
    message: str,
    *,
    level: str = "INFO",
    task_type: str = "task",
    **context: Any,
) -> None:
    """输出统一的任务事件日志。"""
    log_event(
        level=level,
        event=event,
        message=message,
        task_name=task_name,
        task_type=task_type,
        **context,
    )


def log_scheduler_event(
    event: str, message: str, *, level: str = "INFO", **context: Any
) -> None:
    """输出统一的调度器事件日志。"""
    log_event(
        level=level,
        event=event,
        message=message,
        task_name="SCHEDULER",
        task_type="scheduler",
        **context,
    )
