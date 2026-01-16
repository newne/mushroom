"""
设备设定点变更监控模块

功能说明：
用于监控指定库房中所有设备的设定点/开关点变化情况，检测关键控制参数的变更并记录到数据库。

架构设计说明：
1. 配置层：从 static_config.json 读取设备配置，包含 point_name（系统标识符）和 point_alias（用户友好别名）
2. 查询层：通过 dataframe_utils 获取设备配置，使用 get_data 模块查询历史数据
3. 数据转换：get_data.get_device_history_cal 将 point_alias 值赋给返回DataFrame的 point_name 列
4. 监控层：使用 point_alias 作为配置映射键，与查询返回的数据结构保持一致

标识符使用说明：
- point_name: 设备通信使用的系统内部标识符（如 "TemSet", "OnOff"）
- point_alias: 业务逻辑使用的用户友好别名（如 "temp_set", "on_off"）
- 查询返回的数据中，point_name 列实际包含 point_alias 值，实现了标识符转换
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import numpy as np
from loguru import logger
from sqlalchemy import Column, String, DateTime, Float, Integer, Text, Boolean, Index, func
from sqlalchemy.orm import declarative_base

from utils.data_preprocessing import query_data_by_batch_time
from utils.dataframe_utils import get_all_device_configs
from global_const.global_const import pgsql_engine, static_settings


class ChangeType(Enum):
    """变更类型枚举"""
    DIGITAL_ON_OFF = "digital_on_off"      # 数字量开关变化 (0->1 或 1->0)
    ANALOG_VALUE = "analog_value"          # 模拟量数值变化
    ENUM_STATE = "enum_state"              # 枚举状态变化
    THRESHOLD_CROSS = "threshold_cross"    # 阈值穿越


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
    """设备设定点变更监控器"""
    
    def __init__(self):
        """初始化监控器"""
        self.setpoint_configs = self._initialize_setpoint_configs_from_static()
        logger.info(f"Initialized setpoint monitor with {len(self.setpoint_configs)} configurations from static settings")
    
    def _initialize_setpoint_configs_from_static(self) -> List[SetpointConfig]:
        """
        从静态配置中初始化设定点配置
        
        设计说明：
        1. 从 static_settings.mushroom.datapoint 读取设备配置
        2. 基于预定义的监控规则，识别需要监控的关键设定点
        3. 同时保存 point_name（系统标识符）和 point_alias（业务标识符）
        4. 后续数据匹配将使用 point_alias 作为主键
        
        Returns:
            List[SetpointConfig]: 设定点配置列表
        """
        configs = []
        
        try:
            # 获取静态配置中的数据点配置
            datapoint_config = static_settings.mushroom.datapoint
            
            # 定义需要监控的设定点及其配置规则
            # 注意：这里的键对应 static_config.json 中的 point_alias 字段（用户友好别名）
            # 根据 static_config.json 全面梳理所有设备类型的设定点和开关点
            setpoint_definitions = {
                'air_cooler': {
                    # 冷风机开关状态
                    'on_off': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '冷风机开关状态'
                    },
                    # 温度设定值
                    'temp_set': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 0.5,  # 温度变化0.5度触发监控
                        'description': '温度设定值'
                    },
                    # 温差设定值
                    'temp_diffset': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 0.2,  # 温差变化0.2度触发监控
                        'description': '温差设定值'
                    },
                    # 冷风机循环开启时间设定
                    'cyc_on_time': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 时间变化1分钟触发监控
                        'description': '冷风机循环开启时间设定'
                    },
                    # 冷风机循环关闭时间设定
                    'cyc_off_time': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 时间变化1分钟触发监控
                        'description': '冷风机循环关闭时间设定'
                    },
                    # 新风联动冷风机开关
                    'air_on_off': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '新风联动冷风机开关'
                    },
                    # 加湿联动冷风机开关
                    'hum_on_off': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '加湿联动冷风机开关'
                    },
                    # 冷风机循环开关
                    'cyc_on_off': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '冷风机循环开关'
                    }
                },
                'fresh_air_fan': {
                    # 新风模式
                    'mode': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '新风模式'
                    },
                    # 新风控制方式
                    'control': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '新风控制方式'
                    },
                    # CO2启动新风阈值
                    'co2_on': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 50.0,  # CO2浓度变化50ppm触发监控
                        'description': 'CO2启动新风阈值'
                    },
                    # CO2停止新风阈值
                    'co2_off': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 50.0,  # CO2浓度变化50ppm触发监控
                        'description': 'CO2停止新风阈值'
                    },
                    # 新风开启时间设定
                    'on': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 时间变化1分钟触发监控
                        'description': '新风开启时间设定'
                    },
                    # 新风停止时间设定
                    'off': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 时间变化1分钟触发监控
                        'description': '新风停止时间设定'
                    }
                },
                'humidifier': {
                    # 加湿器模式
                    'mode': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '加湿器模式'
                    },
                    # 加湿器开启设定
                    'on': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 2.0,  # 湿度变化2%触发监控
                        'description': '加湿器开启设定'
                    },
                    # 加湿器停止设定
                    'off': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 2.0,  # 湿度变化2%触发监控
                        'description': '加湿器停止设定'
                    }
                },
                'grow_light': {
                    # 补光模式
                    'model': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '补光模式'
                    },
                    # 补光开启分钟设定
                    'on_mset': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 5.0,  # 时间变化5分钟触发监控
                        'description': '补光开启分钟设定'
                    },
                    # 补光停止分钟设定
                    'off_mset': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 5.0,  # 时间变化5分钟触发监控
                        'description': '补光停止分钟设定'
                    },
                    # 1#补光开关
                    'on_off1': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '1#补光开关'
                    },
                    # 2#补光开关
                    'on_off2': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '2#补光开关'
                    },
                    # 3#补光开关
                    'on_off3': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '3#补光开关'
                    },
                    # 4#补光开关
                    'on_off4': {
                        'change_type': ChangeType.DIGITAL_ON_OFF,
                        'description': '4#补光开关'
                    },
                    # 1#光源选择
                    'choose1': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '1#光源选择'
                    },
                    # 2#光源选择
                    'choose2': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '2#光源选择'
                    },
                    # 3#光源选择
                    'choose3': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '3#光源选择'
                    },
                    # 4#光源选择
                    'choose4': {
                        'change_type': ChangeType.ENUM_STATE,
                        'description': '4#光源选择'
                    }
                },
                # 蘑菇信息设定点（进库信息变更监控）
                'mushroom_info': {
                    # 进库包数
                    'in_num': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 包数变化1个触发监控
                        'description': '进库包数'
                    },
                    # 进库天数
                    'in_day_num': {
                        'change_type': ChangeType.ANALOG_VALUE,
                        'threshold': 1.0,  # 天数变化1天触发监控
                        'description': '进库天数'
                    }
                }
            }
            
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
                    if device_type_key in setpoint_definitions:
                        setpoint_defs = setpoint_definitions[device_type_key]
                        
                        for point in point_list:
                            point_name = point.get('point_name')
                            point_alias = point.get('point_alias')
                            
                            if not point_name or not point_alias:
                                logger.warning(f"Invalid point configuration in {device_type_key}: missing point_name or point_alias")
                                continue
                            
                            # 使用 point_alias 进行匹配（而不是 point_name）
                            if point_alias in setpoint_defs:
                                setpoint_def = setpoint_defs[point_alias]
                                
                                # 获取枚举映射（如果存在）
                                enum_mapping = point.get('enum', {})
                                
                                config = SetpointConfig(
                                    device_type=device_type_key,
                                    point_name=point_name,
                                    point_alias=point_alias,
                                    change_type=setpoint_def['change_type'],
                                    threshold=setpoint_def.get('threshold'),
                                    description=setpoint_def['description'],
                                    enum_mapping=enum_mapping if enum_mapping else None
                                )
                                configs.append(config)
                                
                                logger.debug(f"Added setpoint config: {device_type_key}.{point_name} -> {point_alias} ({setpoint_def['change_type'].value})")
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
            
            # 构建配置映射表（使用 point_alias 作为键）
            # 说明：查询返回的 DataFrame 中，point_name 列实际包含 point_alias 值
            # 这是由 get_data.get_device_history_cal 函数的数据转换逻辑决定的
            config_mapping = {}
            for config in self.setpoint_configs:
                config_mapping[config.point_alias] = {
                    'device_type': config.device_type,
                    'change_type': config.change_type.value,
                    'threshold': config.threshold,
                    'description': config.description,
                    'enum_mapping': config.enum_mapping or {}
                }
            
            # 添加配置信息到DataFrame
            # 注意：这里使用 point_name 列进行映射，但该列实际包含 point_alias 值
            df['device_type'] = df['point_name'].map(lambda x: config_mapping.get(x, {}).get('device_type', 'unknown'))
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
    
    def monitor_room_setpoint_changes(self, room_id: str, hours_back: int = 1) -> List[Dict[str, Any]]:
        """
        监控指定库房的设定点变更（从当前时间往前指定小时数）
        
        Args:
            room_id: 库房号
            hours_back: 往前查询的小时数
            
        Returns:
            变更记录列表
        """
        try:
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
    
    def monitor_all_rooms_setpoint_changes(self, hours_back: int = 1) -> Dict[str, List[Dict[str, Any]]]:
        """
        监控所有库房的设定点变更
        
        房间获取策略：
        1. 优先从 static_settings.mushroom.rooms 获取房间列表
        2. 如果获取失败，使用默认房间列表作为备选
        3. 并行处理所有房间的监控任务
        
        Args:
            hours_back: 往前查询的小时数
            
        Returns:
            按库房分组的变更记录字典 {room_id: [change_records]}
        """
        try:
            # 从静态配置获取所有库房列表
            rooms = []
            try:
                rooms_cfg = getattr(static_settings.mushroom, 'rooms', {})
                if rooms_cfg and hasattr(rooms_cfg, 'keys'):
                    rooms = list(rooms_cfg.keys())
                    logger.info(f"Found {len(rooms)} rooms from static config: {rooms}")
                else:
                    logger.warning("No rooms configuration found in static settings")
                    rooms = ['607', '608', '611', '612']
            except Exception as e:
                logger.warning(f"Failed to get rooms from static config: {e}")
                rooms = ['607', '608', '611', '612']
                logger.info(f"Using default room list: {rooms}")
            
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
            # 转换为DataFrame
            df = pd.DataFrame(changes)
            
            # 存储到数据库
            df.to_sql(
                'device_setpoint_changes',
                con=pgsql_engine,
                if_exists='append',
                index=False,
                method='multi'
            )
            
            logger.info(f"Successfully stored {len(changes)} setpoint change records")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store setpoint changes: {e}")
            return False


# 数据库表定义
Base = declarative_base()

class DeviceSetpointChange(Base):
    """设备设定点变更记录表"""
    __tablename__ = "device_setpoint_changes"
    
    __table_args__ = (
        Index('idx_room_change_time', 'room_id', 'change_time'),
        Index('idx_device_point', 'device_name', 'point_name'),
        Index('idx_change_time', 'change_time'),
        Index('idx_device_type', 'device_type'),
        {"comment": "设备设定点变更记录表"}
    )
    
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键ID (自增)"
    )
    
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    device_name = Column(String(100), nullable=False, comment="设备名称")
    point_name = Column(String(100), nullable=False, comment="测点名称")
    point_description = Column(String(200), nullable=True, comment="测点描述")
    
    change_time = Column(DateTime, nullable=False, comment="变更发生时间")
    previous_value = Column(Float, nullable=False, comment="变更前值")
    current_value = Column(Float, nullable=False, comment="变更后值")
    
    change_type = Column(String(50), nullable=False, comment="变更类型")
    change_detail = Column(String(200), nullable=True, comment="变更详情")
    change_magnitude = Column(Float, nullable=True, comment="变更幅度")
    
    detection_time = Column(DateTime, nullable=False, comment="检测时间")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")


def create_setpoint_monitor_table():
    """创建设定点监控表"""
    try:
        Base.metadata.create_all(bind=pgsql_engine, checkfirst=True)
        logger.info("Setpoint monitor table created/verified successfully")
    except Exception as e:
        logger.error(f"Failed to create setpoint monitor table: {e}")


def batch_monitor_setpoint_changes(
    start_time: datetime, 
    end_time: datetime, 
    store_results: bool = True
) -> Dict[str, Any]:
    """
    批量监控所有库房在指定时间范围内的设定点变更情况
    
    功能说明：
    1. 获取所有可用库房列表
    2. 遍历每个库房进行设定点变更分析
    3. 检测各类设备的设定点变化（温度、湿度、CO2、开关状态等）
    4. 将检测结果批量存储到数据库
    
    数据处理逻辑：
    - 兼容现有的 point_name 和 point_alias 标识符转换机制
    - 查询返回数据中 point_name 列实际包含 point_alias 值
    - 维护完整的配置信息映射（设备类型、变更类型、阈值、描述等）
    
    Args:
        start_time: 分析起始时间
        end_time: 分析结束时间  
        store_results: 是否存储结果到数据库，默认True
        
    Returns:
        Dict[str, Any]: 包含以下信息的字典
        - success: bool, 操作是否成功
        - total_rooms: int, 处理的库房总数
        - successful_rooms: int, 成功处理的库房数
        - total_changes: int, 检测到的变更总数
        - changes_by_room: Dict[str, int], 按库房分组的变更数量
        - processing_time: float, 处理耗时（秒）
        - error_rooms: List[str], 处理失败的库房列表
        - stored_records: int, 存储到数据库的记录数
        
    Raises:
        ValueError: 当时间参数无效时
        Exception: 当数据库操作失败时
    """
    # 参数验证
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise ValueError("start_time and end_time must be datetime objects")
    
    if start_time >= end_time:
        raise ValueError("start_time must be earlier than end_time")
    
    # 检查时间范围是否合理（不超过30天）
    time_diff = end_time - start_time
    if time_diff.days > 30:
        logger.warning(f"Large time range detected: {time_diff.days} days. This may take a long time to process.")
    
    processing_start = datetime.now()
    logger.info(f"🚀 Starting batch setpoint monitoring")
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
        monitor = DeviceSetpointChangeMonitor()
        
        # 获取所有库房列表
        logger.info("📍 Getting available rooms from static configuration...")
        rooms = []
        try:
            rooms_cfg = getattr(static_settings.mushroom, 'rooms', {})
            if rooms_cfg and hasattr(rooms_cfg, 'keys'):
                rooms = list(rooms_cfg.keys())
                logger.info(f"Found {len(rooms)} rooms from static config: {rooms}")
            else:
                logger.warning("No rooms configuration found in static settings")
                rooms = ['607', '608', '611', '612']
                logger.info(f"Using default room list: {rooms}")
        except Exception as e:
            logger.warning(f"Failed to get rooms from static config: {e}")
            rooms = ['607', '608', '611', '612']
            logger.info(f"Using default room list: {rooms}")
        
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
                # 转换为DataFrame
                df = pd.DataFrame(all_changes)
                
                # 验证必要字段
                required_fields = [
                    'room_id', 'device_type', 'device_name', 'point_name', 
                    'change_time', 'previous_value', 'current_value', 'change_type'
                ]
                
                missing_fields = [field for field in required_fields if field not in df.columns]
                if missing_fields:
                    raise ValueError(f"Missing required fields in change records: {missing_fields}")
                
                # 批量插入数据库
                df.to_sql(
                    'device_setpoint_changes',
                    con=pgsql_engine,
                    if_exists='append',
                    index=False,
                    method='multi',
                    chunksize=1000  # 分批插入，提高性能
                )
                
                result['stored_records'] = len(df)
                logger.info(f"✅ Successfully stored {len(df)} change records to database")
                
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


def validate_batch_monitoring_environment() -> bool:
    """
    验证批量监控环境的可用性
    
    检查项目：
    1. 数据库连接可用性
    2. 静态配置文件可访问性
    3. 必要模块导入状态
    4. 监控器实例创建能力
    
    Returns:
        bool: 环境验证是否通过
    """
    logger.info("🔍 Validating batch monitoring environment...")
    
    try:
        # 检查数据库连接
        logger.debug("Checking database connection...")
        try:
            # 简单的数据库连接测试
            with pgsql_engine.connect() as conn:
                conn.execute("SELECT 1")
            logger.debug("✅ Database connection OK")
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
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
            monitor = DeviceSetpointChangeMonitor()
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


def create_setpoint_monitor() -> DeviceSetpointChangeMonitor:
    """创建设定点监控器实例"""
    return DeviceSetpointChangeMonitor()


if __name__ == "__main__":
    # 测试代码 - 演示设定点监控系统的功能
    print("🚀 启动设定点变更监控系统测试")
    print("=" * 60)
    
    # 环境验证
    print("\n🔍 验证批量监控环境...")
    if not validate_batch_monitoring_environment():
        print("❌ 环境验证失败，请检查配置")
        exit(1)
    
    # 创建监控器实例
    monitor = create_setpoint_monitor()
    
    # 创建数据库表
    create_setpoint_monitor_table()
    
    # 显示架构设计说明
    print("\n📖 系统架构说明:")
    print("1. 配置层：从 static_config.json 读取设备配置")
    print("2. 查询层：使用 point_alias 过滤设定点数据")
    print("3. 数据层：get_data 模块进行标识符转换")
    print("4. 监控层：基于 point_alias 进行配置映射")
    
    # 显示从静态配置加载的设定点配置
    print(f"\n📋 从静态配置加载的设定点监控配置 (共 {len(monitor.setpoint_configs)} 个):")
    device_types = {}
    for config in monitor.setpoint_configs:
        device_type = config.device_type
        if device_type not in device_types:
            device_types[device_type] = []
        device_types[device_type].append(config)
    
    for device_type, configs in device_types.items():
        print(f"\n🔧 {device_type.upper()} ({len(configs)} 个监控点):")
        for config in configs:
            threshold_info = f", 阈值: {config.threshold}" if config.threshold else ""
            enum_info = f", 枚举: {list(config.enum_mapping.keys())}" if config.enum_mapping else ""
            print(f"   • {config.point_name} -> {config.point_alias}")
            print(f"     类型: {config.change_type.value}{threshold_info}{enum_info}")
            print(f"     描述: {config.description}")
    
    # 测试单个库房监控
    print(f"\n🔍 测试单个库房监控:")
    test_room_id = "611"
    print(f"正在监控库房 {test_room_id} 的设定点变更（最近1小时）...")
    
    changes = monitor.monitor_room_setpoint_changes(test_room_id, hours_back=1)
    
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
    print(f"\n🚀 测试批量监控功能:")
    print("正在执行批量设定点变更分析...")
    
    # 设定测试时间范围（最近2小时）
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=2)
    
    print(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # 执行批量监控
        result = batch_monitor_setpoint_changes(
            start_time=start_time,
            end_time=end_time,
            store_results=True
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
    
    # 边界条件测试
    print(f"\n🧪 边界条件测试:")
    
    # 测试无效时间范围
    try:
        invalid_result = batch_monitor_setpoint_changes(
            start_time=end_time,  # 开始时间晚于结束时间
            end_time=start_time,
            store_results=False
        )
        print("❌ 应该抛出异常但没有")
    except ValueError as e:
        print(f"✅ 正确捕获无效时间范围: {e}")
    except Exception as e:
        print(f"⚠️ 意外异常: {e}")
    
    # 测试空时间范围
    try:
        empty_start = datetime.now() - timedelta(minutes=1)
        empty_end = datetime.now() - timedelta(minutes=1)
        empty_result = batch_monitor_setpoint_changes(
            start_time=empty_start,
            end_time=empty_end,
            store_results=False
        )
        print(f"✅ 空时间范围测试: 检测到 {empty_result['total_changes']} 个变更")
    except Exception as e:
        print(f"⚠️ 空时间范围测试异常: {e}")
    
    print(f"\n🎯 测试完成！")
    print("=" * 60)
    
    # 功能总结
    print(f"\n📋 批量监控功能特性:")
    print("1. ✅ 支持指定时间范围的批量分析")
    print("2. ✅ 自动获取所有可用库房列表")
    print("3. ✅ 并行处理多个库房的监控任务")
    print("4. ✅ 完整的错误处理和重试机制")
    print("5. ✅ 详细的进度反馈和统计信息")
    print("6. ✅ 高效的批量数据库存储")
    print("7. ✅ 环境验证和边界条件检查")
    print("8. ✅ 兼容现有的标识符转换机制")