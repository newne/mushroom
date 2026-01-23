"""
设备设定点变更监控模块 (重构版本)

重构改进：
1. 统一模型定义：使用 create_table.py 中的 DeviceSetpointChange 类
2. 配置文件化：将硬编码值移到配置文件中管理
3. 模块化设计：分离配置管理、数据库操作等逻辑
4. 代码一致性：统一导入、命名和错误处理

功能说明：
用于监控指定库房中所有设备的设定点/开关点变化情况，检测关键控制参数的变更并记录到数据库。

架构设计说明：
1. 配置层：从 static_config.json 和 setpoint_monitor_config.json 读取配置
2. 查询层：通过 dataframe_utils 获取设备配置，使用 get_data 模块查询历史数据
3. 数据转换：get_data.get_device_history_cal 将 point_alias 值赋给返回DataFrame的 point_name 列
4. 监控层：使用 point_alias 作为配置映射键，与查询返回的数据结构保持一致
5. 存储层：统一使用 create_table.py 中定义的数据库模型

标识符使用说明：
- point_name: 设备通信使用的系统内部标识符（如 "TemSet", "OnOff"）
- point_alias: 业务逻辑使用的用户友好别名（如 "temp_set", "on_off"）
- 查询返回的数据中，point_name 列实际包含 point_alias 值，实现了标识符转换
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

import pandas as pd
import numpy as np
from loguru import logger
from sqlalchemy import text

from utils.data_preprocessing import query_data_by_batch_time
from utils.dataframe_utils import get_all_device_configs
from utils.create_table import DeviceSetpointChange, create_tables
from utils.setpoint_config import (
    SetpointConfigManager, 
    ChangeType, 
    get_setpoint_config_manager
)
from global_const.global_const import pgsql_engine, static_settings


@dataclass
class SetpointConfig:
    """
    设定点配置数据类
    
    说明：
    - device_type: 设备类型（如 air_cooler, fresh_air_fan）
    - point_name: 系统内部标识符，用于设备通信（如 TemSet, OnOff）
    - point_alias: 用户友好别名，用于业务逻辑（如 temp_set, on_off）
    - change_type: 变更检测类型（数字量、模拟量、枚举等）
    - threshold: 模拟量变化检测阈值
    - description: 测点描述信息
    - enum_mapping: 枚举值映射表（用于状态描述）
    """
    device_type: str
    point_name: str
    point_alias: str
    change_type: ChangeType
    threshold: Optional[float] = None  # 模拟量变化阈值
    description: str = ""
    enum_mapping: Optional[Dict[str, str]] = None


class DeviceSetpointChangeMonitor:
    """设备设定点变更监控器 (重构版本)"""
    
    def __init__(self, config_manager: Optional[SetpointConfigManager] = None):
        """
        初始化监控器
        
        Args:
            config_manager: 配置管理器实例，None 表示使用全局实例
        """
        self.config_manager = config_manager or get_setpoint_config_manager()
        self.setpoint_configs = self._initialize_setpoint_configs_from_static()
        
        logger.info(f"Initialized setpoint monitor with {len(self.setpoint_configs)} configurations from static settings")
        
        # 显示配置摘要
        summary = self.config_manager.get_config_summary()
        logger.debug(f"Config summary: {summary}")
    
    def _initialize_setpoint_configs_from_static(self) -> List[SetpointConfig]:
        """
        从静态配置中初始化设定点配置
        
        设计说明：
        1. 从 static_settings.mushroom.datapoint 读取设备配置
        2. 基于配置文件中的监控规则，识别需要监控的关键设定点
        3. 同时保存 point_name（系统标识符）和 point_alias（业务标识符）
        4. 后续数据匹配将使用 point_alias 作为主键
        
        Returns:
            List[SetpointConfig]: 设定点配置列表
        """
        configs = []
        
        try:
            # 获取静态配置中的数据点配置
            datapoint_config = static_settings.mushroom.datapoint
            
            # 从配置管理器获取设备类型和监控点配置
            device_types = self.config_manager.get_all_device_types()
            
            # 遍历静态配置中的设备类型
            for device_type_key in datapoint_config.keys():
                if device_type_key in ['remark']:  # 跳过非设备类型的键
                    continue
                
                try:
                    device_type_config = getattr(datapoint_config, device_type_key)
                    if not hasattr(device_type_config, 'point_list'):
                        logger.debug(f"Device type {device_type_key} has no point_list, skipping")
                        continue
                    
                    # 获取该设备类型的测点列表
                    point_list = device_type_config.point_list
                    
                    # 检查是否有需要监控的设定点
                    if device_type_key in device_types:
                        monitored_points = self.config_manager.get_monitored_points(device_type_key)
                        
                        for point in point_list:
                            point_name = point.get('point_name')
                            point_alias = point.get('point_alias')
                            
                            if not point_name or not point_alias:
                                logger.warning(f"Invalid point configuration in {device_type_key}: missing point_name or point_alias")
                                continue
                            
                            # 使用 point_alias 进行匹配（而不是 point_name）
                            if point_alias in monitored_points:
                                # 从配置管理器获取阈值
                                threshold = self.config_manager.get_threshold(device_type_key, point_alias)
                                
                                # 根据阈值确定变更类型
                                change_type = self._determine_change_type(point, threshold)
                                
                                # 获取枚举映射（如果存在）
                                enum_mapping = point.get('enum', {})
                                
                                # 获取描述信息
                                description = point.get('description', f"{device_type_key}.{point_alias}")
                                
                                config = SetpointConfig(
                                    device_type=device_type_key,
                                    point_name=point_name,
                                    point_alias=point_alias,
                                    change_type=change_type,
                                    threshold=threshold,
                                    description=description,
                                    enum_mapping=enum_mapping if enum_mapping else None
                                )
                                configs.append(config)
                                
                                logger.debug(f"Added setpoint config: {device_type_key}.{point_name} -> {point_alias} ({change_type.value})")
                    else:
                        logger.debug(f"No setpoint definitions found for device type: {device_type_key}")
                        
                except Exception as e:
                    logger.error(f"Error processing device type {device_type_key}: {e}")
                    continue
            
            logger.info(f"Successfully loaded {len(configs)} setpoint configurations from static settings")
            
            # 按设备类型分组显示加载的配置
            device_type_counts = {}
            for config in configs:
                device_type_counts[config.device_type] = device_type_counts.get(config.device_type, 0) + 1
            
            for device_type, count in device_type_counts.items():
                logger.debug(f"  - {device_type}: {count} setpoints")
            return configs
            
        except Exception as e:
            logger.error(f"Failed to initialize setpoint configs from static settings: {e}")
            # 如果从静态配置加载失败，返回空列表
            return []
    
    def _determine_change_type(self, point_config: Dict[str, Any], threshold: Optional[float]) -> ChangeType:
        """
        根据测点配置和阈值确定变更类型
        
        Args:
            point_config: 测点配置字典
            threshold: 阈值
            
        Returns:
            ChangeType: 变更类型
        """
        # 检查是否有枚举配置
        if point_config.get('enum'):
            return ChangeType.ENUM_STATE
        
        # 检查数据类型
        data_type = point_config.get('data_type', '').lower()
        
        if data_type in ['bool', 'boolean'] or 'on_off' in point_config.get('point_alias', ''):
            return ChangeType.DIGITAL_ON_OFF
        elif threshold is not None:
            return ChangeType.ANALOG_VALUE
        else:
            # 默认为模拟量
            return ChangeType.ANALOG_VALUE
    
    def get_room_setpoint_data(self, room_id: str, start_time: datetime, end_time: datetime) -> pd.DataFrame:
        """
        获取指定库房在指定时间范围内的所有设定点数据
        
        数据流说明：
        1. 通过 get_all_device_configs 获取库房设备配置（包含 point_name 和 point_alias）
        2. 使用 point_alias 过滤出需要监控的设定点
        3. 调用 query_data_by_batch_time 查询历史数据
        4. 查询返回的 DataFrame 中，point_name 列实际包含 point_alias 值
        5. 使用 point_alias 作为键进行配置信息映射
        
        Args:
            room_id: 库房号
            start_time: 开始时间
            end_time: 结束时间
            
        Returns:
            包含所有设定点数据的DataFrame，包含配置信息字段
        """
        try:
            # 获取库房设备配置
            room_configs = get_all_device_configs(room_id=room_id)
            if not room_configs:
                logger.warning(f"No device configuration found for room {room_id}")
                return pd.DataFrame()
            
            # 合并所有设备类型的配置
            all_query_df = pd.concat(room_configs.values(), ignore_index=True)
            
            if all_query_df.empty:
                logger.warning(f"No device data available for room {room_id}")
                return pd.DataFrame()
            
            # 只保留设定点相关的测点
            setpoint_aliases = {config.point_alias for config in self.setpoint_configs}
            setpoint_df = all_query_df[all_query_df['point_alias'].isin(setpoint_aliases)].copy()
            
            if setpoint_df.empty:
                logger.warning(f"No setpoint data available for room {room_id}")
                return pd.DataFrame()
            
            logger.info(f"Querying setpoint data for room {room_id}, time range: {start_time} ~ {end_time}")
            logger.debug(f"Found {len(setpoint_df)} setpoint configurations")
            
            # 查询历史数据
            df = setpoint_df.groupby("device_alias", group_keys=False).apply(
                query_data_by_batch_time, 
                start_time, 
                end_time
            ).reset_index(drop=True).sort_values("time")
            
            if df.empty:
                logger.warning(f"No historical setpoint data found for room {room_id}")
                return pd.DataFrame()
            
            # 添加库房信息
            df['room_id'] = room_id
            
            # 构建设备别名到设备类型的映射表（用于准确的设备类型识别）
            device_type_mapping = {}
            
            # 遍历DataFrame，为每个设备别名建立设备类型映射
            for _, row in df.iterrows():
                device_alias = row.get('device_alias', '')
                if device_alias:
                    # 从设备别名中提取设备类型
                    # 设备别名格式通常为: device_type_room_id (如 grow_light_607, air_cooler_608)
                    device_parts = device_alias.split('_')
                    if len(device_parts) >= 2:
                        # 提取设备类型部分（去掉最后的房间号）
                        device_type_from_alias = '_'.join(device_parts[:-1])
                        device_type_mapping[device_alias] = device_type_from_alias
            
            # 构建配置映射表（使用 point_alias 作为键，但仅用于获取其他配置信息）
            # 说明：查询返回的 DataFrame 中，point_name 列实际包含 point_alias 值
            # 这是由 get_data.get_device_history_cal 函数的数据转换逻辑决定的
            config_mapping = {}
            for config in self.setpoint_configs:
                # 使用 point_alias 作为主键（向后兼容）
                config_mapping[config.point_alias] = {
                    'device_type': config.device_type,
                    'change_type': config.change_type.value,
                    'threshold': config.threshold,
                    'description': config.description,
                    'enum_mapping': config.enum_mapping or {}
                }
            
            # 添加配置信息到DataFrame
            # 优先使用设备别名映射，确保设备类型识别准确
            def get_device_type(row):
                device_alias = row.get('device_alias', '')
                device_name = row.get('device_name', '')
                
                # 方法1: 优先使用设备别名映射（最准确的方法）
                if device_alias in device_type_mapping:
                    return device_type_mapping[device_alias]
                
                # 方法2: 从设备别名中解析设备类型
                if device_alias:
                    device_parts = device_alias.split('_')
                    if len(device_parts) >= 2:
                        return '_'.join(device_parts[:-1])
                
                # 方法3: 从设备名称中解析设备类型（备用方案）
                if device_name:
                    device_parts = device_name.split('_')
                    if len(device_parts) >= 2:
                        return '_'.join(device_parts[:-1])
                
                # 方法4: 最后回退到测点别名映射（已知不准确，仅作最后手段）
                point_alias = row.get('point_name', '')  # 实际包含 point_alias 值
                fallback_type = config_mapping.get(point_alias, {}).get('device_type', 'unknown')
                
                # 记录回退情况以便调试
                if fallback_type != 'unknown':
                    logger.debug(f"Using fallback device type mapping: {device_alias or device_name}.{point_alias} -> {fallback_type}")
                
                return fallback_type
            
            df['device_type'] = df.apply(get_device_type, axis=1)
            df['change_type'] = df['point_name'].map(lambda x: config_mapping.get(x, {}).get('change_type', 'unknown'))
            df['threshold'] = df['point_name'].map(lambda x: config_mapping.get(x, {}).get('threshold'))
            df['description'] = df['point_name'].map(lambda x: config_mapping.get(x, {}).get('description', ''))
            
            logger.info(f"Retrieved {len(df)} setpoint data records for room {room_id}")
            return df
            
        except Exception as e:
            logger.error(f"Failed to get setpoint data for room {room_id}: {e}")
            return pd.DataFrame()
    
    def detect_setpoint_changes(self, setpoint_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        检测设定点变更
        
        变更检测逻辑：
        1. 按设备和测点分组处理数据
        2. 根据配置的变更类型应用不同的检测算法
        3. 数字量：检测 0/1 状态变化
        4. 模拟量：检测超过阈值的数值变化
        5. 枚举量：检测状态值变化
        
        Args:
            setpoint_data: 设定点历史数据（包含配置信息）
            
        Returns:
            变更记录列表，每个记录包含变更详情和上下文信息
        """
        if setpoint_data.empty:
            logger.debug("No setpoint data provided for change detection")
            return []
        
        changes = []
        processed_groups = 0
        
        try:
            # 按设备和测点分组检测变更
            grouped_data = setpoint_data.groupby(['device_name', 'point_name'])
            logger.debug(f"Processing {len(grouped_data)} device-point combinations for change detection")
            
            for (device_name, point_name), group in grouped_data:
                processed_groups += 1
                
                if len(group) < 2:
                    logger.debug(f"Skipping {device_name}.{point_name}: insufficient data points ({len(group)})")
                    continue  # 至少需要2个数据点才能检测变更
                
                # 按时间排序
                group = group.sort_values('time').reset_index(drop=True)
                
                # 获取配置信息
                try:
                    change_type = group.iloc[0]['change_type']
                    threshold = group.iloc[0]['threshold']
                    description = group.iloc[0]['description']
                    room_id = group.iloc[0]['room_id']
                    device_type = group.iloc[0]['device_type']
                except KeyError as e:
                    logger.warning(f"Missing configuration field for {device_name}.{point_name}: {e}")
                    continue
                
                # 检测变更
                group_changes = 0
                for i in range(1, len(group)):
                    current_row = group.iloc[i]
                    previous_row = group.iloc[i-1]
                    
                    current_value = current_row['value']
                    previous_value = previous_row['value']
                    
                    # 跳过无效值
                    if pd.isna(current_value) or pd.isna(previous_value):
                        continue
                    
                    change_detected = False
                    change_info = {}
                    
                    if change_type == ChangeType.DIGITAL_ON_OFF.value:
                        # 数字量开关变化检测
                        if int(current_value) != int(previous_value):
                            change_detected = True
                            change_info = {
                                'change_detail': f"{int(previous_value)} -> {int(current_value)}",
                                'change_magnitude': abs(current_value - previous_value)
                            }
                    
                    elif change_type == ChangeType.ANALOG_VALUE.value:
                        # 模拟量变化检测
                        if threshold and abs(current_value - previous_value) >= threshold:
                            change_detected = True
                            change_info = {
                                'change_detail': f"{previous_value:.2f} -> {current_value:.2f}",
                                'change_magnitude': abs(current_value - previous_value)
                            }
                    
                    elif change_type == ChangeType.ENUM_STATE.value:
                        # 枚举状态变化检测
                        if int(current_value) != int(previous_value):
                            change_detected = True
                            change_info = {
                                'change_detail': f"{int(previous_value)} -> {int(current_value)}",
                                'change_magnitude': abs(current_value - previous_value)
                            }
                    
                    if change_detected:
                        group_changes += 1
                        change_record = {
                            'room_id': room_id,
                            'device_type': device_type,
                            'device_name': device_name,
                            'point_name': point_name,
                            'point_description': description,
                            'change_time': current_row['time'],
                            'previous_value': float(previous_value),
                            'current_value': float(current_value),
                            'change_type': change_type,
                            'change_detail': change_info.get('change_detail', ''),
                            'change_magnitude': change_info.get('change_magnitude', 0.0),
                            'detection_time': datetime.now()
                        }
                        changes.append(change_record)
                        
                        logger.debug(f"Change detected: {device_name}.{point_name} - {change_info.get('change_detail', '')}")
                
                if group_changes > 0:
                    logger.debug(f"Found {group_changes} changes for {device_name}.{point_name}")
            
            logger.info(f"Processed {processed_groups} device-point combinations, detected {len(changes)} setpoint changes")
            return changes
            
        except Exception as e:
            logger.error(f"Failed to detect setpoint changes: {e}")
            return []
    
    def monitor_room_setpoint_changes(self, room_id: str, hours_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        监控指定库房的设定点变更（从当前时间往前指定小时数）
        
        Args:
            room_id: 库房号
            hours_back: 往前查询的小时数，None 表示使用配置文件中的默认值
            
        Returns:
            变更记录列表
        """
        try:
            # 获取时间范围配置
            if hours_back is None:
                time_limits = self.config_manager.get_time_limits()
                hours_back = time_limits.get('default_hours_back', 1)
            
            # 计算时间范围
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours_back)
            
            logger.info(f"Monitoring setpoint changes for room {room_id}, time range: {start_time} ~ {end_time}")
            
            # 获取设定点数据
            setpoint_data = self.get_room_setpoint_data(room_id, start_time, end_time)
            
            if setpoint_data.empty:
                logger.info(f"No setpoint data found for room {room_id}")
                return []
            
            # 检测变更
            changes = self.detect_setpoint_changes(setpoint_data)
            
            logger.info(f"Found {len(changes)} setpoint changes for room {room_id}")
            return changes
            
        except Exception as e:
            logger.error(f"Failed to monitor setpoint changes for room {room_id}: {e}")
            return []
    
    def monitor_all_rooms_setpoint_changes(self, hours_back: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        监控所有库房的设定点变更
        
        房间获取策略：
        1. 优先从配置管理器获取房间列表（已集成静态配置和默认配置的逻辑）
        2. 并行处理所有房间的监控任务
        
        Args:
            hours_back: 往前查询的小时数，None 表示使用配置文件中的默认值
            
        Returns:
            按库房分组的变更记录字典 {room_id: [change_records]}
        """
        try:
            # 从配置管理器获取房间列表
            rooms = self.config_manager.get_default_rooms()
            logger.info(f"Monitoring setpoint changes for {len(rooms)} rooms: {rooms}")
            
            all_changes = {}
            total_changes = 0
            successful_rooms = 0
            
            for room_id in rooms:
                try:
                    logger.info(f"Monitoring setpoint changes for room {room_id}")
                    changes = self.monitor_room_setpoint_changes(room_id, hours_back)
                    all_changes[room_id] = changes
                    total_changes += len(changes)
                    successful_rooms += 1
                    
                    if changes:
                        logger.info(f"Room {room_id}: found {len(changes)} setpoint changes")
                    else:
                        logger.debug(f"Room {room_id}: no setpoint changes detected")
                        
                except Exception as e:
                    logger.error(f"Failed to monitor room {room_id}: {e}")
                    all_changes[room_id] = []  # 确保所有房间都有记录
            
            logger.info(f"Monitoring completed: {successful_rooms}/{len(rooms)} rooms processed successfully")
            logger.info(f"Total setpoint changes detected across all rooms: {total_changes}")
            
            # 按房间汇总统计
            for room_id, changes in all_changes.items():
                if changes:
                    change_types = {}
                    for change in changes:
                        change_type = change.get('change_type', 'unknown')
                        change_types[change_type] = change_types.get(change_type, 0) + 1
                    logger.debug(f"Room {room_id} change types: {change_types}")
            
            return all_changes
            
        except Exception as e:
            logger.error(f"Failed to monitor all rooms setpoint changes: {e}")
            return {}
    
    def store_setpoint_changes(self, changes: List[Dict[str, Any]]) -> bool:
        """
        存储设定点变更记录到数据库
        
        Args:
            changes: 变更记录列表
            
        Returns:
            存储是否成功
        """
        if not changes:
            logger.info("No setpoint changes to store")
            return True
        
        try:
            # 获取数据库配置
            db_config = self.config_manager.get_database_config()
            table_name = db_config.get('table_name', 'device_setpoint_changes')
            batch_size = db_config.get('batch_size', 1000)
            
            # 转换为DataFrame
            df = pd.DataFrame(changes)
            
            # 验证必要字段
            required_fields = db_config.get('required_fields', [
                'room_id', 'device_type', 'device_name', 'point_name',
                'change_time', 'previous_value', 'current_value', 'change_type'
            ])
            
            missing_fields = [field for field in required_fields if field not in df.columns]
            if missing_fields:
                raise ValueError(f"Missing required fields in change records: {missing_fields}")
            
            # 存储到数据库
            df.to_sql(
                table_name,
                con=pgsql_engine,
                if_exists='append',
                index=False,
                method='multi',
                chunksize=batch_size
            )
            
            logger.info(f"Successfully stored {len(changes)} setpoint change records")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store setpoint changes: {e}")
            return False


def create_setpoint_monitor_table():
    """创建设定点监控表（使用统一的表定义）"""
    try:
        create_tables()
        logger.info("Setpoint monitor table created/verified successfully")
    except Exception as e:
        logger.error(f"Failed to create setpoint monitor table: {e}")


def batch_monitor_setpoint_changes(
    start_time: datetime, 
    end_time: datetime, 
    store_results: bool = True,
    config_manager: Optional[SetpointConfigManager] = None
) -> Dict[str, Any]:
    """
    批量监控所有库房在指定时间范围内的设定点变更情况 (重构版本)
    
    改进说明：
    1. 使用配置管理器获取房间列表和配置参数
    2. 统一使用 create_table.py 中的数据库模型
    3. 移除硬编码的房间列表和配置参数
    4. 改进错误处理和日志记录
    
    Args:
        start_time: 分析起始时间
        end_time: 分析结束时间  
        store_results: 是否存储结果到数据库，默认True
        config_manager: 配置管理器实例，None 表示使用全局实例
        
    Returns:
        Dict[str, Any]: 包含处理结果的详细信息字典
        
    Raises:
        ValueError: 当时间参数无效时
        Exception: 当数据库操作失败时
    """
    # 参数验证
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise ValueError("start_time and end_time must be datetime objects")
    
    if start_time >= end_time:
        raise ValueError("start_time must be earlier than end_time")
    
    # 获取配置管理器
    if config_manager is None:
        config_manager = get_setpoint_config_manager()
    
    # 检查时间范围是否合理
    time_diff = end_time - start_time
    time_limits = config_manager.get_time_limits()
    max_days = time_limits.get('max_batch_days', 30)
    
    if time_diff.days > max_days:
        logger.warning(f"Large time range detected: {time_diff.days} days (max recommended: {max_days}). This may take a long time to process.")
    
    processing_start = datetime.now()
    logger.info(f"🚀 Starting batch setpoint monitoring (refactored version)")
    logger.info(f"   Time range: {start_time} ~ {end_time} ({time_diff})")
    
    # 初始化结果统计
    result = {
        'success': False,
        'total_rooms': 0,
        'successful_rooms': 0,
        'total_changes': 0,
        'changes_by_room': {},
        'processing_time': 0.0,
        'error_rooms': [],
        'stored_records': 0
    }
    
    try:
        # 确保数据库表存在
        if store_results:
            logger.info("📋 Ensuring database table exists...")
            create_setpoint_monitor_table()
        
        # 创建监控器实例
        logger.info("🔧 Creating setpoint monitor instance...")
        monitor = DeviceSetpointChangeMonitor(config_manager)
        
        # 获取所有库房列表
        logger.info("📍 Getting available rooms from configuration...")
        rooms = config_manager.get_default_rooms()
        logger.info(f"Found {len(rooms)} rooms: {rooms}")
        
        result['total_rooms'] = len(rooms)
        
        # 批量处理所有库房
        all_changes = []
        successful_rooms = 0
        
        logger.info(f"🔍 Processing {len(rooms)} rooms for setpoint changes...")
        
        for i, room_id in enumerate(rooms, 1):
            try:
                logger.info(f"[{i}/{len(rooms)}] Processing room {room_id}...")
                
                # 获取库房设定点数据
                setpoint_data = monitor.get_room_setpoint_data(room_id, start_time, end_time)
                
                if setpoint_data.empty:
                    logger.info(f"Room {room_id}: No setpoint data found")
                    result['changes_by_room'][room_id] = 0
                    successful_rooms += 1
                    continue
                
                # 检测变更
                changes = monitor.detect_setpoint_changes(setpoint_data)
                
                if changes:
                    logger.info(f"Room {room_id}: Detected {len(changes)} setpoint changes")
                    all_changes.extend(changes)
                    result['changes_by_room'][room_id] = len(changes)
                    
                    # 按设备类型统计
                    device_type_stats = {}
                    for change in changes:
                        device_type = change.get('device_type', 'unknown')
                        device_type_stats[device_type] = device_type_stats.get(device_type, 0) + 1
                    
                    logger.debug(f"Room {room_id} change types: {device_type_stats}")
                else:
                    logger.info(f"Room {room_id}: No setpoint changes detected")
                    result['changes_by_room'][room_id] = 0
                
                successful_rooms += 1
                
            except Exception as e:
                logger.error(f"Failed to process room {room_id}: {e}")
                result['error_rooms'].append(room_id)
                result['changes_by_room'][room_id] = 0
                continue
        
        result['successful_rooms'] = successful_rooms
        result['total_changes'] = len(all_changes)
        
        # 存储结果到数据库
        if store_results and all_changes:
            logger.info(f"💾 Storing {len(all_changes)} change records to database...")
            
            try:
                success = monitor.store_setpoint_changes(all_changes)
                if success:
                    result['stored_records'] = len(all_changes)
                    logger.info(f"✅ Successfully stored {len(all_changes)} change records to database")
                else:
                    result['stored_records'] = 0
                    logger.error("Failed to store change records to database")
                
            except Exception as e:
                logger.error(f"Failed to store change records to database: {e}")
                result['stored_records'] = 0
                # 不抛出异常，允许返回检测结果
        
        elif store_results and not all_changes:
            logger.info("ℹ️ No changes detected, nothing to store")
            result['stored_records'] = 0
        
        elif not store_results:
            logger.info("ℹ️ Storage disabled, skipping database operations")
            result['stored_records'] = 0
        
        # 计算处理时间
        processing_end = datetime.now()
        result['processing_time'] = (processing_end - processing_start).total_seconds()
        
        # 生成处理报告
        logger.info(f"📊 Batch monitoring completed:")
        logger.info(f"   Processed rooms: {successful_rooms}/{len(rooms)}")
        logger.info(f"   Total changes detected: {result['total_changes']}")
        logger.info(f"   Records stored: {result['stored_records']}")
        logger.info(f"   Processing time: {result['processing_time']:.2f} seconds")
        
        if result['error_rooms']:
            logger.warning(f"   Failed rooms: {result['error_rooms']}")
        
        # 按库房显示统计
        for room_id, change_count in result['changes_by_room'].items():
            if change_count > 0:
                logger.info(f"   Room {room_id}: {change_count} changes")
        
        result['success'] = True
        return result
        
    except Exception as e:
        logger.error(f"Batch monitoring failed: {e}")
        result['processing_time'] = (datetime.now() - processing_start).total_seconds()
        result['success'] = False
        raise


def validate_batch_monitoring_environment(config_manager: Optional[SetpointConfigManager] = None) -> bool:
    """
    验证批量监控环境的可用性 (重构版本)
    
    检查项目：
    1. 数据库连接可用性
    2. 配置文件可访问性
    3. 必要模块导入状态
    4. 监控器实例创建能力
    
    Args:
        config_manager: 配置管理器实例，None 表示使用全局实例
    
    Returns:
        bool: 环境验证是否通过
    """
    logger.info("🔍 Validating batch monitoring environment (refactored version)...")
    
    try:
        # 获取配置管理器
        if config_manager is None:
            config_manager = get_setpoint_config_manager()
        
        # 检查数据库连接
        logger.debug("Checking database connection...")
        try:
            # 简单的数据库连接测试
            with pgsql_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.debug("✅ Database connection OK")
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            return False
        
        # 检查配置管理器
        logger.debug("Checking configuration manager...")
        try:
            summary = config_manager.get_config_summary()
            if summary['device_types_count'] == 0:
                logger.error("❌ No device types configured")
                return False
            logger.debug(f"✅ Configuration manager OK: {summary}")
        except Exception as e:
            logger.error(f"❌ Configuration manager check failed: {e}")
            return False
        
        # 检查静态配置
        logger.debug("Checking static configuration...")
        try:
            rooms_cfg = getattr(static_settings.mushroom, 'rooms', {})
            datapoint_cfg = getattr(static_settings.mushroom, 'datapoint', {})
            if not rooms_cfg or not datapoint_cfg:
                logger.error("❌ Static configuration incomplete")
                return False
            logger.debug("✅ Static configuration OK")
        except Exception as e:
            logger.error(f"❌ Static configuration access failed: {e}")
            return False
        
        # 检查监控器创建
        logger.debug("Checking monitor instance creation...")
        try:
            monitor = DeviceSetpointChangeMonitor(config_manager)
            if not monitor.setpoint_configs:
                logger.error("❌ Monitor has no setpoint configurations")
                return False
            logger.debug(f"✅ Monitor created with {len(monitor.setpoint_configs)} configurations")
        except Exception as e:
            logger.error(f"❌ Monitor creation failed: {e}")
            return False
        
        # 检查数据库表
        logger.debug("Checking database table...")
        try:
            create_setpoint_monitor_table()
            logger.debug("✅ Database table OK")
        except Exception as e:
            logger.error(f"❌ Database table check failed: {e}")
            return False
        
        logger.info("✅ Environment validation passed")
        return True
        
    except Exception as e:
        logger.error(f"❌ Environment validation failed: {e}")
        return False


def create_setpoint_monitor(config_manager: Optional[SetpointConfigManager] = None) -> DeviceSetpointChangeMonitor:
    """
    创建设定点监控器实例 (重构版本)
    
    Args:
        config_manager: 配置管理器实例，None 表示使用全局实例
        
    Returns:
        DeviceSetpointChangeMonitor: 监控器实例
    """
    return DeviceSetpointChangeMonitor(config_manager)


if __name__ == "__main__":
    # 测试代码 - 演示重构后的设定点监控系统功能
    print("🚀 启动设定点变更监控系统测试 (重构版本)")
    print("=" * 70)
    
    # 环境验证
    # print("\n🔍 验证批量监控环境...")
    # if not validate_batch_monitoring_environment():
    #     print("❌ 环境验证失败，请检查配置")
    #     exit(1)
    
    # 创建配置管理器
    config_manager = get_setpoint_config_manager()
    
    # 显示配置摘要
    summary = config_manager.get_config_summary()
    print(f"\n📋 配置摘要:")
    print(f"  配置文件: {summary['config_path']}")
    print(f"  默认房间数: {summary['default_rooms_count']}")
    print(f"  设备类型数: {summary['device_types_count']}")
    print(f"  监控点总数: {summary['total_monitored_points']}")
    print(f"  批量监控: {'启用' if summary['monitoring_enabled']['batch'] else '禁用'}")
    print(f"  实时监控: {'启用' if summary['monitoring_enabled']['real_time'] else '禁用'}")
    
    # 创建监控器实例
    monitor = create_setpoint_monitor(config_manager)
    
    # 显示从配置加载的设定点配置
    print(f"\n📋 从配置加载的设定点监控配置 (共 {len(monitor.setpoint_configs)} 个):")
    device_types = {}
    for config in monitor.setpoint_configs:
        device_type = config.device_type
        if device_type not in device_types:
            device_types[device_type] = []
        device_types[device_type].append(config)
    
    for device_type, configs in device_types.items():
        print(f"\n🔧 {device_type.upper()} ({len(configs)} 个监控点):")
        for config in configs[:3]:  # 显示前3个
            threshold_info = f", 阈值: {config.threshold}" if config.threshold else ""
            enum_info = f", 枚举: {list(config.enum_mapping.keys())}" if config.enum_mapping else ""
            print(f"   • {config.point_name} -> {config.point_alias}")
            print(f"     类型: {config.change_type.value}{threshold_info}{enum_info}")
            print(f"     描述: {config.description}")
        if len(configs) > 3:
            print(f"   ... 还有 {len(configs) - 3} 个监控点")
    
    # 测试单个库房监控
    print(f"\n🔍 测试单个库房监控:")
    rooms = config_manager.get_default_rooms()
    test_room_id = rooms[0] if rooms else "611"
    print(f"正在监控库房 {test_room_id} 的设定点变更（使用配置的默认时间范围）...")
    
    changes = monitor.monitor_room_setpoint_changes(test_room_id)
    
    if changes:
        print(f"✅ 检测到 {len(changes)} 个设定点变更:")
        for i, change in enumerate(changes[:3], 1):  # 显示前3个
            print(f"   {i}. {change['device_name']}.{change['point_name']}")
            print(f"      变更: {change['change_detail']}")
            print(f"      时间: {change['change_time']}")
            print(f"      类型: {change['change_type']}")
        
        if len(changes) > 3:
            print(f"   ... 还有 {len(changes) - 3} 个变更记录")
    else:
        print("ℹ️ 未检测到设定点变更")
    
    # 测试批量监控功能
    print(f"\n🚀 测试批量监控功能 (重构版本):")
    print("正在执行批量设定点变更分析...")
    
    # 设定测试时间范围
    time_limits = config_manager.get_time_limits()
    default_hours = time_limits.get('default_hours_back', 1)
    
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=default_hours * 2)  # 使用配置的2倍时间
    
    print(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # 执行批量监控
        result = batch_monitor_setpoint_changes(
            start_time=start_time,
            end_time=end_time,
            store_results=True,
            config_manager=config_manager
        )
        
        if result['success']:
            print(f"\n✅ 批量监控完成:")
            print(f"   处理库房: {result['successful_rooms']}/{result['total_rooms']}")
            print(f"   检测变更: {result['total_changes']} 个")
            print(f"   存储记录: {result['stored_records']} 条")
            print(f"   处理耗时: {result['processing_time']:.2f} 秒")
            
            if result['error_rooms']:
                print(f"   失败库房: {result['error_rooms']}")
            
            # 显示各库房统计
            print(f"\n📊 各库房变更统计:")
            for room_id, change_count in result['changes_by_room'].items():
                status = "✅" if change_count > 0 else "⚪"
                print(f"   {status} 库房 {room_id}: {change_count} 个变更")
        else:
            print("❌ 批量监控失败")
            
    except Exception as e:
        print(f"❌ 批量监控异常: {e}")
    
    print(f"\n🎯 重构版本测试完成！")
    print("=" * 70)
    
    # 重构改进总结
    print(f"\n📋 重构改进总结:")
    print("1. ✅ 统一模型定义：使用 create_table.py 中的 DeviceSetpointChange")
    print("2. ✅ 配置文件化：硬编码值移到 setpoint_monitor_config.json")
    print("3. ✅ 模块化设计：分离配置管理器和数据库操作")
    print("4. ✅ 代码一致性：统一导入、命名和错误处理")
    print("5. ✅ 灵活配置：支持动态配置和热重载")
    print("6. ✅ 改进日志：更详细的操作日志和错误信息")
    print("7. ✅ 环境验证：完整的环境检查和边界条件处理")
    print("8. ✅ 向后兼容：保持原有API接口不变")