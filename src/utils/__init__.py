"""
工具模块

提供系统通用的工具函数和类。

工具模块：
- database_utils: 数据库操作工具
- monitoring_utils: 监控和健康检查工具
- task_common: 任务公共组件
- env_data_processor: 环境数据处理器
- loguru_setting: 日志配置
- create_table: 数据库表管理
- minio_client: MinIO客户端
- data_preprocessing: 数据预处理
- dataframe_utils: DataFrame工具
- visualization: 数据可视化
"""

# 导入核心工具类
from .database_utils import (
    DatabaseManager,
    DatabaseRetryManager,
    check_database_health,
    execute_with_retry,
    get_database_manager,
)
from .monitoring_utils import (
    HealthChecker,
    SystemMonitor,
    TaskMonitor,
    get_health_checker,
    get_system_monitor,
    get_task_monitor,
    quick_health_check,
)
from .task_common import (
    TASK_RESULT_STABLE_FIELDS,
    TaskExecutionContext,
    check_database_connection,
    create_task_result,
    get_time_range_for_task,
    log_task_summary,
    task_retry_wrapper,
    validate_room_ids,
)
from .task_logging import (
    TASK_LOG_EXTRA_DEFAULTS,
    build_task_run_id,
    get_current_log_context,
    log_scheduler_event,
    log_task_event,
    use_log_context,
)

# 工具类列表
__all__ = [
    # 数据库工具
    "DatabaseManager",
    "DatabaseRetryManager",
    "get_database_manager",
    "execute_with_retry",
    "check_database_health",
    # 监控工具
    "SystemMonitor",
    "TaskMonitor",
    "HealthChecker",
    "get_system_monitor",
    "get_task_monitor",
    "get_health_checker",
    "quick_health_check",
    # 任务公共组件
    "task_retry_wrapper",
    "create_task_result",
    "log_task_summary",
    "check_database_connection",
    "get_time_range_for_task",
    "validate_room_ids",
    "TaskExecutionContext",
    "TASK_RESULT_STABLE_FIELDS",
    # 日志协议工具
    "TASK_LOG_EXTRA_DEFAULTS",
    "build_task_run_id",
    "get_current_log_context",
    "log_task_event",
    "log_scheduler_event",
    "use_log_context",
]
