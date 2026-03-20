"""调度任务注册模块集合。"""

from scheduling.tasks.register_batch_yield_jobs import register_batch_yield_jobs
from scheduling.tasks.register_decision_jobs import register_decision_jobs
from scheduling.tasks.register_env_jobs import register_env_jobs
from scheduling.tasks.register_monitoring_jobs import register_monitoring_jobs
from scheduling.tasks.register_vision_jobs import register_vision_jobs

TASK_REGISTRARS = (
    register_env_jobs,
    register_monitoring_jobs,
    register_vision_jobs,
    register_batch_yield_jobs,
    register_decision_jobs,
)

__all__ = [
    "TASK_REGISTRARS",
    "register_env_jobs",
    "register_monitoring_jobs",
    "register_vision_jobs",
    "register_batch_yield_jobs",
    "register_decision_jobs",
]
