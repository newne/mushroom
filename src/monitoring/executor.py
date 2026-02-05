"""
设定点监控任务执行器

专门负责设定点变更监控和分析。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from global_const.const_config import MUSHROOM_ROOM_IDS
from global_const.global_const import pgsql_engine
from tasks.base_task import BaseTask
from utils.create_table import (
    DecisionAnalysisStaticConfig,
    query_decision_analysis_static_configs,
)
from utils.loguru_setting import logger


class SetpointMonitoringTask(BaseTask):
    """设定点监控任务执行器"""

    def __init__(self):
        """初始化设定点监控任务"""
        super().__init__(task_name="SETPOINT_MONITORING", max_retries=3, retry_delay=5)

        self.rooms = MUSHROOM_ROOM_IDS
        self._device_configs_cache: Dict[str, Dict[str, pd.DataFrame]] = {}

    def execute_task(self) -> Dict[str, Any]:
        """
        执行基于静态配置表的设定点监控

        Returns:
            Dict[str, Any]: 监控结果
        """
        logger.info(f"[{self.task_name}] 🚀 开始基于静态配置表的设定点监控")
        processing_start = datetime.now()
        self._device_configs_cache = {}

        # 1. 从静态配置表获取所有测点配置
        logger.info(f"[{self.task_name}] 📋 从静态配置表获取测点配置...")
        static_configs = self._get_static_configs_from_database()

        if not static_configs:
            logger.warning(
                f"[{self.task_name}] ⚠️ 静态配置表中没有找到测点配置，使用备用方案"
            )
            return self._execute_fallback_monitoring()

        logger.info(
            f"[{self.task_name}] ✅ 从静态配置表获取到 {len(static_configs)} 个测点配置"
        )

        # 2. 按库房分组配置
        configs_by_room = self._group_configs_by_room(static_configs)

        logger.info(
            f"[{self.task_name}] 📍 涉及 {len(configs_by_room)} 个库房: {list(configs_by_room.keys())}"
        )

        # 3. 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)

        logger.info(f"[{self.task_name}] ⏰ 监控时间范围: {start_time} ~ {end_time}")

        # 4. 逐个库房处理
        all_changes = []
        successful_rooms = 0
        changes_by_room = {}
        error_rooms = []

        for room_id, room_configs in configs_by_room.items():
            try:
                logger.info(
                    f"[{self.task_name}] 🔍 处理库房 {room_id} ({len(room_configs)} 个测点)"
                )

                # 获取库房的实时数据
                room_changes = self._monitor_room_with_static_configs(
                    room_id, room_configs, start_time, end_time
                )

                if room_changes:
                    logger.info(
                        f"[{self.task_name}] ✅ 库房 {room_id}: 检测到 {len(room_changes)} 个变更"
                    )
                    all_changes.extend(room_changes)
                    changes_by_room[room_id] = len(room_changes)
                else:
                    logger.info(f"[{self.task_name}] ⚪ 库房 {room_id}: 无变更")
                    changes_by_room[room_id] = 0

                successful_rooms += 1

            except Exception as e:
                logger.error(f"[{self.task_name}] ❌ 库房 {room_id} 处理失败: {e}")
                error_rooms.append(room_id)
                changes_by_room[room_id] = 0
                continue

        # 5. 存储变更记录到数据库
        stored_records = 0
        if all_changes:
            logger.info(
                f"[{self.task_name}] 💾 存储 {len(all_changes)} 条变更记录到数据库..."
            )
            stored_records = self._store_setpoint_changes_to_database(all_changes)

            if stored_records == len(all_changes):
                logger.info(
                    f"[{self.task_name}] ✅ 成功存储 {stored_records} 条变更记录"
                )
            else:
                logger.warning(
                    f"[{self.task_name}] ⚠️ 部分存储失败: {stored_records}/{len(all_changes)}"
                )
        else:
            logger.info(f"[{self.task_name}] ℹ️ 无变更记录需要存储")

        processing_time = (datetime.now() - processing_start).total_seconds()

        logger.info(
            f"[{self.task_name}] 🎯 监控完成: {successful_rooms}/{len(configs_by_room)} 库房成功"
        )

        return self._create_success_result(
            total_rooms=len(configs_by_room),
            successful_rooms=successful_rooms,
            total_changes=len(all_changes),
            changes_by_room=changes_by_room,
            error_rooms=error_rooms,
            stored_records=stored_records,
            processing_time=processing_time,
            monitoring_period=f"{start_time} ~ {end_time}",
        )

    def _get_static_configs_from_database(self) -> List[Dict[str, Any]]:
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
                logger.warning(f"[{self.task_name}] 静态配置表中没有找到启用的配置")
                return []

            now = datetime.now()
            valid_configs = [
                config
                for config in configs
                if config.effective_time is None or config.effective_time <= now
            ]

            if not valid_configs:
                logger.warning(f"[{self.task_name}] 静态配置表中没有有效生效的配置")
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

            logger.info(f"[{self.task_name}] 成功获取 {len(config_dicts)} 个静态配置")

            # 按设备类型统计
            device_type_stats = {}
            for config in config_dicts:
                device_type = config["device_type"]
                device_type_stats[device_type] = (
                    device_type_stats.get(device_type, 0) + 1
                )

            logger.debug(f"[{self.task_name}] 设备类型统计: {device_type_stats}")

            return config_dicts

        except Exception as e:
            logger.error(f"[{self.task_name}] 从静态配置表获取配置失败: {e}")
            return []

    def _group_configs_by_room(
        self, static_configs: List[Dict[str, Any]]
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
                f"[{self.task_name}] 库房 {room_id}: {len(room_configs)} 个测点, 设备类型: {device_types}"
            )

        return configs_by_room

    def _monitor_room_with_static_configs(
        self,
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
            logger.debug(f"[{self.task_name}] 开始监控库房 {room_id}")

            # 1. 获取实时数据
            realtime_data = self._get_realtime_setpoint_data(
                room_id, room_configs, start_time, end_time
            )

            if realtime_data.empty:
                logger.debug(f"[{self.task_name}] 库房 {room_id} 无实时数据")
                return []

            logger.debug(
                f"[{self.task_name}] 库房 {room_id} 获取到 {len(realtime_data)} 条实时数据"
            )

            # 2. 检测变更
            changes = self._detect_changes_with_static_configs(
                realtime_data, room_configs
            )

            logger.debug(
                f"[{self.task_name}] 库房 {room_id} 检测到 {len(changes)} 个变更"
            )

            return changes

        except Exception as e:
            logger.error(f"[{self.task_name}] 库房 {room_id} 监控失败: {e}")
            return []

    def _get_realtime_setpoint_data(
        self,
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
            # 使用BASE_DIR统一管理路径
            from global_const.global_const import ensure_src_path

            ensure_src_path()
            from utils.data_preprocessing import query_data_by_batch_time
            from utils.dataframe_utils import get_all_device_configs

            # 获取库房设备配置
            device_configs = get_all_device_configs(room_id=room_id)
            if not device_configs:
                logger.warning(f"[{self.task_name}] 库房 {room_id} 无设备配置")
                return pd.DataFrame()

            # 合并所有设备类型的配置
            all_query_df = pd.concat(device_configs.values(), ignore_index=True)

            if all_query_df.empty:
                logger.warning(f"[{self.task_name}] 库房 {room_id} 无设备数据")
                return pd.DataFrame()

            # 只保留静态配置中定义的测点
            config_point_aliases = {config["point_alias"] for config in room_configs}
            setpoint_df = all_query_df[
                all_query_df["point_alias"].isin(config_point_aliases)
            ].copy()

            if setpoint_df.empty:
                logger.warning(f"[{self.task_name}] 库房 {room_id} 无匹配的设定点数据")
                return pd.DataFrame()

            # 查询历史数据
            df = (
                setpoint_df.groupby("device_alias", group_keys=False)
                .apply(query_data_by_batch_time, start_time, end_time)
                .reset_index(drop=True)
                .sort_values("time")
            )

            if df.empty:
                logger.warning(f"[{self.task_name}] 库房 {room_id} 无历史数据")
                return pd.DataFrame()

            # 添加库房信息
            df["room_id"] = room_id

            logger.debug(
                f"[{self.task_name}] 库房 {room_id} 获取到 {len(df)} 条实时数据"
            )

            return df

        except Exception as e:
            logger.error(f"[{self.task_name}] 获取库房 {room_id} 实时数据失败: {e}")
            return pd.DataFrame()

    def _detect_changes_with_static_configs(
        self, realtime_data: pd.DataFrame, room_configs: List[Dict[str, Any]]
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

        changes = []

        try:
            # 构建配置映射表
            config_mapping = {}
            config_mapping_by_name = {}
            for config in room_configs:
                alias_key = f"{config['device_alias']}_{config['point_alias']}"
                name_key = f"{config['device_name']}_{config['point_name']}"
                config_mapping[alias_key] = config
                config_mapping_by_name[name_key] = config

            # 检查数据结构
            logger.debug(
                f"[{self.task_name}] 实时数据列: {list(realtime_data.columns)}"
            )

            # 根据实际数据结构选择分组字段
            if (
                "device_alias" in realtime_data.columns
                and "point_name" in realtime_data.columns
            ):
                # 按设备和测点分组检测变更
                grouped_data = realtime_data.groupby(["device_alias", "point_name"])
                group_key_format = "device_alias_point_name"
            elif (
                "device_name" in realtime_data.columns
                and "point_name" in realtime_data.columns
            ):
                # 备用分组方式
                grouped_data = realtime_data.groupby(["device_name", "point_name"])
                group_key_format = "device_name_point_name"
            else:
                logger.error(f"[{self.task_name}] 数据结构不匹配，无法进行分组")
                return []

            logger.debug(f"[{self.task_name}] 使用分组方式: {group_key_format}")

            for group_key, group in grouped_data:
                if len(group) < 2:
                    continue  # 至少需要2个数据点才能检测变更

                # 根据分组方式构建配置键
                if group_key_format == "device_alias_point_name":
                    device_alias, point_name = group_key
                    config_key = (
                        f"{device_alias}_{point_name}"  # point_name实际是point_alias
                    )
                    config = config_mapping.get(config_key)
                else:
                    device_name, point_name = group_key
                    # 先尝试把 realtime 的 device_name 当作 alias 使用
                    alias_key = f"{device_name}_{point_name}"
                    config = config_mapping.get(alias_key)
                    if not config:
                        name_key = f"{device_name}_{point_name}"
                        config = config_mapping_by_name.get(name_key)

                if not config:
                    logger.debug(f"[{self.task_name}] 未找到匹配配置: {group_key}")
                    continue

                # 按时间排序
                group = group.sort_values("time").reset_index(drop=True)

                # 检测变更
                group_changes = self._detect_point_changes(group, config)
                changes.extend(group_changes)

            logger.debug(f"[{self.task_name}] 检测到 {len(changes)} 个变更")

            return changes

        except Exception as e:
            logger.error(f"[{self.task_name}] 变更检测失败: {e}")
            import traceback

            logger.error(f"[{self.task_name}] 错误详情: {traceback.format_exc()}")
            return []

    def _detect_point_changes(
        self, group: pd.DataFrame, config: Dict[str, Any]
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
                        change_info = {
                            "change_detail": f"{int(previous_value)} -> {int(current_value)}",
                            "change_magnitude": abs(current_value - previous_value),
                        }

                elif change_type == "analog_value":
                    # 模拟量变化检测
                    if threshold and abs(current_value - previous_value) >= threshold:
                        change_detected = True
                        change_info = {
                            "change_detail": f"{previous_value:.2f} -> {current_value:.2f}",
                            "change_magnitude": abs(current_value - previous_value),
                        }

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
                        change_info = {
                            "change_detail": f"{prev_desc} -> {curr_desc}",
                            "change_magnitude": abs(current_value - previous_value),
                        }

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
                        "change_detail": change_info.get("change_detail", ""),
                        "change_magnitude": change_info.get("change_magnitude", 0.0),
                        "detection_time": datetime.now(),
                    }
                    changes.append(change_record)

                    logger.debug(
                        f"[{self.task_name}] {config['device_name']}.{config['point_name']}: {change_info.get('change_detail', '')}"
                    )

            return changes

        except Exception as e:
            logger.error(f"[{self.task_name}] 测点变更检测失败: {e}")
            return []

    def _store_setpoint_changes_to_database(self, changes: List[Dict[str, Any]]) -> int:
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

            # 存储到数据库
            stored_count = df.to_sql(
                "device_setpoint_changes",
                con=pgsql_engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1000,
            )

            logger.info(f"[{self.task_name}] 成功存储 {len(changes)} 条变更记录")
            return len(changes)

        except Exception as e:
            logger.error(f"[{self.task_name}] 存储变更记录失败: {e}")
            return 0

    def _execute_fallback_monitoring(self) -> Dict[str, Any]:
        """
        备用监控方案（当静态配置表无法访问时）

        Returns:
            Dict[str, Any]: 监控结果
        """
        logger.info(f"[{self.task_name}] 🔄 执行备用监控方案...")

        try:
            # 导入原有的监控函数
            from utils.setpoint_change_monitor import batch_monitor_setpoint_changes

            # 设定监控时间范围（最近1小时）
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=1)

            logger.info(f"[{self.task_name}] 监控时间范围: {start_time} ~ {end_time}")

            # 执行批量监控
            result = batch_monitor_setpoint_changes(
                start_time=start_time, end_time=end_time, store_results=True
            )

            logger.info(f"[{self.task_name}] ✅ 备用监控方案执行完成")
            return result

        except Exception as e:
            logger.error(f"[{self.task_name}] ❌ 备用监控方案失败: {e}")
            return self._create_success_result(
                total_rooms=0,
                successful_rooms=0,
                total_changes=0,
                changes_by_room={},
                error_rooms=[],
                stored_records=0,
                processing_time=0.0,
                error="备用监控方案失败",
            )

    def get_monitoring_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        获取监控摘要

        Args:
            hours: 查询小时数

        Returns:
            Dict[str, Any]: 监控摘要
        """
        try:
            from sqlalchemy import text

            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)

            with pgsql_engine.connect() as conn:
                result = conn.execute(
                    text("""
                    SELECT 
                        room_id,
                        COUNT(*) as change_count,
                        COUNT(DISTINCT device_name) as affected_devices,
                        COUNT(DISTINCT change_type) as change_types
                    FROM device_setpoint_changes 
                    WHERE change_time BETWEEN :start_time AND :end_time
                    GROUP BY room_id
                    ORDER BY change_count DESC
                """),
                    {"start_time": start_time, "end_time": end_time},
                )

                room_summaries = {}
                for row in result:
                    room_summaries[row[0]] = {
                        "change_count": row[1],
                        "affected_devices": row[2],
                        "change_types": row[3],
                    }

                # 总体统计
                total_result = conn.execute(
                    text("""
                    SELECT 
                        COUNT(*) as total_changes,
                        COUNT(DISTINCT room_id) as affected_rooms,
                        COUNT(DISTINCT device_name) as total_affected_devices
                    FROM device_setpoint_changes 
                    WHERE change_time BETWEEN :start_time AND :end_time
                """),
                    {"start_time": start_time, "end_time": end_time},
                )

                total_row = total_result.fetchone()

                return {
                    "monitoring_period": f"{start_time} to {end_time}",
                    "total_changes": total_row[0] if total_row else 0,
                    "affected_rooms": total_row[1] if total_row else 0,
                    "total_affected_devices": total_row[2] if total_row else 0,
                    "room_summaries": room_summaries,
                }

        except Exception as e:
            logger.error(f"[{self.task_name}] 获取监控摘要失败: {e}")
            return {"error": str(e), "query_time": datetime.now().isoformat()}


# 创建全局实例
setpoint_monitoring_task = SetpointMonitoringTask()


def safe_hourly_setpoint_monitoring() -> None:
    """
    每小时设定点变更监控任务（兼容原接口）
    """
    result = setpoint_monitoring_task.run()

    if not result.get("success", False):
        logger.error(
            f"[SETPOINT_MONITOR] 设定点监控任务失败: {result.get('error', '未知错误')}"
        )
    else:
        logger.info("[SETPOINT_MONITOR] 设定点监控任务成功完成")


def get_monitoring_summary(hours: int = 24) -> Dict[str, Any]:
    """
    获取监控摘要（兼容原接口）

    Args:
        hours: 查询小时数

    Returns:
        Dict[str, Any]: 监控摘要
    """
    return setpoint_monitoring_task.get_monitoring_summary(hours)
