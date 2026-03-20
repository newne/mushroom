"""
基础任务执行类

提供所有定时任务的基础功能和通用接口。
"""

from abc import ABC, abstractmethod
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict

from utils import build_task_run_id, log_task_event, use_log_context
from utils.task_common import create_task_result, execute_task_with_retry


class BaseTask(ABC):
    """基础任务执行类。"""

    def __init__(self, task_name: str, max_retries: int = 3, retry_delay: int = 5):
        self.task_name = task_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.connection_error_keywords = [
            "timeout",
            "connection",
            "connect",
            "database",
            "server",
        ]

    @abstractmethod
    def execute_task(self) -> Dict[str, Any]:
        """执行具体任务逻辑（子类必须实现）。"""

    def run(self) -> Dict[str, Any]:
        """运行任务（带重试机制）。"""
        task_run_id = build_task_run_id(self.task_name)

        with use_log_context(
            task_name=self.task_name,
            task_type="task",
            task_run_id=task_run_id,
        ):

            def _before_attempt(attempt: int, total_attempts: int) -> None:
                log_task_event(
                    self.task_name,
                    "TASK_ATTEMPT_START",
                    "开始执行任务",
                    task_run_id=task_run_id,
                    attempt=attempt,
                    max_retries=total_attempts,
                    status="running",
                )

            def _run_task(attempt: int, _total_attempts: int) -> Dict[str, Any]:
                start_time = datetime.now()
                result = self.execute_task()
                duration = (datetime.now() - start_time).total_seconds()
                if isinstance(result, dict):
                    result.setdefault("task_run_id", task_run_id)
                    result.setdefault("processing_time", duration)
                    result["execution_time"] = duration
                    result["attempt"] = attempt
                return result

            def _on_failure(
                error_msg: str,
                attempt: int,
                _total_attempts: int,
            ) -> Dict[str, Any]:
                return self._create_error_result(error_msg, attempt, task_run_id)

            result = execute_task_with_retry(
                task_name=self.task_name,
                task_func=_run_task,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                task_context={"task_type": "task", "task_run_id": task_run_id},
                before_attempt=_before_attempt,
                on_exhausted=_on_failure,
                on_non_retryable=_on_failure,
                connection_error_keywords=self.connection_error_keywords,
            )

            if isinstance(result, dict):
                result.setdefault("task_run_id", task_run_id)
                result.setdefault("task_name", self.task_name)
                result.setdefault("timestamp", datetime.now().isoformat())
            return result

    def _create_error_result(
        self,
        error_msg: str,
        attempt: int,
        task_run_id: str | None = None,
    ) -> Dict[str, Any]:
        """创建错误结果。"""
        return dict(
            create_task_result(
                success=False,
                failed_items=1,
                error_items=[error_msg],
                additional_data={
                    "error": error_msg,
                    "attempt": attempt,
                    "task_name": self.task_name,
                    "task_run_id": task_run_id,
                },
            )
        )

    def _create_success_result(self, **kwargs: Any) -> Dict[str, Any]:
        """创建成功结果。"""
        result = {
            "success": True,
            "task_name": self.task_name,
            "timestamp": datetime.now().isoformat(),
        }
        result.update(kwargs)
        return result


class TaskExecutor:
    """任务执行器，提供任务执行统一接口。"""

    @staticmethod
    def execute_with_retry(
        task_func: Callable[..., Any],
        task_name: str,
        max_retries: int = 3,
        retry_delay: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """执行任务（带重试机制）。"""
        task_run_id = build_task_run_id(task_name)
        connection_error_keywords = [
            "timeout",
            "connection",
            "connect",
            "database",
            "server",
        ]

        with use_log_context(
            task_name=task_name, task_type="task", task_run_id=task_run_id
        ):

            def _before_attempt(attempt: int, total_attempts: int) -> None:
                log_task_event(
                    task_name,
                    "TASK_ATTEMPT_START",
                    "开始执行任务",
                    task_run_id=task_run_id,
                    attempt=attempt,
                    max_retries=total_attempts,
                    status="running",
                )

            def _run_task(attempt: int, _total_attempts: int) -> Dict[str, Any]:
                start_time = datetime.now()
                result = task_func(**kwargs)
                duration = (datetime.now() - start_time).total_seconds()
                return {
                    "success": True,
                    "result": result,
                    "processing_time": duration,
                    "execution_time": duration,
                    "attempt": attempt,
                    "task_name": task_name,
                    "task_run_id": task_run_id,
                }

            def _on_failure(
                error_msg: str, attempt: int, _total_attempts: int
            ) -> Dict[str, Any]:
                return dict(
                    create_task_result(
                        success=False,
                        failed_items=1,
                        error_items=[error_msg],
                        additional_data={
                            "error": error_msg,
                            "attempt": attempt,
                            "task_name": task_name,
                            "task_run_id": task_run_id,
                        },
                    )
                )

            return execute_task_with_retry(
                task_name=task_name,
                task_func=_run_task,
                max_retries=max_retries,
                retry_delay=retry_delay,
                task_context={"task_type": "task", "task_run_id": task_run_id},
                before_attempt=_before_attempt,
                on_exhausted=_on_failure,
                on_non_retryable=_on_failure,
                connection_error_keywords=connection_error_keywords,
            )


def task_wrapper(task_name: str, max_retries: int = 3, retry_delay: int = 5):
    """任务装饰器，为任务函数添加重试机制。"""

    def decorator(func: Callable[..., Any]):
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            return TaskExecutor.execute_with_retry(
                lambda **inner_kwargs: func(*args, **inner_kwargs),
                task_name,
                max_retries,
                retry_delay,
                **kwargs,
            )

        return wrapper

    return decorator
