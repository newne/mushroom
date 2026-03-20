"""
设定点监控任务模块

负责设定点变更监控等监控相关任务。

重构说明：
- 从DecisionAnalysisStaticConfig静态配置表中读取测点配置
- 实现基于数据库配置的动态监控逻辑
- 支持数字量、模拟量、枚举量的变化检测
- 优化性能，避免重复查询
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils import build_task_run_id, log_task_event, use_log_context
from utils.batch_yield_service import resolve_setpoint_batch_info
from utils.create_table import (
    DecisionAnalysisStaticConfig,
    query_decision_analysis_static_configs,
)
from utils.task_common import (
    TaskResult,
    create_task_result,
    ensure_database_connection,
    execute_task_with_retry,
    log_task_summary,
)

_DEVICE_CONFIGS_CACHE: Dict[str, Dict[str, pd.DataFrame]] = {}


def _monitor_log(event: str, message: str, level: str = "INFO", **context: Any) -> None:
    """统一输出监控任务事件日志。"""
    log_task_event(
        "SETPOINT_MONITOR",
        event,
        message,
        level=level,
        task_type="monitoring",
        **context,
    )


def _enrich_changes_with_batch_info(
    changes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not changes:
        return changes

    Session = sessionmaker(bind=pgsql_engine)
    with Session() as session:
        for change in changes:
            info = resolve_setpoint_batch_info(
                change.get("room_id"),
                change.get("change_time"),
                db=session,
            )
            change.update(info)
    return changes


def safe_hourly_setpoint_monitoring() -> TaskResult:
    """
    每小时设定点变更监控任务（基于静态配置表的优化版本）

    功能改进：
    1. 从DecisionAnalysisStaticConfig静态配置表中获取所有测点配置信息
    2. 获取当前时间点的实时测点数据
    3. 实现对比逻辑，检测每个测点的值是否发生变化
    4. 支持数字量、模拟量、枚举量的变化检测
    5. 记录变化并存储到数据库
    6. 具备错误处理机制和性能优化
    """
    task_run_id = build_task_run_id("SETPOINT_MONITOR")
    ensure_database_connection(
        "[SETPOINT_MONITOR] 数据库不可达，任务终止（按配置不启用容错）"
    )

    max_retries = 3
    retry_delay = 5  # 秒

    def _before_attempt(attempt: int, total_attempts: int) -> None:
        log_task_event(
            "SETPOINT_MONITOR",
            "TASK_ATTEMPT_START",
            "开始执行设定点变更监控",
            task_type="monitoring",
            task_run_id=task_run_id,
            attempt=attempt,
            max_retries=total_attempts,
            status="running",
        )

    def _run_monitoring(_attempt: int, _max_retries: int) -> TaskResult:
        start_time = datetime.now()
        with use_log_context(task_run_id=task_run_id, task_type="monitoring"):
            log_task_event(
                "SETPOINT_MONITOR",
                "TASK_START",
                "进入设定点变更监控执行阶段",
                task_type="monitoring",
                task_run_id=task_run_id,
                status="running",
            )

            # 执行基于静态配置表的监控
            result = execute_static_config_based_monitoring()

        # 记录执行结果
        changed_rooms: list[str] = []
        if result["success"]:
            changed_rooms = [
                room_id
                for room_id, count in result["changes_by_room"].items()
                if count > 0
            ]

        failed_rooms_count = len(result.get("error_rooms", []))
        finish_status = "success" if failed_rooms_count == 0 else "partial"
        finish_level = "INFO" if failed_rooms_count == 0 else "WARNING"

        duration = (datetime.now() - start_time).total_seconds()
        log_task_event(
            "SETPOINT_MONITOR",
            "TASK_FINISH",
            (
                "设定点变更监控执行完成"
                f" | rooms={int(result.get('total_rooms', 0))}"
                f" success={int(result.get('successful_rooms', 0))}"
                f" failed={failed_rooms_count}"
                f" changes={int(result.get('total_changes', 0))}"
                f" stored={int(result.get('stored_records', 0))}"
            ),
            level=finish_level,
            task_type="monitoring",
            task_run_id=task_run_id,
            status=finish_status,
            total_items=int(result.get("total_rooms", 0)),
            successful_items=int(result.get("successful_rooms", 0)),
            failed_items=failed_rooms_count,
            total_changes=int(result.get("total_changes", 0)),
            stored_records=int(result.get("stored_records", 0)),
            changed_rooms=changed_rooms,
            duration_ms=round(duration * 1000, 2),
        )

        return create_task_result(
            success=bool(result.get("success")),
            total_items=int(result.get("total_rooms", 0)),
            successful_items=int(result.get("successful_rooms", 0)),
            failed_items=len(result.get("error_rooms", [])),
            error_items=result.get("error_rooms", []),
            processing_time=duration,
            additional_data={
                "task_run_id": task_run_id,
                "total_changes": int(result.get("total_changes", 0)),
                "changes_by_room": result.get("changes_by_room", {}),
                "stored_records": int(result.get("stored_records", 0)),
            },
        )

    def _on_failure(error_msg: str, _attempt: int, _total_attempts: int) -> TaskResult:
        return create_task_result(
            success=False,
            error_items=[error_msg],
            additional_data={"task_run_id": task_run_id},
        )

    result = execute_task_with_retry(
        task_name="SETPOINT_MONITOR",
        task_func=_run_monitoring,
        max_retries=max_retries,
        retry_delay=retry_delay,
        task_context={"task_type": "monitoring", "task_run_id": task_run_id},
        before_attempt=_before_attempt,
        on_non_retryable=_on_failure,
        on_exhausted=_on_failure,
    )
    log_task_summary("SETPOINT_MONITOR", result)
    return result


def execute_static_config_based_monitoring() -> Dict[str, Any]:
    """
    执行基于静态配置表的设定点监控

    核心流程：
    1. 从静态配置表获取所有测点配置
    2. 按库房分组获取实时数据
    3. 对比检测变化
    4. 存储变化记录

    Returns:
        Dict[str, Any]: 监控结果统计
    """
    result = {
        "success": False,
        "total_rooms": 0,
        "successful_rooms": 0,
        "total_changes": 0,
        "changes_by_room": {},
        "error_rooms": [],
        "stored_records": 0,
        "processing_time": 0.0,
    }

    processing_start = datetime.now()
    global _DEVICE_CONFIGS_CACHE
    _DEVICE_CONFIGS_CACHE = {}

    try:
        _monitor_log("MONITOR_PIPELINE_START", "开始基于静态配置表的设定点监控")

        # 1. 从静态配置表获取所有测点配置
        _monitor_log("STATIC_CONFIG_LOAD_START", "开始加载静态配置表测点配置")
        static_configs = get_static_configs_from_database()

        if not static_configs:
            _monitor_log(
                "STATIC_CONFIG_EMPTY",
                "静态配置表中没有找到测点配置，切换备用方案",
                level="WARNING",
                status="fallback",
            )
            return execute_fallback_monitoring()

        _monitor_log(
            "STATIC_CONFIG_LOAD_FINISH",
            "静态配置表测点配置加载完成",
            total_items=len(static_configs),
            successful_items=len(static_configs),
        )

        # 2. 按库房分组配置
        configs_by_room = group_configs_by_room(static_configs)
        result["total_rooms"] = len(configs_by_room)

        _monitor_log(
            "CONFIG_GROUP_SUMMARY",
            "静态配置按库房分组完成",
            total_items=len(configs_by_room),
            room_ids=list(configs_by_room.keys()),
        )

        # 3. 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)

        _monitor_log(
            "MONITOR_WINDOW",
            "监控时间范围已确定",
            trigger_time=end_time.isoformat(),
            window_start=start_time.isoformat(),
        )

        # 4. 逐个库房处理
        all_changes = []
        successful_rooms = 0

        for room_id, room_configs in configs_by_room.items():
            try:
                _monitor_log(
                    "ROOM_START",
                    "开始处理库房监控",
                    room_id=room_id,
                    status="running",
                    total_items=len(room_configs),
                )

                # 获取库房的实时数据
                room_changes = monitor_room_with_static_configs(
                    room_id, room_configs, start_time, end_time
                )

                if room_changes:
                    _monitor_log(
                        "ROOM_CHANGES_DETECTED",
                        "库房检测到设定点变更",
                        room_id=room_id,
                        status="success",
                        total_changes=len(room_changes),
                    )
                    all_changes.extend(room_changes)
                    result["changes_by_room"][room_id] = len(room_changes)
                else:
                    _monitor_log(
                        "ROOM_NO_CHANGES",
                        "库房未检测到设定点变更",
                        room_id=room_id,
                        status="success",
                    )
                    result["changes_by_room"][room_id] = 0

                successful_rooms += 1

            except Exception as e:
                _monitor_log(
                    "ROOM_FAILURE",
                    "库房监控处理失败",
                    level="ERROR",
                    room_id=room_id,
                    status="failed",
                    error_type=type(e).__name__,
                    error_code="room_monitor_failed",
                    error_message=str(e),
                )
                result["error_rooms"].append(room_id)
                result["changes_by_room"][room_id] = 0
                continue

        result["successful_rooms"] = successful_rooms
        result["total_changes"] = len(all_changes)

        # 5. 存储变更记录到数据库
        if all_changes:
            _monitor_log(
                "DB_STORE_START",
                "开始存储设定点变更记录",
                total_changes=len(all_changes),
            )
            stored_count = store_setpoint_changes_to_database(all_changes)
            result["stored_records"] = stored_count

            if stored_count == len(all_changes):
                _monitor_log(
                    "DB_STORE_SUCCESS",
                    "设定点变更记录存储完成",
                    stored_records=stored_count,
                    status="success",
                )
            else:
                _monitor_log(
                    "DB_STORE_PARTIAL",
                    "设定点变更记录部分存储失败",
                    level="WARNING",
                    stored_records=stored_count,
                    total_changes=len(all_changes),
                    status="degraded",
                )
        else:
            _monitor_log("DB_STORE_SKIPPED", "无变更记录需要存储", status="skipped")
            result["stored_records"] = 0

        # 6. 计算处理时间
        result["processing_time"] = (datetime.now() - processing_start).total_seconds()
        result["success"] = True

        pipeline_status = "success" if len(result["error_rooms"]) == 0 else "partial"
        pipeline_level = "DEBUG" if pipeline_status == "success" else "WARNING"

        _monitor_log(
            "MONITOR_PIPELINE_FINISH",
            (
                "静态配置监控执行完成"
                f" | rooms={len(configs_by_room)}"
                f" success={successful_rooms}"
                f" failed={len(result['error_rooms'])}"
                f" changes={result['total_changes']}"
                f" stored={result['stored_records']}"
            ),
            level=pipeline_level,
            status=pipeline_status,
            successful_items=successful_rooms,
            total_items=len(configs_by_room),
            total_changes=result["total_changes"],
            stored_records=result["stored_records"],
            duration_ms=round(result["processing_time"] * 1000, 2),
        )

        return result

    except Exception as e:
        _monitor_log(
            "MONITOR_PIPELINE_FAILED",
            "静态配置监控执行失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="monitor_pipeline_failed",
            error_message=str(e),
        )
        result["processing_time"] = (datetime.now() - processing_start).total_seconds()
        result["success"] = False
        return result


def get_static_configs_from_database() -> List[Dict[str, Any]]:
    """
    从DecisionAnalysisStaticConfig静态配置表获取所有测点配置

    Returns:
        List[Dict[str, Any]]: 测点配置列表
    """
    try:
        # 查询所有启用的静态配置
        configs = query_decision_analysis_static_configs(
            is_active=True,
            limit=10000,  # 设置较大的限制以获取所有配置
        )

        if not configs:
            _monitor_log(
                "STATIC_CONFIG_NO_ACTIVE",
                "静态配置表中没有找到启用的配置",
                level="WARNING",
                status="empty",
            )
            return []

        now = datetime.now()
        valid_configs = [
            config
            for config in configs
            if config.effective_time is None or config.effective_time <= now
        ]

        if not valid_configs:
            _monitor_log(
                "STATIC_CONFIG_NOT_EFFECTIVE",
                "静态配置表中没有有效生效的配置",
                level="WARNING",
                status="empty",
            )
            return []

        # 对同一测点选择最新版本配置（按 config_version / effective_time）
        latest_by_key: Dict[tuple[str, str, str], DecisionAnalysisStaticConfig] = {}
        for config in valid_configs:
            key = (config.room_id, config.device_alias, config.point_alias)
            existing = latest_by_key.get(key)
            if not existing:
                latest_by_key[key] = config
                continue

            existing_version = existing.config_version or 0
            current_version = config.config_version or 0
            if current_version > existing_version:
                latest_by_key[key] = config
            elif current_version == existing_version:
                existing_time = existing.effective_time or existing.created_at
                current_time = config.effective_time or config.created_at
                if current_time and existing_time and current_time > existing_time:
                    latest_by_key[key] = config

        # 转换为字典格式
        config_dicts = []
        for config in latest_by_key.values():
            config_dict = {
                "id": str(config.id),
                "room_id": config.room_id,
                "device_type": config.device_type,
                "device_name": config.device_name,
                "device_alias": config.device_alias,
                "point_alias": config.point_alias,
                "point_name": config.point_name,
                "remark": config.remark,
                "change_type": config.change_type,
                "threshold": config.threshold,
                "enum_mapping": config.enum_mapping or {},
                "config_version": config.config_version,
                "effective_time": config.effective_time,
                "created_at": config.created_at,
            }
            config_dicts.append(config_dict)

        _monitor_log(
            "STATIC_CONFIG_READY",
            "静态配置数据准备完成",
            total_items=len(config_dicts),
            successful_items=len(config_dicts),
        )

        # 按设备类型统计
        device_type_stats = {}
        for config in config_dicts:
            device_type = config["device_type"]
            device_type_stats[device_type] = device_type_stats.get(device_type, 0) + 1

        _monitor_log(
            "STATIC_CONFIG_DEVICE_TYPES",
            "静态配置设备类型统计完成",
            level="DEBUG",
            device_type_stats=device_type_stats,
        )

        return config_dicts

    except Exception as e:
        _monitor_log(
            "STATIC_CONFIG_FAILED",
            "从静态配置表获取配置失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="static_config_failed",
            error_message=str(e),
        )
        return []


def group_configs_by_room(
    static_configs: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    按库房分组静态配置

    Args:
        static_configs: 静态配置列表

    Returns:
        Dict[str, List[Dict[str, Any]]]: 按库房分组的配置
    """
    configs_by_room = {}

    for config in static_configs:
        room_id = config["room_id"]
        if room_id not in configs_by_room:
            configs_by_room[room_id] = []
        configs_by_room[room_id].append(config)

    # 按库房统计
    for room_id, room_configs in configs_by_room.items():
        device_types = set(config["device_type"] for config in room_configs)
        _monitor_log(
            "CONFIG_GROUP_ROOM_SUMMARY",
            "库房静态配置分组摘要",
            level="DEBUG",
            room_id=room_id,
            total_items=len(room_configs),
            device_types=sorted(device_types),
        )

    return configs_by_room


def monitor_room_with_static_configs(
    room_id: str,
    room_configs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> List[Dict[str, Any]]:
    """
    使用静态配置监控单个库房的设定点变更

    Args:
        room_id: 库房编号
        room_configs: 库房的测点配置列表
        start_time: 开始时间
        end_time: 结束时间

    Returns:
        List[Dict[str, Any]]: 检测到的变更记录
    """
    try:
        _monitor_log(
            "ROOM_MONITOR_START", "开始监控单个库房", level="DEBUG", room_id=room_id
        )

        # 1. 获取实时数据
        realtime_data = get_realtime_setpoint_data(
            room_id, room_configs, start_time, end_time
        )

        if realtime_data.empty:
            _monitor_log(
                "ROOM_MONITOR_NO_REALTIME_DATA",
                "库房无实时数据",
                level="DEBUG",
                room_id=room_id,
                status="empty",
            )
            return []

        _monitor_log(
            "ROOM_MONITOR_REALTIME_READY",
            "库房实时数据加载完成",
            level="DEBUG",
            room_id=room_id,
            total_items=len(realtime_data),
        )

        # 2. 检测变更
        changes = detect_changes_with_static_configs(realtime_data, room_configs)

        # 3. 绑定批次信息
        changes = _enrich_changes_with_batch_info(changes)

        _monitor_log(
            "ROOM_MONITOR_FINISH",
            "库房监控完成",
            level="DEBUG",
            room_id=room_id,
            total_changes=len(changes),
        )

        return changes

    except Exception as e:
        _monitor_log(
            "ROOM_MONITOR_FAILED",
            "库房监控失败",
            level="ERROR",
            room_id=room_id,
            status="failed",
            error_type=type(e).__name__,
            error_code="room_monitor_failed",
            error_message=str(e),
        )
        return []


def get_realtime_setpoint_data(
    room_id: str,
    room_configs: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    """
    获取库房的实时设定点数据

    Args:
        room_id: 库房编号
        room_configs: 测点配置列表
        start_time: 开始时间
        end_time: 结束时间

    Returns:
        pd.DataFrame: 实时数据
    """
    try:
        # 导入数据获取模块 - 修复容器环境中的导入路径
        # 使用BASE_DIR统一管理路径
        from global_const.global_const import ensure_src_path

        ensure_src_path()

        from utils.data_preprocessing import query_data_by_batch_time
        from utils.dataframe_utils import get_all_device_configs

        # 获取库房设备配置（批量执行时缓存，避免重复加载）
        device_configs = _DEVICE_CONFIGS_CACHE.get(room_id)
        if device_configs is None:
            device_configs = get_all_device_configs(room_id=room_id)
            _DEVICE_CONFIGS_CACHE[room_id] = device_configs
        if not device_configs:
            _monitor_log(
                "REALTIME_DATA_NO_DEVICE_CONFIG",
                "库房无设备配置",
                level="WARNING",
                room_id=room_id,
                status="empty",
            )
            return pd.DataFrame()

        # 合并所有设备类型的配置
        all_query_df = pd.concat(device_configs.values(), ignore_index=True)

        if all_query_df.empty:
            _monitor_log(
                "REALTIME_DATA_NO_DEVICE_DATA",
                "库房无设备数据",
                level="WARNING",
                room_id=room_id,
                status="empty",
            )
            return pd.DataFrame()

        # 只保留静态配置中定义的测点
        if (
            "device_alias" not in all_query_df.columns
            and "device_name" in all_query_df.columns
        ):
            all_query_df = all_query_df.rename(columns={"device_name": "device_alias"})

        config_keys_df = (
            pd.DataFrame(room_configs)[["device_alias", "point_alias"]]
            .drop_duplicates()
            .reset_index(drop=True)
        )
        setpoint_df = all_query_df.merge(
            config_keys_df, on=["device_alias", "point_alias"], how="inner"
        )

        if setpoint_df.empty:
            _monitor_log(
                "REALTIME_DATA_NO_SETPOINT_MATCH",
                "库房无匹配的设定点数据",
                level="WARNING",
                room_id=room_id,
                status="empty",
            )
            return pd.DataFrame()

        # 查询历史数据
        df = (
            setpoint_df.groupby(
                "device_alias", group_keys=False, sort=False, observed=True
            )
            .apply(
                lambda group: query_data_by_batch_time(
                    group.assign(device_alias=group.name),
                    start_time,
                    end_time,
                ),
                include_groups=False,
            )
            .reset_index(drop=True)
            .sort_values("time")
        )

        if df.empty:
            _monitor_log(
                "REALTIME_DATA_NO_HISTORY",
                "库房无历史数据",
                level="WARNING",
                room_id=room_id,
                status="empty",
            )
            return pd.DataFrame()

        # 添加别名列，保持与查询返回结构一致
        df["device_alias"] = df["device_name"]
        df["point_alias"] = df["point_name"]

        # 添加库房信息
        df["room_id"] = room_id

        _monitor_log(
            "REALTIME_DATA_READY",
            "库房实时数据获取完成",
            level="DEBUG",
            room_id=room_id,
            total_items=len(df),
        )

        return df

    except Exception as e:
        _monitor_log(
            "REALTIME_DATA_FAILED",
            "获取库房实时数据失败",
            level="ERROR",
            room_id=room_id,
            status="failed",
            error_type=type(e).__name__,
            error_code="realtime_data_failed",
            error_message=str(e),
        )
        return pd.DataFrame()


def detect_changes_with_static_configs(
    realtime_data: pd.DataFrame, room_configs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    使用静态配置检测设定点变更

    Args:
        realtime_data: 实时数据
        room_configs: 测点配置列表

    Returns:
        List[Dict[str, Any]]: 变更记录列表
    """
    if realtime_data.empty:
        return []

    try:
        realtime_df = realtime_data.copy()
        if (
            "device_alias" not in realtime_df.columns
            and "device_name" in realtime_df.columns
        ):
            realtime_df["device_alias"] = realtime_df["device_name"]
        if (
            "point_alias" not in realtime_df.columns
            and "point_name" in realtime_df.columns
        ):
            realtime_df["point_alias"] = realtime_df["point_name"]

        config_df = pd.DataFrame(room_configs)
        required_cols = {"device_alias", "point_alias"}
        if not required_cols.issubset(realtime_df.columns) or config_df.empty:
            _monitor_log(
                "CHANGE_DETECT_INVALID_SCHEMA",
                "数据结构不匹配，无法进行分组",
                level="ERROR",
                status="failed",
                error_code="change_detect_invalid_schema",
            )
            return []

        merged = realtime_df.merge(
            config_df,
            on=["device_alias", "point_alias"],
            how="inner",
            suffixes=("", "_cfg"),
        )

        if merged.empty:
            _monitor_log(
                "CHANGE_DETECT_NO_MATCH",
                "未匹配到可用于检测的配置数据",
                level="DEBUG",
                status="empty",
            )
            return []

        merged = merged.sort_values("time")
        group_keys = ["device_alias", "point_alias"]
        merged["previous_value"] = merged.groupby(group_keys)["value"].shift(1)

        valid_mask = merged["value"].notna() & merged["previous_value"].notna()
        if not valid_mask.any():
            return []

        value_num = pd.to_numeric(merged["value"], errors="coerce")
        prev_num = pd.to_numeric(merged["previous_value"], errors="coerce")
        value_int = value_num.round().astype("Int64")
        prev_int = prev_num.round().astype("Int64")
        delta = (value_num - prev_num).abs()
        threshold = merged["threshold"].fillna(0.0)

        digital_mask = (merged["change_type"] == "digital_on_off") & (
            value_int != prev_int
        )
        analog_mask = (merged["change_type"] == "analog_value") & (delta >= threshold)
        enum_mask = (merged["change_type"] == "enum_state") & (value_int != prev_int)

        change_mask = valid_mask & (digital_mask | analog_mask | enum_mask)
        if not change_mask.any():
            return []

        changes_df = merged.loc[change_mask].copy()

        changes_df["detection_time"] = datetime.now()

        result_df = changes_df[
            [
                "room_id",
                "device_type",
                "device_name_cfg",
                "point_name_cfg",
                "remark",
                "time",
                "previous_value",
                "value",
                "change_type",
                "detection_time",
            ]
        ].rename(
            columns={
                "device_name_cfg": "device_name",
                "point_name_cfg": "point_name",
                "remark": "point_description",
                "time": "change_time",
                "value": "current_value",
            }
        )

        # 保护性去重：同一库房同一设备测点同一时刻的重复变化仅保留一条
        dedupe_keys = ["room_id", "device_name", "point_name", "change_time"]
        before_dedupe = len(result_df)
        result_df = result_df.drop_duplicates(subset=dedupe_keys, keep="last")
        duplicate_count = before_dedupe - len(result_df)
        if duplicate_count > 0:
            _monitor_log(
                "CHANGE_DETECT_DUPLICATES_REMOVED",
                "检测到并移除重复变化记录",
                level="WARNING",
                failed_items=duplicate_count,
                dedupe_keys=dedupe_keys,
                status="deduplicated",
            )

        _monitor_log(
            "CHANGE_DETECT_FINISH",
            "变更检测完成",
            level="DEBUG",
            total_changes=len(result_df),
        )
        return result_df.to_dict("records")

    except Exception as e:
        _monitor_log(
            "CHANGE_DETECT_FAILED",
            "变更检测失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="change_detect_failed",
            error_message=str(e),
        )
        import traceback

        _monitor_log(
            "CHANGE_DETECT_TRACEBACK",
            "变更检测异常堆栈",
            level="DEBUG",
            error_stack=traceback.format_exc(),
        )
        return []


def detect_point_changes(
    group: pd.DataFrame, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    检测单个测点的变更

    Args:
        group: 测点的时间序列数据
        config: 测点配置

    Returns:
        List[Dict[str, Any]]: 变更记录列表
    """
    changes = []

    try:
        change_type = config["change_type"]
        threshold = config.get("threshold")
        enum_mapping = config.get("enum_mapping", {})

        for i in range(1, len(group)):
            current_row = group.iloc[i]
            previous_row = group.iloc[i - 1]

            current_value = current_row["value"]
            previous_value = previous_row["value"]

            # 跳过无效值
            if pd.isna(current_value) or pd.isna(previous_value):
                continue

            change_detected = False
            change_info = {}

            # 根据变更类型检测变化
            if change_type == "digital_on_off":
                # 数字量开关变化检测
                if int(current_value) != int(previous_value):
                    change_detected = True
                    change_info = f"{int(previous_value)} -> {int(current_value)}"

            elif change_type == "analog_value":
                # 模拟量变化检测（无阈值时默认记录任意变化）
                effective_threshold = 0.0 if threshold is None else threshold
                if abs(current_value - previous_value) >= effective_threshold:
                    change_detected = True
                    change_info = f"{previous_value:.2f} -> {current_value:.2f}"

            elif change_type == "enum_state":
                # 枚举状态变化检测
                if int(current_value) != int(previous_value):
                    change_detected = True
                    # 使用枚举映射获取状态描述
                    prev_desc = enum_mapping.get(
                        str(int(previous_value)), str(int(previous_value))
                    )
                    curr_desc = enum_mapping.get(
                        str(int(current_value)), str(int(current_value))
                    )
                    change_info = f"{prev_desc} -> {curr_desc}"

            if change_detected:
                change_record = {
                    "room_id": config["room_id"],
                    "device_type": config["device_type"],
                    "device_name": config["device_name"],
                    "point_name": config["point_name"],
                    "point_description": config.get("remark", ""),
                    "change_time": current_row["time"],
                    "previous_value": float(previous_value),
                    "current_value": float(current_value),
                    "change_type": change_type,
                    "detection_time": datetime.now(),
                }
                changes.append(change_record)

                _monitor_log(
                    "POINT_CHANGE_DETECTED",
                    "检测到测点变更",
                    level="DEBUG",
                    room_id=config["room_id"],
                    device_name=config["device_name"],
                    point_name=config["point_name"],
                    change_info=change_info,
                )

        return changes

    except Exception as e:
        _monitor_log(
            "POINT_CHANGE_FAILED",
            "测点变更检测失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="point_change_failed",
            error_message=str(e),
        )
        return []


def store_setpoint_changes_to_database(changes: List[Dict[str, Any]]) -> int:
    """
    存储设定点变更记录到数据库

    Args:
        changes: 变更记录列表

    Returns:
        int: 成功存储的记录数
    """
    if not changes:
        return 0

    try:
        # 转换为DataFrame
        df = pd.DataFrame(changes)

        if df.empty:
            return 0

        # 统一时间字段，避免字符串/时区差异导致重复判定失效
        df["change_time"] = pd.to_datetime(df["change_time"], errors="coerce")
        df = df.dropna(subset=["change_time"])

        if df.empty:
            _monitor_log(
                "DB_STORE_INVALID_CHANGE_TIME",
                "变更记录的 change_time 全部无效，跳过入库",
                level="WARNING",
                status="skipped",
            )
            return 0

        # 批内去重：同一批次内重复记录只保留一条
        dedupe_keys = ["room_id", "device_name", "point_name", "change_time"]
        batch_before = len(df)
        df = df.drop_duplicates(subset=dedupe_keys, keep="last").reset_index(drop=True)
        batch_removed = batch_before - len(df)
        if batch_removed > 0:
            _monitor_log(
                "DB_STORE_BATCH_DEDUP",
                "批内去重移除重复记录",
                level="WARNING",
                failed_items=batch_removed,
                dedupe_keys=dedupe_keys,
                status="deduplicated",
            )

        # 库内幂等：过滤数据库中已经存在的相同主键记录，避免重试/重跑重复写入
        min_change_time = df["change_time"].min()
        max_change_time = df["change_time"].max()
        room_ids = sorted(df["room_id"].dropna().astype(str).unique().tolist())

        existing_sql = text(
            """
            SELECT room_id, device_name, point_name, change_time
            FROM device_setpoint_changes
            WHERE change_time BETWEEN :min_change_time AND :max_change_time
              AND room_id IN :room_ids
            """
        ).bindparams(bindparam("room_ids", expanding=True))

        existing_df = pd.read_sql(
            existing_sql,
            con=pgsql_engine,
            params={
                "min_change_time": min_change_time,
                "max_change_time": max_change_time,
                "room_ids": room_ids,
            },
        )

        if not existing_df.empty:
            existing_df["change_time"] = pd.to_datetime(
                existing_df["change_time"], errors="coerce"
            )
            existing_df = existing_df.dropna(subset=["change_time"]).drop_duplicates(
                subset=dedupe_keys
            )

            df = df.merge(
                existing_df[dedupe_keys],
                on=dedupe_keys,
                how="left",
                indicator=True,
            )
            already_exists = int((df["_merge"] == "both").sum())
            df = df[df["_merge"] == "left_only"].drop(columns=["_merge"])

            if already_exists > 0:
                _monitor_log(
                    "DB_STORE_ALREADY_EXISTS",
                    "检测到已存在记录，已跳过重复写入",
                    level="WARNING",
                    skipped_items=already_exists,
                    status="deduplicated",
                )

        if df.empty:
            _monitor_log(
                "DB_STORE_NO_NEW_RECORDS",
                "过滤重复后无新记录需要入库",
                status="skipped",
            )
            return 0

        # 存储到数据库
        df.to_sql(
            "device_setpoint_changes",
            con=pgsql_engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )

        _monitor_log(
            "DB_STORE_FINISH",
            "设定点变更记录已写入数据库",
            status="success",
            stored_records=len(df),
        )
        return len(df)

    except Exception as e:
        _monitor_log(
            "DB_STORE_FAILED",
            "存储设定点变更记录失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="db_store_failed",
            error_message=str(e),
        )
        return 0


def execute_fallback_monitoring() -> Dict[str, Any]:
    """
    备用监控方案（当静态配置表无法访问时）

    Returns:
        Dict[str, Any]: 监控结果
    """
    _monitor_log(
        "FALLBACK_START", "执行备用监控方案", level="WARNING", status="fallback"
    )

    try:
        # 导入原有的监控函数
        from utils.setpoint_change_monitor import batch_monitor_setpoint_changes

        # 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)

        _monitor_log(
            "FALLBACK_WINDOW",
            "备用监控方案时间范围已确定",
            trigger_time=end_time.isoformat(),
            window_start=start_time.isoformat(),
        )

        # 执行批量监控
        result = batch_monitor_setpoint_changes(
            start_time=start_time, end_time=end_time, store_results=True
        )

        _monitor_log("FALLBACK_FINISH", "备用监控方案执行完成", status="success")
        return result

    except Exception as e:
        _monitor_log(
            "FALLBACK_FAILED",
            "备用监控方案失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_code="fallback_failed",
            error_message=str(e),
        )
        return {
            "success": False,
            "total_rooms": 0,
            "successful_rooms": 0,
            "total_changes": 0,
            "changes_by_room": {},
            "error_rooms": [],
            "stored_records": 0,
            "processing_time": 0.0,
        }
