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
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

import pandas as pd
import numpy as np
from sqlalchemy.orm import sessionmaker

from utils.loguru_setting import logger
from global_const.global_const import pgsql_engine
from global_const.const_config import MUSHROOM_ROOM_IDS
from utils.create_table import (
    DecisionAnalysisStaticConfig, 
    DeviceSetpointChange,
    query_decision_analysis_static_configs
)


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
    max_retries = 3
    retry_delay = 5  # 秒
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[SETPOINT_MONITOR] 开始执行设定点变更监控 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            # 执行基于静态配置表的监控
            result = execute_static_config_based_monitoring()
            
            # 记录执行结果
            if result['success']:
                logger.info(f"[SETPOINT_MONITOR] 设定点监控完成: 处理 {result['successful_rooms']}/{result['total_rooms']} 个库房")
                logger.info(f"[SETPOINT_MONITOR] 检测到 {result['total_changes']} 个设定点变更，存储 {result['stored_records']} 条记录")
                
                # 记录有变更的库房
                changed_rooms = [room_id for room_id, count in result['changes_by_room'].items() if count > 0]
                if changed_rooms:
                    logger.info(f"[SETPOINT_MONITOR] 有变更的库房: {changed_rooms}")
                
                if result['error_rooms']:
                    logger.warning(f"[SETPOINT_MONITOR] 处理失败的库房: {result['error_rooms']}")
            else:
                logger.error("[SETPOINT_MONITOR] 设定点监控执行失败")
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"[SETPOINT_MONITOR] 设定点变更监控完成，耗时: {duration:.2f}秒")
            
            # 成功执行，退出重试循环
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SETPOINT_MONITOR] 设定点变更监控失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            # 检查是否是数据库连接错误
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[SETPOINT_MONITOR] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[SETPOINT_MONITOR] 设定点监控任务失败，已达到最大重试次数 ({max_retries})")
                # 不再抛出异常，避免调度器崩溃
                return
            else:
                # 非连接错误，不重试
                logger.error(f"[SETPOINT_MONITOR] 设定点监控任务遇到非连接错误，不再重试")
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
        'success': False,
        'total_rooms': 0,
        'successful_rooms': 0,
        'total_changes': 0,
        'changes_by_room': {},
        'error_rooms': [],
        'stored_records': 0,
        'processing_time': 0.0
    }
    
    processing_start = datetime.now()
    
    try:
        logger.info("[SETPOINT_MONITOR] 🚀 开始基于静态配置表的设定点监控")
        
        # 1. 从静态配置表获取所有测点配置
        logger.info("[SETPOINT_MONITOR] 📋 从静态配置表获取测点配置...")
        static_configs = get_static_configs_from_database()
        
        if not static_configs:
            logger.warning("[SETPOINT_MONITOR] ⚠️ 静态配置表中没有找到测点配置，使用备用方案")
            return execute_fallback_monitoring()
        
        logger.info(f"[SETPOINT_MONITOR] ✅ 从静态配置表获取到 {len(static_configs)} 个测点配置")
        
        # 2. 按库房分组配置
        configs_by_room = group_configs_by_room(static_configs)
        result['total_rooms'] = len(configs_by_room)
        
        logger.info(f"[SETPOINT_MONITOR] 📍 涉及 {len(configs_by_room)} 个库房: {list(configs_by_room.keys())}")
        
        # 3. 设定监控时间范围（最近1小时）
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
        
        logger.info(f"[SETPOINT_MONITOR] ⏰ 监控时间范围: {start_time} ~ {end_time}")
        
        # 4. 逐个库房处理
        all_changes = []
        successful_rooms = 0
        
        for room_id, room_configs in configs_by_room.items():
            try:
                logger.info(f"[SETPOINT_MONITOR] 🔍 处理库房 {room_id} ({len(room_configs)} 个测点)")
                
                # 获取库房的实时数据
                room_changes = monitor_room_with_static_configs(
                    room_id, room_configs, start_time, end_time
                )
                
                if room_changes:
                    logger.info(f"[SETPOINT_MONITOR] ✅ 库房 {room_id}: 检测到 {len(room_changes)} 个变更")
                    all_changes.extend(room_changes)
                    result['changes_by_room'][room_id] = len(room_changes)
                else:
                    logger.info(f"[SETPOINT_MONITOR] ⚪ 库房 {room_id}: 无变更")
                    result['changes_by_room'][room_id] = 0
                
                successful_rooms += 1
                
            except Exception as e:
                logger.error(f"[SETPOINT_MONITOR] ❌ 库房 {room_id} 处理失败: {e}")
                result['error_rooms'].append(room_id)
                result['changes_by_room'][room_id] = 0
                continue
        
        result['successful_rooms'] = successful_rooms
        result['total_changes'] = len(all_changes)
        
        # 5. 存储变更记录到数据库
        if all_changes:
            logger.info(f"[SETPOINT_MONITOR] 💾 存储 {len(all_changes)} 条变更记录到数据库...")
            stored_count = store_setpoint_changes_to_database(all_changes)
            result['stored_records'] = stored_count
            
            if stored_count == len(all_changes):
                logger.info(f"[SETPOINT_MONITOR] ✅ 成功存储 {stored_count} 条变更记录")
            else:
                logger.warning(f"[SETPOINT_MONITOR] ⚠️ 部分存储失败: {stored_count}/{len(all_changes)}")
        else:
            logger.info("[SETPOINT_MONITOR] ℹ️ 无变更记录需要存储")
            result['stored_records'] = 0
        
        # 6. 计算处理时间
        result['processing_time'] = (datetime.now() - processing_start).total_seconds()
        result['success'] = True
        
        logger.info(f"[SETPOINT_MONITOR] 🎯 监控完成: {successful_rooms}/{len(configs_by_room)} 库房成功")
        
        return result
        
    except Exception as e:
        logger.error(f"[SETPOINT_MONITOR] ❌ 静态配置监控执行失败: {e}")
        result['processing_time'] = (datetime.now() - processing_start).total_seconds()
        result['success'] = False
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
            limit=10000  # 设置较大的限制以获取所有配置
        )
        
        if not configs:
            logger.warning("[STATIC_CONFIG] 静态配置表中没有找到启用的配置")
            return []
        
        # 转换为字典格式
        config_dicts = []
        for config in configs:
            config_dict = {
                'id': str(config.id),
                'room_id': config.room_id,
                'device_type': config.device_type,
                'device_name': config.device_name,
                'device_alias': config.device_alias,
                'point_alias': config.point_alias,
                'point_name': config.point_name,
                'remark': config.remark,
                'change_type': config.change_type,
                'threshold': config.threshold,
                'enum_mapping': config.enum_mapping or {},
                'config_version': config.config_version,
                'effective_time': config.effective_time,
                'created_at': config.created_at
            }
            config_dicts.append(config_dict)
        
        logger.info(f"[STATIC_CONFIG] 成功获取 {len(config_dicts)} 个静态配置")
        
        # 按设备类型统计
        device_type_stats = {}
        for config in config_dicts:
            device_type = config['device_type']
            device_type_stats[device_type] = device_type_stats.get(device_type, 0) + 1
        
        logger.debug(f"[STATIC_CONFIG] 设备类型统计: {device_type_stats}")
        
        return config_dicts
        
    except Exception as e:
        logger.error(f"[STATIC_CONFIG] 从静态配置表获取配置失败: {e}")
        return []


def group_configs_by_room(static_configs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    按库房分组静态配置
    
    Args:
        static_configs: 静态配置列表
        
    Returns:
        Dict[str, List[Dict[str, Any]]]: 按库房分组的配置
    """
    configs_by_room = {}
    
    for config in static_configs:
        room_id = config['room_id']
        if room_id not in configs_by_room:
            configs_by_room[room_id] = []
        configs_by_room[room_id].append(config)
    
    # 按库房统计
    for room_id, room_configs in configs_by_room.items():
        device_types = set(config['device_type'] for config in room_configs)
        logger.debug(f"[CONFIG_GROUP] 库房 {room_id}: {len(room_configs)} 个测点, 设备类型: {device_types}")
    
    return configs_by_room


def monitor_room_with_static_configs(
    room_id: str, 
    room_configs: List[Dict[str, Any]], 
    start_time: datetime, 
    end_time: datetime
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
        realtime_data = get_realtime_setpoint_data(room_id, room_configs, start_time, end_time)
        
        if realtime_data.empty:
            logger.debug(f"[ROOM_MONITOR] 库房 {room_id} 无实时数据")
            return []
        
        logger.debug(f"[ROOM_MONITOR] 库房 {room_id} 获取到 {len(realtime_data)} 条实时数据")
        
        # 2. 检测变更
        changes = detect_changes_with_static_configs(realtime_data, room_configs)
        
        logger.debug(f"[ROOM_MONITOR] 库房 {room_id} 检测到 {len(changes)} 个变更")
        
        return changes
        
    except Exception as e:
        logger.error(f"[ROOM_MONITOR] 库房 {room_id} 监控失败: {e}")
        return []


def get_realtime_setpoint_data(
    room_id: str, 
    room_configs: List[Dict[str, Any]], 
    start_time: datetime, 
    end_time: datetime
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
        # 导入数据获取模块
        sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
        from dataframe_utils import get_all_device_configs
        from data_preprocessing import query_data_by_batch_time
        
        # 获取库房设备配置
        device_configs = get_all_device_configs(room_id=room_id)
        if not device_configs:
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无设备配置")
            return pd.DataFrame()
        
        # 合并所有设备类型的配置
        all_query_df = pd.concat(device_configs.values(), ignore_index=True)
        
        if all_query_df.empty:
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无设备数据")
            return pd.DataFrame()
        
        # 只保留静态配置中定义的测点
        config_point_aliases = {config['point_alias'] for config in room_configs}
        setpoint_df = all_query_df[all_query_df['point_alias'].isin(config_point_aliases)].copy()
        
        if setpoint_df.empty:
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无匹配的设定点数据")
            return pd.DataFrame()
        
        # 查询历史数据
        df = setpoint_df.groupby("device_alias", group_keys=False).apply(
            query_data_by_batch_time, 
            start_time, 
            end_time
        ).reset_index(drop=True).sort_values("time")
        
        if df.empty:
            logger.warning(f"[REALTIME_DATA] 库房 {room_id} 无历史数据")
            return pd.DataFrame()
        
        # 添加库房信息
        df['room_id'] = room_id
        
        logger.debug(f"[REALTIME_DATA] 库房 {room_id} 获取到 {len(df)} 条实时数据")
        
        return df
        
    except Exception as e:
        logger.error(f"[REALTIME_DATA] 获取库房 {room_id} 实时数据失败: {e}")
        return pd.DataFrame()


def detect_changes_with_static_configs(
    realtime_data: pd.DataFrame, 
    room_configs: List[Dict[str, Any]]
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
        for config in room_configs:
            key = f"{config['device_alias']}_{config['point_alias']}"
            config_mapping[key] = config
        
        # 检查数据结构
        logger.debug(f"[CHANGE_DETECT] 实时数据列: {list(realtime_data.columns)}")
        logger.debug(f"[CHANGE_DETECT] 数据样例: {realtime_data.head(1).to_dict('records') if not realtime_data.empty else 'Empty'}")
        
        # 根据实际数据结构选择分组字段
        if 'device_alias' in realtime_data.columns and 'point_name' in realtime_data.columns:
            # 按设备和测点分组检测变更
            grouped_data = realtime_data.groupby(['device_alias', 'point_name'])
            group_key_format = "device_alias_point_name"
        elif 'device_name' in realtime_data.columns and 'point_name' in realtime_data.columns:
            # 备用分组方式
            grouped_data = realtime_data.groupby(['device_name', 'point_name'])
            group_key_format = "device_name_point_name"
        else:
            logger.error(f"[CHANGE_DETECT] 数据结构不匹配，无法进行分组")
            return []
        
        logger.debug(f"[CHANGE_DETECT] 使用分组方式: {group_key_format}")
        
        for group_key, group in grouped_data:
            if len(group) < 2:
                continue  # 至少需要2个数据点才能检测变更
            
            # 根据分组方式构建配置键
            if group_key_format == "device_alias_point_name":
                device_alias, point_name = group_key
                config_key = f"{device_alias}_{point_name}"  # point_name实际是point_alias
            else:
                device_name, point_name = group_key
                # 需要从配置中找到对应的device_alias
                matching_config = None
                for config in room_configs:
                    if config['device_name'] == device_name and config['point_name'] == point_name:
                        matching_config = config
                        break
                if not matching_config:
                    logger.debug(f"[CHANGE_DETECT] 未找到匹配配置: {device_name}.{point_name}")
                    continue
                config_key = f"{matching_config['device_alias']}_{matching_config['point_alias']}"
            
            config = config_mapping.get(config_key)
            
            if not config:
                logger.debug(f"[CHANGE_DETECT] 未找到配置: {config_key}")
                continue
            
            # 按时间排序
            group = group.sort_values('time').reset_index(drop=True)
            
            # 检测变更
            group_changes = detect_point_changes(group, config)
            changes.extend(group_changes)
        
        logger.debug(f"[CHANGE_DETECT] 检测到 {len(changes)} 个变更")
        
        return changes
        
    except Exception as e:
        logger.error(f"[CHANGE_DETECT] 变更检测失败: {e}")
        import traceback
        logger.error(f"[CHANGE_DETECT] 错误详情: {traceback.format_exc()}")
        return []


def detect_point_changes(group: pd.DataFrame, config: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        change_type = config['change_type']
        threshold = config.get('threshold')
        enum_mapping = config.get('enum_mapping', {})
        
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
            
            # 根据变更类型检测变化
            if change_type == 'digital_on_off':
                # 数字量开关变化检测
                if int(current_value) != int(previous_value):
                    change_detected = True
                    change_info = {
                        'change_detail': f"{int(previous_value)} -> {int(current_value)}",
                        'change_magnitude': abs(current_value - previous_value)
                    }
            
            elif change_type == 'analog_value':
                # 模拟量变化检测
                if threshold and abs(current_value - previous_value) >= threshold:
                    change_detected = True
                    change_info = {
                        'change_detail': f"{previous_value:.2f} -> {current_value:.2f}",
                        'change_magnitude': abs(current_value - previous_value)
                    }
            
            elif change_type == 'enum_state':
                # 枚举状态变化检测
                if int(current_value) != int(previous_value):
                    change_detected = True
                    # 使用枚举映射获取状态描述
                    prev_desc = enum_mapping.get(str(int(previous_value)), str(int(previous_value)))
                    curr_desc = enum_mapping.get(str(int(current_value)), str(int(current_value)))
                    change_info = {
                        'change_detail': f"{prev_desc} -> {curr_desc}",
                        'change_magnitude': abs(current_value - previous_value)
                    }
            
            if change_detected:
                change_record = {
                    'room_id': config['room_id'],
                    'device_type': config['device_type'],
                    'device_name': config['device_name'],
                    'point_name': config['point_name'],
                    'point_description': config.get('remark', ''),
                    'change_time': current_row['time'],
                    'previous_value': float(previous_value),
                    'current_value': float(current_value),
                    'change_type': change_type,
                    'change_detail': change_info.get('change_detail', ''),
                    'change_magnitude': change_info.get('change_magnitude', 0.0),
                    'detection_time': datetime.now()
                }
                changes.append(change_record)
                
                logger.debug(f"[POINT_CHANGE] {config['device_name']}.{config['point_name']}: {change_info.get('change_detail', '')}")
        
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
        
        # 存储到数据库
        stored_count = df.to_sql(
            'device_setpoint_changes',
            con=pgsql_engine,
            if_exists='append',
            index=False,
            method='multi',
            chunksize=1000
        )
        
        logger.info(f"[DB_STORE] 成功存储 {len(changes)} 条变更记录")
        return len(changes)
        
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
            start_time=start_time,
            end_time=end_time,
            store_results=True
        )
        
        logger.info("[FALLBACK] ✅ 备用监控方案执行完成")
        return result
        
    except Exception as e:
        logger.error(f"[FALLBACK] ❌ 备用监控方案失败: {e}")
        return {
            'success': False,
            'total_rooms': 0,
            'successful_rooms': 0,
            'total_changes': 0,
            'changes_by_room': {},
            'error_rooms': [],
            'stored_records': 0,
            'processing_time': 0.0
        }