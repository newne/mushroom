"""
健康检查模块（适配 APScheduler 调度器）
遵循 Google Python 代码规范，核心功能：
1. 校验调度任务的执行状态（异常/超时）
2. 提供详细/简化的健康状态查询接口
3. 线程安全的任务状态管理
"""

# ===================== 常量定义（全大写 + 语义化） =====================
# 时区配置（与调度器统一使用 UTC）
import os
# ===================== 标准库导入（按字母序） =====================
import threading
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Dict, List, Optional, Final, Any

# ===================== 第三方库导入（按字母序） =====================
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Response
from fastapi.openapi.utils import get_openapi
from loguru import logger
from typing_extensions import TypedDict

# ===================== 本地模块导入（按字母序） =====================

# 获取系统时区，如果获取不到则使用UTC
LOCAL_TZ_STR = os.environ.get('TZ')
if LOCAL_TZ_STR:
    import pytz
    try:
        LOCAL_TZ = pytz.timezone(LOCAL_TZ_STR)
    except:
        LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc
else:
    LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc

DEFAULT_TIMEZONE: Final[timezone] = LOCAL_TZ
# 默认超时阈值（秒）
DEFAULT_TIMEOUT_SEC: Final[int] = 300  # 5分钟
MAX_TIMEOUT_SEC: Final[int] = 3600  # 1小时（超时阈值上限）
MIN_TIMEOUT_SEC: Final[int] = 60  # 1分钟（超时阈值下限）
# 健康检查响应状态
HEALTHY_STATUS: Final[str] = "healthy"
UNHEALTHY_STATUS: Final[str] = "unhealthy"
ERROR_STATUS: Final[str] = "error"
# HTTP 状态码
HTTP_200_OK: Final[int] = 200
HTTP_503_UNAVAILABLE: Final[int] = 503
HTTP_500_INTERNAL_ERROR: Final[int] = 500

# ===================== 类型定义（结构化 + 可读性） =====================
class JobStatus(TypedDict):
    """任务状态结构化类型（强制键存在性）"""
    status: str  # success/error
    last_execution: datetime
    exception: Optional[str]

# 全局任务状态存储（线程安全）
_JOB_STATUS: Dict[str, JobStatus] = {}
_JOB_STATUS_LOCK: threading.Lock = threading.Lock()

# 调度器实例存储（线程安全）
_SCHEDULER_INSTANCE: Optional[Any] = None
_SCHEDULER_LOCK: threading.Lock = threading.Lock()

# ===================== 配置数据类（中心化管理） =====================
@dataclass(frozen=True)
class HealthCheckConfig:
    """健康检查核心配置"""
    default_timeout_sec: int = DEFAULT_TIMEOUT_SEC
    max_timeout_sec: int = MAX_TIMEOUT_SEC
    min_timeout_sec: int = MIN_TIMEOUT_SEC
    timezone: timezone = DEFAULT_TIMEZONE


# 配置实例化（统一入口，便于测试替换）
HEALTH_CHECK_CONFIG: Final[HealthCheckConfig] = HealthCheckConfig()

# ===================== 调度器实例管理（线程安全） =====================
def set_scheduler_instance(scheduler: Any) -> None:
    """设置调度器实例（供健康检查模块使用，线程安全）

    Args:
        scheduler: APScheduler 调度器实例（BackgroundScheduler/BlockingScheduler）
    """
    global _SCHEDULER_INSTANCE
    with _SCHEDULER_LOCK:
        _SCHEDULER_INSTANCE = scheduler
        logger.debug("[HEALTH-011] 调度器实例已注册到健康检查模块")


def get_scheduler_instance() -> Optional[Any]:
    """获取调度器实例（线程安全）

    Returns:
        Optional[Any]: 调度器实例（None 表示未注册）
    """
    with _SCHEDULER_LOCK:
        return _SCHEDULER_INSTANCE

# ===================== 超时阈值计算（职责单一） =====================
def _calculate_cron_timeout(trigger: CronTrigger, job_id: str) -> int:
    """解析 CronTrigger 计算任务超时阈值（秒）

    Args:
        trigger: APScheduler Cron 触发器实例
        job_id: 任务ID（用于日志上下文）

    Returns:
        int: 计算后的超时阈值（秒）
    """
    fields = trigger.fields
    minute_field = next((f for f in fields if f.name == "minute"), None)
    hour_field = next((f for f in fields if f.name == "hour"), None)

    # 天级任务（固定小时/分钟）
    if hour_field and hour_field.is_static and minute_field and minute_field.is_static:
        timeout_sec = 25 * 3600  # 25小时
        logger.debug(
            f"[HEALTH-020] 任务 {job_id} 为天级 Cron 任务 | 超时阈值 {timeout_sec} 秒"
        )
        return timeout_sec

    # 小时级任务（分钟固定，小时动态）
    if hour_field and not hour_field.is_static and minute_field and minute_field.is_static:
        timeout_sec = 2 * 3600  # 2小时
        logger.debug(
            f"[HEALTH-021] 任务 {job_id} 为小时级 Cron 任务 | 超时阈值 {timeout_sec} 秒"
        )
        return timeout_sec

    # 分钟级任务
    if minute_field and not minute_field.is_static:
        expr = str(minute_field.expressions[0]) if minute_field.expressions else ""
        if expr.startswith("*/"):
            try:
                interval_min = int(expr[2:])
                timeout_sec = interval_min * 60 * 3
                timeout_sec = min(timeout_sec, HEALTH_CHECK_CONFIG.max_timeout_sec)
                logger.debug(
                    f"[HEALTH-022] 任务 {job_id} 为分钟级 Cron 任务 | 间隔 {interval_min} 分钟 | "
                    f"超时阈值 {timeout_sec} 秒"
                )
                return timeout_sec
            except (ValueError, IndexError):
                pass
        # 其他分钟级任务默认1小时
        timeout_sec = 3600
        logger.debug(
            f"[HEALTH-023] 任务 {job_id} 为分钟级 Cron 任务 | 超时阈值 {timeout_sec} 秒"
        )
        return timeout_sec

    # 默认30分钟
    timeout_sec = 1800
    logger.debug(
        f"[HEALTH-024] 任务 {job_id} 为 Cron 任务（未匹配细分类型） | 超时阈值 {timeout_sec} 秒"
    )
    return timeout_sec


def calculate_job_timeout(job_id: str) -> Optional[int]:
    """根据任务ID动态计算超时阈值（线程安全）

    规则：
    - 一次性任务（无触发器）返回 None（不检查超时）
    - CronTrigger/IntervalTrigger 按执行周期的3倍计算
    - 异常场景返回默认超时值

    Args:
        job_id: 任务唯一标识

    Returns:
        Optional[int]: 超时阈值（秒），None 表示无需检查超时

    Raises:
        Exception: 调度器访问异常（已捕获并返回默认值）
    """
    scheduler = get_scheduler_instance()
    if scheduler is None:
        logger.debug(
            f"[HEALTH-012] 调度器实例未注册 | 任务 {job_id} 使用默认超时 {HEALTH_CHECK_CONFIG.default_timeout_sec} 秒"
        )
        return HEALTH_CHECK_CONFIG.default_timeout_sec

    # 获取任务实例
    try:
        job = scheduler.get_job(job_id)
        if job is None:
            logger.warning(
                f"[HEALTH-013] 未找到任务 {job_id} | 使用默认超时 {HEALTH_CHECK_CONFIG.default_timeout_sec} 秒"
            )
            return HEALTH_CHECK_CONFIG.default_timeout_sec
    except Exception as e:
        logger.warning(
            f"[HEALTH-014] 获取任务 {job_id} 失败 | 错误: {str(e)} | "
            f"使用默认超时 {HEALTH_CHECK_CONFIG.default_timeout_sec} 秒",
            exc_info=True
        )
        return HEALTH_CHECK_CONFIG.default_timeout_sec

    # 处理一次性任务（无触发器）
    trigger = job.trigger
    if trigger is None:
        logger.debug(f"[HEALTH-015] 任务 {job_id} 为一次性任务 | 跳过超时检查")
        return None

    # 处理 IntervalTrigger
    if isinstance(trigger, IntervalTrigger):
        try:
            interval_sec = trigger.interval.total_seconds()
            timeout_sec = interval_sec * 3
            # 限制上下限
            timeout_sec = max(
                min(timeout_sec, HEALTH_CHECK_CONFIG.max_timeout_sec),
                HEALTH_CHECK_CONFIG.min_timeout_sec
            )
            logger.debug(
                f"[HEALTH-016] 任务 {job_id} 为间隔触发器 | 周期 {interval_sec} 秒 | "
                f"超时阈值 {timeout_sec} 秒"
            )
            return int(timeout_sec)
        except Exception as e:
            logger.warning(
                f"[HEALTH-017] 解析 IntervalTrigger 失败 | 任务 {job_id} | 错误: {str(e)} | "
                f"使用默认超时 1800 秒",
                exc_info=True
            )
            return 1800

    # 处理 CronTrigger
    if isinstance(trigger, CronTrigger):
        try:
            return _calculate_cron_timeout(trigger, job_id)
        except Exception as e:
            logger.warning(
                f"[HEALTH-018] 解析 CronTrigger 失败 | 任务 {job_id} | 错误: {str(e)} | "
                f"使用默认超时 1800 秒",
                exc_info=True
            )
            return 1800

    # 未知触发器类型
    logger.warning(
        f"[HEALTH-019] 任务 {job_id} 触发器类型未知 | 类型: {type(trigger)} | "
        f"使用默认超时 {HEALTH_CHECK_CONFIG.default_timeout_sec} 秒"
    )
    return HEALTH_CHECK_CONFIG.default_timeout_sec

# ===================== 任务状态管理（线程安全） =====================
def get_job_status_copy() -> Dict[str, JobStatus]:
    """获取任务状态的深拷贝（线程安全，避免外部修改）

    Returns:
        Dict[str, JobStatus]: 任务状态字典副本
            key: 任务ID
            value: JobStatus 结构化数据
    """
    with _JOB_STATUS_LOCK:
        # 深拷贝确保嵌套结构不可变
        status_copy: Dict[str, JobStatus] = {
            job_id: {
                "status": status["status"],
                "last_execution": status["last_execution"],
                "exception": status["exception"]
            }
            for job_id, status in _JOB_STATUS.items()
            # 校验键完整性，过滤无效条目
            if all(key in status for key in ["status", "last_execution", "exception"])
        }
    return status_copy


def update_job_status(
    job_id: str,
    status: str,
    exception: Optional[str] = None
) -> None:
    """更新任务状态（线程安全）

    Args:
        job_id: 任务ID
        status: 任务状态（success/error）
        exception: 异常信息（None 表示无异常）
    """
    current_time = datetime.now(HEALTH_CHECK_CONFIG.timezone)
    with _JOB_STATUS_LOCK:
        _JOB_STATUS[job_id] = {
            "status": status,
            "last_execution": current_time,
            "exception": exception
        }
    logger.debug(
        f"[HEALTH-010] 更新任务状态 | 任务 {job_id} | 状态 {status} | "
        f"时间 {current_time.isoformat()}"
    )

# ===================== 异常监听器（标准化） =====================
def exception_listener(event: Any) -> None:
    """APScheduler 任务事件监听器（捕获执行成功/失败事件）

    Args:
        event: APScheduler 事件实例（含 job_id、exception 等属性）
    """
    # 校验 job_id 非空
    job_id = getattr(event, "job_id", None)
    if not job_id:
        logger.warning("[HEALTH-001] 监听到无 job_id 的任务事件 | 忽略更新")
        return

    # 更新任务状态
    if event.exception:
        exception_str = str(event.exception)
        logger.warning(
            f"[HEALTH-002] 任务执行异常 | 任务 {job_id} | 异常: {exception_str}"
        )
        update_job_status(job_id, "error", exception_str)
    else:
        update_job_status(job_id, "success")


def _check_single_job_health(
    job_id: str,
    job_status: JobStatus,
    current_time: datetime
) -> bool:
    """校验单个任务的健康状态

    Args:
        job_id: 任务ID
        job_status: 任务状态数据
        current_time: 当前UTC时间

    Returns:
        bool: True 健康，False 不健康
    """
    # 检查任务错误状态
    if job_status["status"] == "error":
        logger.warning(
            f"[HEALTH-003] 任务异常 | 任务 {job_id} | 异常: {job_status['exception']}"
        )
        return False

    # 计算超时阈值
    timeout_sec = calculate_job_timeout(job_id)
    if timeout_sec is None:
        logger.debug("[HEALTH-004] 任务 {} 为一次性任务 | 无需检查超时", job_id)
        return True

    # 检查超时
    last_exec = job_status["last_execution"]
    time_diff_sec = (current_time - last_exec).total_seconds()
    if time_diff_sec > timeout_sec:
        logger.warning(
            f"[HEALTH-005] 任务超时未执行 | 任务 {job_id} | 上次执行 {last_exec.isoformat()} | "
            f"当前时间 {current_time.isoformat()} | 超时阈值 {timeout_sec} 秒 | "
            f"已超时 {time_diff_sec:.0f} 秒"
        )
        return False

    logger.debug(
        f"[HEALTH-006] 任务状态正常 | 任务 {job_id} | 距离上次执行 {time_diff_sec:.0f} 秒 | "
        f"超时阈值 {timeout_sec} 秒"
    )
    return True


def get_health_details() -> Dict[str, Any]:
    """获取完整的健康检查结果（一次性计算，避免重复逻辑）

    Returns:
        Dict[str, Any]: 健康检查详情
            - overall_healthy: 整体健康状态（bool）
            - job_status: 所有任务状态副本（Dict[str, JobStatus]）
            - unhealthy_jobs: 不健康任务ID列表（List[str]）
            - timestamp: 检查时间（UTC ISO格式）
    """
    # 获取线程安全的任务状态副本
    job_status_copy = get_job_status_copy()
    current_time = datetime.now(HEALTH_CHECK_CONFIG.timezone)
    unhealthy_jobs: List[str] = []

    # 无任务记录时默认健康
    if not job_status_copy:
        logger.debug("[HEALTH-007] 无任务执行记录 | 判定为整体健康")
        return {
            "overall_healthy": True,
            "job_status": job_status_copy,
            "unhealthy_jobs": [],
            "timestamp": current_time.isoformat()
        }

    # 校验每个任务的健康状态
    for job_id, status in job_status_copy.items():
        if not _check_single_job_health(job_id, status, current_time):
            unhealthy_jobs.append(job_id)

    # 判定整体健康状态
    overall_healthy = len(unhealthy_jobs) == 0
    if overall_healthy:
        logger.info("[HEALTH-008] 所有任务运行正常 | 整体健康状态: 健康")
    else:
        logger.warning(
            f"[HEALTH-009] 发现不健康任务 | 数量: {len(unhealthy_jobs)} | "
            f"任务ID: {unhealthy_jobs} | 整体健康状态: 不健康"
        )

    return {
        "overall_healthy": overall_healthy,
        "job_status": job_status_copy,
        "unhealthy_jobs": unhealthy_jobs,
        "timestamp": current_time.isoformat()
    }


def is_healthy() -> bool:
    """快速判断系统整体健康状态

    Returns:
        bool: True 健康，False 不健康
    """
    return get_health_details()["overall_healthy"]


def health_check() -> Dict[str, JobStatus]:
    """获取所有任务的当前状态副本（兼容原有接口）

    Returns:
        Dict[str, JobStatus]: 任务状态字典副本（线程安全）
    """
    return get_job_status_copy()

# ===================== FastAPI 接口（标准化） =====================
# 初始化路由（遵循 FastAPI 最佳实践 + Google 规范）
router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={404: {"description": "接口未找到"}}
)


@router.get(
    "",
    summary="获取详细健康状态（根路径）",
    description="返回所有调度任务的详细健康状态（含超时/异常信息）"
)
async def get_root_health_status() -> Dict[str, Any]:
    """获取详细健康状态接口（根路径，避免重定向）

    Returns:
        Dict[str, Any]: 详细健康状态响应
            - status: 整体状态（healthy/unhealthy/error）
            - jobs: 任务状态详情
            - unhealthy_jobs: 不健康任务列表
            - timestamp: 检查时间（UTC ISO格式）
            - message: 错误信息（仅异常时返回）
    """
    try:
        health_details = get_health_details()
        return {
            "status": HEALTHY_STATUS if health_details["overall_healthy"] else UNHEALTHY_STATUS,
            "jobs": health_details["job_status"],
            "unhealthy_jobs": health_details["unhealthy_jobs"],
            "timestamp": health_details["timestamp"]
        }
    except Exception as e:
        error_msg = f"健康检查接口异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "status": ERROR_STATUS,
            "message": error_msg,
            "timestamp": datetime.now(HEALTH_CHECK_CONFIG.timezone).isoformat()
        }


@router.get(
    "/",
    summary="获取详细健康状态",
    description="返回所有调度任务的详细健康状态（含超时/异常信息）"
)
async def get_detailed_health_status() -> Dict[str, Any]:
    """获取详细健康状态接口

    Returns:
        Dict[str, Any]: 详细健康状态响应
            - status: 整体状态（healthy/unhealthy/error）
            - jobs: 任务状态详情
            - unhealthy_jobs: 不健康任务列表
            - timestamp: 检查时间（UTC ISO格式）
            - message: 错误信息（仅异常时返回）
    """
    try:
        health_details = get_health_details()
        return {
            "status": HEALTHY_STATUS if health_details["overall_healthy"] else UNHEALTHY_STATUS,
            "jobs": health_details["job_status"],
            "unhealthy_jobs": health_details["unhealthy_jobs"],
            "timestamp": health_details["timestamp"]
        }
    except Exception as e:
        error_msg = f"健康检查接口异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "status": ERROR_STATUS,
            "message": error_msg,
            "timestamp": datetime.now(HEALTH_CHECK_CONFIG.timezone).isoformat()
        }


@router.get(
    "/status",
    summary="获取简化健康状态",
    description="返回简化健康状态（适配负载均衡/监控系统）"
)
async def get_simple_health_status(response: Response) -> Dict[str, str]:
    """获取简化健康状态接口（HTTP状态码适配监控系统）

    Args:
        response: FastAPI Response 对象（用于设置HTTP状态码）

    Returns:
        Dict[str, str]: 简化健康状态响应
            - status: 健康状态（healthy/unhealthy/error）
            - message: 错误信息（仅异常时返回）
    """
    try:
        health_details = get_health_details()
        if health_details["overall_healthy"]:
            response.status_code = HTTP_200_OK
            return {"status": HEALTHY_STATUS}
        else:
            response.status_code = HTTP_503_UNAVAILABLE
            return {"status": UNHEALTHY_STATUS}
    except Exception as e:
        error_msg = f"简化健康检查接口异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        response.status_code = HTTP_500_INTERNAL_ERROR
        return {"status": ERROR_STATUS, "message": error_msg}