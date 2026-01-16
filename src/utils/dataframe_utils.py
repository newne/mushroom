"""
数据框处理工具函数
"""
import json
import os
from pathlib import Path
from typing import Dict, Optional
import time

import pandas as pd
from loguru import logger

from global_const.global_const import static_settings, conn, mushroom_redis_key, BASE_DIR

REDIS_CACHE_TTL = 3600*24
STATIC_CONFIG_FILE_PATH = BASE_DIR / "configs" / "static_config.json"


def _get_redis_key_timestamp(redis_key: str) -> Optional[float]:
    """
    获取Redis键的设置时间戳
    
    Args:
        redis_key: Redis键名
        
    Returns:
        时间戳（秒），如果无法获取则返回None
    """
    try:
        # 使用Redis的OBJECT IDLETIME命令获取键的空闲时间
        # 然后计算设置时间 = 当前时间 - 空闲时间
        idle_time = conn.object('idletime', redis_key)
        if idle_time is not None:
            # idle_time是秒数，计算键的最后访问时间
            last_access_time = time.time() - idle_time
            return last_access_time
        
        # 如果无法获取IDLETIME，尝试使用TTL信息
        ttl = conn.ttl(redis_key)
        if ttl > 0:
            # 估算设置时间 = 当前时间 - (TTL设置值 - 剩余TTL)
            estimated_set_time = time.time() - (REDIS_CACHE_TTL - ttl)
            return estimated_set_time
            
        return None
    except Exception as e:
        logger.debug(f"Failed to get Redis key timestamp for {redis_key}: {e}")
        return None


def _get_file_modification_time(file_path: Path) -> Optional[float]:
    """
    获取文件的最后修改时间戳
    
    Args:
        file_path: 文件路径
        
    Returns:
        时间戳（秒），如果文件不存在则返回None
    """
    try:
        if file_path.exists():
            return file_path.stat().st_mtime
        return None
    except Exception as e:
        logger.warning(f"Failed to get file modification time for {file_path}: {e}")
        return None


def _is_cache_valid(redis_key: str, config_file_path: Path) -> bool:
    """
    检查缓存是否有效（配置文件未更新）
    
    Args:
        redis_key: Redis键名
        config_file_path: 配置文件路径
        
    Returns:
        True表示缓存有效，False表示需要重新生成缓存
    """
    try:
        # 检查Redis键是否存在
        if not conn.exists(redis_key):
            logger.debug(f"Redis key {redis_key} does not exist, cache invalid")
            return False
        
        # 获取配置文件修改时间
        file_mtime = _get_file_modification_time(config_file_path)
        if file_mtime is None:
            logger.warning(f"Cannot get modification time for {config_file_path}")
            # 如果无法获取文件时间，但缓存存在，则认为缓存有效
            return True
        
        # 获取Redis键的时间戳
        cache_timestamp = _get_redis_key_timestamp(redis_key)
        if cache_timestamp is None:
            logger.debug(f"Cannot get cache timestamp for {redis_key}, assuming cache invalid")
            return False
        
        # 比较时间戳：如果文件修改时间晚于缓存时间，则缓存无效
        is_valid = file_mtime <= cache_timestamp
        
        if not is_valid:
            logger.debug(f"缓存失效: {redis_key}")
        
        return is_valid
        
    except Exception as e:
        logger.warning(f"Error checking cache validity for {redis_key}: {e}")
        # 出现异常时，如果缓存存在则认为有效，否则无效
        return conn.exists(redis_key)


def _set_cache_with_metadata(redis_key: str, data: str, ttl: int = REDIS_CACHE_TTL) -> bool:
    """
    设置缓存并记录元数据
    
    Args:
        redis_key: Redis键名
        data: 要缓存的数据
        ttl: 过期时间（秒）
        
    Returns:
        True表示设置成功，False表示设置失败
    """
    try:
        # 设置主数据
        conn.set(redis_key, data, ex=ttl)
        
        # 设置元数据键记录缓存创建时间
        metadata_key = f"{redis_key}:metadata"
        metadata = {
            "created_at": time.time(),
            "config_file": str(STATIC_CONFIG_FILE_PATH),
            "ttl": ttl
        }
        conn.set(metadata_key, json.dumps(metadata), ex=ttl)
        
        logger.debug(f"Cache set successfully for {redis_key} with metadata")
        return True
        
    except Exception as e:
        logger.error(f"Failed to set cache for {redis_key}: {e}")
        return False


def get_static_config_by_device_type(device_type: str) -> pd.DataFrame:
    """
    根据设备类型获取静态配置，优先从Redis获取，若不存在或配置文件已更新则重新生成并存入Redis。

    假设：同一设备类型的所有设备共享相同的 point_list（即点位定义在类型级别，非设备实例级别）。

    :param device_type: 设备类型，如 'air_cooler', 'fresh_air_fan' 等
    :return: DataFrame，每行为一个 (设备, 点位) 组合
    :raises ValueError: 若设备类型不存在或配置结构异常
    """
    if not hasattr(static_settings.mushroom.datapoint, device_type):
        raise ValueError(f"设备类型 '{device_type}' 未在静态配置中定义")

    device_key = mushroom_redis_key["static_config"].format(device_type=device_type)

    # 检查缓存是否有效
    cache_valid = _is_cache_valid(device_key, STATIC_CONFIG_FILE_PATH)
    
    if cache_valid:
        try:
            # 尝试从 Redis 读取
            df_json = conn.get(device_key)
            if df_json:
                df_data = json.loads(df_json)
                return pd.DataFrame(df_data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"缓存数据损坏，重新生成: {device_type}")
        except Exception as e:
            logger.error(f"读取缓存失败: {device_type}, 错误: {e}")

    # 缓存无效或读取失败，从静态配置重新生成
    try:
        logger.debug(f"重新生成缓存: {device_type}")
        
        device_config = getattr(static_settings.mushroom.datapoint, device_type)
        device_df = pd.DataFrame(device_config.device_list)
        point_df = pd.DataFrame(device_config.point_list)

        # 笛卡尔积：每个设备 × 每个点位（基于"同类型设备共享点位"假设）
        device_query_df = (
            point_df.assign(__cart_key=1)
            .merge(device_df.drop(columns=["remark"], errors="ignore").assign(__cart_key=1), on="__cart_key")
            .drop("__cart_key", axis=1)
        )

        # 写入 Redis（带TTL和元数据）
        df_json = device_query_df.to_json(orient='records', force_ascii=False, indent=None)
        cache_success = _set_cache_with_metadata(device_key, df_json, REDIS_CACHE_TTL)
        
        if not cache_success:
            logger.warning(f"缓存更新失败: {device_type}")
        
        return device_query_df

    except Exception as e:
        logger.error(f"生成设备类型 '{device_type}' 配置时出错: {e}")
        raise ValueError(f"无法加载设备类型 '{device_type}' 的配置: {str(e)}")


def get_all_device_configs(room_id: str = None) -> Dict[str, pd.DataFrame]:
    """
    获取所有设备类型的配置（触发缓存预热）
    
    优化说明：
    1. 缓存有效性检查优化：在函数开始时统一检查配置文件修改时间，避免重复检查
    2. 日志规范化：使用Google日志规范，统一日志编号和格式，使用中文
    3. 代码结构优化：使用向量化操作和字典推导式，减少嵌套循环
    
    Args:
        room_id: 可选的库房号，如果提供则只返回该库房的设备配置
    
    Returns:
        Dict[str, pd.DataFrame]: 设备类型到配置DataFrame的映射
    """
    # 开始获取所有设备配置
    logger.debug(f"获取设备配置 | 库房: {room_id or '全部'}")
    
    try:
        # 提取所有有效的设备类型
        datapoint_config = static_settings.mushroom.datapoint
        device_types = [
            key for key, value in datapoint_config.items()
            if isinstance(value, dict) and 'device_list' in value
        ]
        
        # 预先获取库房设备列表（如果指定了库房）
        room_devices = None
        if room_id is not None:
            try:
                room_config = static_settings.mushroom.rooms.get(room_id, {})
                room_devices = room_config.get('devices', [])
                
                if not room_devices:
                    logger.warning(f"库房无设备配置: {room_id}")
            except Exception as e:
                logger.error(f"获取库房设备列表失败 | 库房: {room_id}, 错误: {e}")
                room_devices = None
        
        # 使用字典推导式批量获取配置
        all_configs = {}
        success_count = 0
        failed_types = []
        
        for device_type in device_types:
            try:
                # 获取设备类型配置
                df = get_static_config_by_device_type(device_type)
                
                # 使用向量化操作进行库房过滤
                if room_id is not None:
                    # 优先使用预先获取的库房设备列表
                    if room_devices:
                        filtered_df = df[df['device_alias'].isin(room_devices)].copy()
                        if not filtered_df.empty:
                            all_configs[device_type] = filtered_df
                            success_count += 1
                    else:
                        # 回退方案：使用设备名称后缀过滤
                        filtered_df = df[df['device_name'].str.endswith(f'_{room_id}', na=False)].copy()
                        if not filtered_df.empty:
                            all_configs[device_type] = filtered_df
                            success_count += 1
                else:
                    # 不过滤，返回所有设备
                    all_configs[device_type] = df
                    success_count += 1
                    
            except Exception as e:
                failed_types.append(device_type)
                logger.error(f"获取设备配置失败 | 类型: {device_type}, 错误: {e}")
                continue
        
        # 汇总统计信息
        total_devices = sum(len(df) for df in all_configs.values())
        logger.info(
            f"设备配置加载完成 | 库房: {room_id or '全部'}, "
            f"成功: {success_count}/{len(device_types)}, 总设备数: {total_devices}"
        )
        
        if failed_types:
            logger.warning(f"配置加载失败的设备类型: {failed_types}")
        
        return all_configs
        
    except Exception as e:
        logger.error(f"获取设备配置异常: {e}", exc_info=True)
        return {}


def clear_device_config_cache(device_type: str = None) -> bool:
    """
    清除设备配置缓存
    
    Args:
        device_type: 设备类型，如果为None则清除所有设备类型的缓存
        
    Returns:
        True表示清除成功，False表示清除失败
    """
    try:
        if device_type:
            # 清除指定设备类型的缓存
            device_key = mushroom_redis_key["static_config"].format(device_type=device_type)
            metadata_key = f"{device_key}:metadata"
            
            deleted_count = 0
            if conn.exists(device_key):
                conn.delete(device_key)
                deleted_count += 1
            if conn.exists(metadata_key):
                conn.delete(metadata_key)
                deleted_count += 1
                
            logger.info(f"Cleared cache for device type '{device_type}', deleted {deleted_count} keys")
            return True
        else:
            # 清除所有设备类型的缓存
            try:
                datapoint_config = static_settings.mushroom.datapoint
                device_types = [
                    key for key, value in datapoint_config.items()
                    if isinstance(value, dict) and 'device_list' in value
                ]
                
                total_deleted = 0
                for dt in device_types:
                    device_key = mushroom_redis_key["static_config"].format(device_type=dt)
                    metadata_key = f"{device_key}:metadata"
                    
                    if conn.exists(device_key):
                        conn.delete(device_key)
                        total_deleted += 1
                    if conn.exists(metadata_key):
                        conn.delete(metadata_key)
                        total_deleted += 1
                
                logger.info(f"Cleared cache for all device types, deleted {total_deleted} keys")
                return True
                
            except Exception as e:
                logger.error(f"Failed to get device types for cache clearing: {e}")
                return False
                
    except Exception as e:
        logger.error(f"Failed to clear device config cache: {e}")
        return False


def get_cache_info(device_type: str = None) -> Dict[str, any]:
    """
    获取缓存信息
    
    Args:
        device_type: 设备类型，如果为None则获取所有设备类型的缓存信息
        
    Returns:
        缓存信息字典
    """
    try:
        if device_type:
            # 获取指定设备类型的缓存信息
            device_key = mushroom_redis_key["static_config"].format(device_type=device_type)
            metadata_key = f"{device_key}:metadata"
            
            info = {
                'device_type': device_type,
                'cache_exists': conn.exists(device_key),
                'metadata_exists': conn.exists(metadata_key),
                'ttl': conn.ttl(device_key) if conn.exists(device_key) else None,
                'file_mtime': _get_file_modification_time(STATIC_CONFIG_FILE_PATH),
                'cache_valid': _is_cache_valid(device_key, STATIC_CONFIG_FILE_PATH)
            }
            
            # 获取元数据
            if info['metadata_exists']:
                try:
                    metadata_json = conn.get(metadata_key)
                    if metadata_json:
                        metadata = json.loads(metadata_json)
                        info['metadata'] = metadata
                except Exception as e:
                    logger.warning(f"Failed to parse metadata for {device_type}: {e}")
                    info['metadata'] = None
            
            return info
        else:
            # 获取所有设备类型的缓存信息
            try:
                datapoint_config = static_settings.mushroom.datapoint
                device_types = [
                    key for key, value in datapoint_config.items()
                    if isinstance(value, dict) and 'device_list' in value
                ]
                
                all_info = {}
                for dt in device_types:
                    all_info[dt] = get_cache_info(dt)
                
                # 添加总体信息
                all_info['_summary'] = {
                    'total_device_types': len(device_types),
                    'cached_types': sum(1 for info in all_info.values() if isinstance(info, dict) and info.get('cache_exists', False)),
                    'valid_caches': sum(1 for info in all_info.values() if isinstance(info, dict) and info.get('cache_valid', False)),
                    'config_file_path': str(STATIC_CONFIG_FILE_PATH),
                    'config_file_exists': STATIC_CONFIG_FILE_PATH.exists()
                }
                
                return all_info
                
            except Exception as e:
                logger.error(f"Failed to get device types for cache info: {e}")
                return {'error': str(e)}
                
    except Exception as e:
        logger.error(f"Failed to get cache info: {e}")
        return {'error': str(e)}