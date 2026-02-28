"""
设定点监控任务模块

负责设定点变更监控等监控相关任务。

重构说明：
- 从DecisionAnalysisStaticConfig静态配置表中读取测点配置
- 实现基于数据库配置的动态监控逻辑
- 支持数字量、模拟量、枚举量的变化检测
- 优化性能，避免重复查询
"""

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.batch_yield_service import resolve_setpoint_batch_info
from utils.create_table import (
    DecisionAnalysisStaticConfig,
    query_decision_analysis_static_configs,
)
from utils.loguru_setting import logger

_DEVICE_CONFIGS_CACHE: Dict[str, Dict[str, pd.DataFrame]] = {}


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


def safe_hourly_setpoint_monitoring() -> None:
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
    from utils.task_common import check_database_connection

    if not check_database_connection():
        error_msg = "[SETPOINT_MONITOR] 数据库不可达，任务终止（按配置不启用容错）"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    max_retries = 3
    retry_delay = 5  # 秒

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[SETPOINT_MONITOR] 开始执行设定点变更监控 (尝试 {attempt}/{max_retries})"
            )
            start_time = datetime.now()

            # 执行基于静态配置表的监控
            result = execute_static_config_based_monitoring()

            # 记录执行结果
            if result["success"]:
                logger.info(
                    f"[SETPOINT_MONITOR] 设定点监控完成: 处理 {result['successful_rooms']}/{result['total_rooms']} 个库房"
                )
                logger.info(
                    f"[SETPOINT_MONITOR] 检测到 {result['total_changes']} 个设定点变更，存储 {result['stored_records']} 条记录"
                )

                # 记录有变更的库房
                changed_rooms = [
                    room_id
                    for room_id, count in result["changes_by_room"].items()
                    if count > 0
                ]
                if changed_rooms:
                    logger.info(f"[SETPOINT_MONITOR] 有变更的库房: {changed_rooms}")

                if result["error_rooms"]:
                    logger.warning(
                        f"[SETPOINT_MONITOR] 处理失败的库房: {result['error_rooms']}"
                    )
            else:
                logger.error("[SETPOINT_MONITOR] 设定点监控执行失败")

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"[SETPOINT_MONITOR] 设定点变更监控完成，耗时: {duration:.2f}秒"
            )

            # 成功执行，退出重试循环
            return

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[SETPOINT_MONITOR] 设定点变更监控失败 (尝试 {attempt}/{max_retries}): {error_msg}"
            )

            # 检查是否是数据库连接错误
            is_connection_error = any(
                keyword in error_msg.lower()
                for keyword in [
                    "timeout",
                    "connection",
                    "connect",
                    "database",
                    "server",
                ]
            )

            if is_connection_error and attempt < max_retries:
                logger.warning(
                    f"[SETPOINT_MONITOR] 检测到连接错误，{retry_delay}秒后重试..."
                )
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(
                    f"[SETPOINT_MONITOR] 设定点监控任务失败，已达到最大重试次数 ({max_retries})"
                )
                # 不再抛出异常，避免调度器崩溃
                return
            else:
                # 非连接错误，不重试
                logger.error(
                    "[SETPOINT_MONITOR] 设定点监控任务遇到非连接错误，不再重试"
                )
                return


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
        logger.info("[SETPOINT_MONITOR] 🚀 开始基于静态配置表的设定点监控")

        # 1. 从静态配置表获取所有测点配置
        logger.info("[SETPOINT_MONITOR] 📋 从静态配置表获取测点配置...")
        static_configs = get_static_configs_from_database()

        if not static_configs:
            logger.warning(
                "[SETPOINT_MONITOR] ⚠️ 静态配置表中没有找到测点配置，使用备用方案"
            )
            return execute_fallback_monitoring()

        logger.info(
            f"[SETPOINT_MONITOR] ✅ 从静态配置表获取到 {len(static_configs)} 个测点配置"
        )

        # 2. 按库房分组配置
        configs_by_room = group_configs_by_room(static_configs)
        result["total_rooms"] = len(configs_by_room)

        logger.info(
            f"[SETPOINT_MONITOR] 📍 涉及 {len(configs_by_room)} 个库房: {list(configs_by_room.keys())}"
        )

        # 3. 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)

        logger.info(f"[SETPOINT_MONITOR] ⏰ 监控时间范围: {start_time} ~ {end_time}")

        # 4. 逐个库房处理
        all_changes = []
        successful_rooms = 0

        for room_id, room_configs in configs_by_room.items():
            try:
                logger.info(
                    f"[SETPOINT_MONITOR] 🔍 处理库房 {room_id} ({len(room_configs)} 个测点)"
                )

                # 获取库房的实时数据
                room_changes = monitor_room_with_static_configs(
                    room_id, room_configs, start_time, end_time
                )

                if room_changes:
                    logger.info(
                        f"[SETPOINT_MONITOR] ✅ 库房 {room_id}: 检测到 {len(room_changes)} 个变更"
                    )
                    all_changes.extend(room_changes)
                    result["changes_by_room"][room_id] = len(room_changes)
                else:
                    logger.info(f"[SETPOINT_MONITOR] ⚪ 库房 {room_id}: 无变更")
                    result["changes_by_room"][room_id] = 0

                successful_rooms += 1

            except Exception as e:
                logger.error(f"[SETPOINT_MONITOR] ❌ 库房 {room_id} 处理失败: {e}")
                result["error_rooms"].append(room_id)
                result["changes_by_room"][room_id] = 0
                continue

        result["successful_rooms"] = successful_rooms
        result["total_changes"] = len(all_changes)

        # 5. 存储变更记录到数据库
        if all_changes:
            logger.info(
                f"[SETPOINT_MONITOR] 💾 存储 {len(all_changes)} 条变更记录到数据库..."
            )
            stored_count = store_setpoint_changes_to_database(all_changes)
            result["stored_records"] = stored_count

            if stored_count == len(all_changes):
                logger.info(f"[SETPOINT_MONITOR] ✅ 成功存储 {stored_count} 条变更记录")
            else:
                logger.warning(
                    f"[SETPOINT_MONITOR] ⚠️ 部分存储失败: {stored_count}/{len(all_changes)}"
                )
        else:
            logger.info("[SETPOINT_MONITOR] ℹ️ 无变更记录需要存储")
            result["stored_records"] = 0

        # 6. 计算处理时间
        result["processing_time"] = (datetime.now() - processing_start).total_seconds()
        result["success"] = True

        logger.info(
            f"[SETPOINT_MONITOR] 🎯 监控完成: {successful_rooms}/{len(configs_by_room)} 库房成功"
        )

        return result

    except Exception as e:
        logger.error(f"[SETPOINT_MONITOR] ❌ 静态配置监控执行失败: {e}")
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
            logger.warning("[STATIC_CONFIG] 静态配置表中没有找到启用的配置")
            return []

        now = datetime.now()
        valid_configs = [
            config
            for config in configs
            if config.effective_time is None or config.effective_time <= now
        ]

        if not valid_configs:
            logger.warning("[STATIC_CONFIG] 静态配置表中没有有效生效的配置")
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

        logger.info(f"[STATIC_CONFIG] 成功获取 {len(config_dicts)} 个静态配置")

        # 按设备类型统计
        device_type_stats = {}
        for config in config_dicts:
            device_type = config["device_type"]
            device_type_stats[device_type] = device_type_stats.get(device_type, 0) + 1

        logger.debug(f"[STATIC_CONFIG] 设备类型统计: {device_type_stats}")

        return config_dicts

    except Exception as e:
        logger.error(f"[STATIC_CONFIG] 从静态配置表获取配置失败: {e}")
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
        logger.debug(
            f"[CONFIG_GROUP] 库房 {room_id}: {len(room_configs)} 个测点, 设备类型: {device_types}"
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
        logger.debug(f"[ROOM_MONITOR] 开始监控库房 {room_id}")

        # 1. 获取实时数据
        realtime_data = get_realtime_setpoint_data(
            room_id, room_configs, start_time, end_time
        )

        if realtime_data.empty:
            logger.debug(f"[ROOM_MONITOR] 库房 {room_id} 无实时数据")
            return []

        logger.debug(
            f"[ROOM_MONITOR] 库房 {room_id} 获取到 {len(realtime_data)} 条实时数据"
        )

        # 2. 检测变更
        changes = detect_changes_with_static_configs(realtime_data, room_configs)

        # 3. 绑定批次信息
        changes = _enrich_changes_with_batch_info(changes)

        logger.debug(f"[ROOM_MONITOR] 库房 {room_id} 检测到 {len(changes)} 个变更")

        return changes

    except Exception as e:
        logger.error(f"[ROOM_MONITOR] 库房 {room_id} 监控失败: {e}")
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
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无设备配置")
            return pd.DataFrame()

        # 合并所有设备类型的配置
        all_query_df = pd.concat(device_configs.values(), ignore_index=True)

        if all_query_df.empty:
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无设备数据")
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
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无匹配的设定点数据")
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
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无历史数据")
            return pd.DataFrame()

        # 添加别名列，保持与查询返回结构一致
        df["device_alias"] = df["device_name"]
        df["point_alias"] = df["point_name"]

        # 添加库房信息
        df["room_id"] = room_id

        logger.debug(f"[REALTIME_DATA] 库房 {room_id} 获取到 {len(df)} 条实时数据")

        return df

    except Exception as e:
        logger.error(f"[REALTIME_DATA] 获取库房 {room_id} 实时数据失败: {e}")
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
            logger.error("[CHANGE_DETECT] 数据结构不匹配，无法进行分组")
            return []

        merged = realtime_df.merge(
            config_df,
            on=["device_alias", "point_alias"],
            how="inner",
            suffixes=("", "_cfg"),
        )

        if merged.empty:
            logger.debug("[CHANGE_DETECT] 无匹配配置数据")
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
            logger.warning(
                f"[CHANGE_DETECT] 检测到并移除 {duplicate_count} 条重复变化（按 {dedupe_keys}）"
            )

        logger.debug(f"[CHANGE_DETECT] 检测到 {len(result_df)} 个变更")
        return result_df.to_dict("records")

    except Exception as e:
        logger.error(f"[CHANGE_DETECT] 变更检测失败: {e}")
        import traceback

        logger.error(f"[CHANGE_DETECT] 错误详情: {traceback.format_exc()}")
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

                logger.debug(
                    f"[POINT_CHANGE] {config['device_name']}.{config['point_name']}: {change_info}"
                )

        return changes

    except Exception as e:
        logger.error(f"[POINT_CHANGE] 测点变更检测失败: {e}")
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
            logger.warning("[DB_STORE] 变更记录的 change_time 全部无效，跳过入库")
            return 0

        # 批内去重：同一批次内重复记录只保留一条
        dedupe_keys = ["room_id", "device_name", "point_name", "change_time"]
        batch_before = len(df)
        df = df.drop_duplicates(subset=dedupe_keys, keep="last").reset_index(drop=True)
        batch_removed = batch_before - len(df)
        if batch_removed > 0:
            logger.warning(
                f"[DB_STORE] 批内去重移除 {batch_removed} 条重复记录（按 {dedupe_keys}）"
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
                logger.warning(
                    f"[DB_STORE] 检测到 {already_exists} 条已存在记录，已跳过重复写入"
                )

        if df.empty:
            logger.info("[DB_STORE] 过滤重复后无新记录需要入库")
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

        logger.info(f"[DB_STORE] 成功存储 {len(df)} 条变更记录")
        return len(df)

    except Exception as e:
        logger.error(f"[DB_STORE] 存储变更记录失败: {e}")
        return 0


def execute_fallback_monitoring() -> Dict[str, Any]:
    """
    备用监控方案（当静态配置表无法访问时）

    Returns:
        Dict[str, Any]: 监控结果
    """
    logger.info("[FALLBACK] 🔄 执行备用监控方案...")

    try:
        # 导入原有的监控函数
        from utils.setpoint_change_monitor import batch_monitor_setpoint_changes

        # 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)

        logger.info(f"[FALLBACK] 监控时间范围: {start_time} ~ {end_time}")

        # 执行批量监控
        result = batch_monitor_setpoint_changes(
            start_time=start_time, end_time=end_time, store_results=True
        )

        logger.info("[FALLBACK] ✅ 备用监控方案执行完成")
        return result

    except Exception as e:
        logger.error(f"[FALLBACK] ❌ 备用监控方案失败: {e}")
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
