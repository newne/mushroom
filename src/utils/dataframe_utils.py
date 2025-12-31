"""
数据框处理工具函数
"""
import json
from typing import Dict

import pandas as pd
from loguru import logger

from global_const.global_const import static_settings, conn, mushroom_redis_key

REDIS_CACHE_TTL = 3600*24


def get_static_config_by_device_type(device_type: str) -> pd.DataFrame:
    """
    根据设备类型获取静态配置，优先从Redis获取，若不存在则从文件加载并存入Redis（带TTL）。

    假设：同一设备类型的所有设备共享相同的 point_list（即点位定义在类型级别，非设备实例级别）。

    :param device_type: 设备类型，如 'air_cooler', 'fresh_air_fan' 等
    :return: DataFrame，每行为一个 (设备, 点位) 组合
    :raises ValueError: 若设备类型不存在或配置结构异常
    """
    if not hasattr(static_settings.mushroom.datapoint, device_type):
        raise ValueError(f"设备类型 '{device_type}' 未在静态配置中定义")

    device_key = mushroom_redis_key["static_config"].format(device_type=device_type)

    try:
        # 尝试从 Redis 读取
        if (df_json := conn.get(device_key)):
            df_data = json.loads(df_json)
            return pd.DataFrame(df_data)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Redis 中 {device_key} 的缓存数据损坏，将重新生成: {e}")
    except Exception as e:
        logger.error(f"从 Redis 读取 {device_key} 失败: {e}")

    # 从静态配置生成
    try:
        device_config = getattr(static_settings.mushroom.datapoint, device_type)
        device_df = pd.DataFrame(device_config.device_list)
        point_df = pd.DataFrame(device_config.point_list)

        # 笛卡尔积：每个设备 × 每个点位（基于“同类型设备共享点位”假设）
        device_query_df = (
            point_df.assign(__cart_key=1)
            .merge(device_df.drop(columns=["remark"], errors="ignore").assign(__cart_key=1), on="__cart_key")
            .drop("__cart_key", axis=1)
        )

        # 写入 Redis（带TTL）
        df_json = device_query_df.to_json(orient='records', force_ascii=False, indent=None)
        conn.set(device_key, df_json, ex=REDIS_CACHE_TTL)
        return device_query_df

    except Exception as e:
        logger.error(f"生成设备类型 '{device_type}' 配置时出错: {e}")
        raise ValueError(f"无法加载设备类型 '{device_type}' 的配置: {str(e)}")


def get_all_device_configs(room_id: str = None) -> Dict[str, pd.DataFrame]:
    """
    获取所有设备类型的配置（触发缓存预热）
    
    Args:
        room_id: 可选的库房号，如果提供则只返回该库房的设备配置
    
    Returns:
        Dict[str, pd.DataFrame]: 设备类型到配置DataFrame的映射
    """
    datapoint_config = static_settings.mushroom.datapoint
    device_types = [
        key for key, value in datapoint_config.items()
        if isinstance(value, dict) and 'device_list' in value
    ]
    
    all_configs = {}
    for device_type in device_types:
        df = get_static_config_by_device_type(device_type)
        
        # 如果指定了库房号，则根据静态配置中的房间设备列表进行过滤
        if room_id is not None:
            try:
                # 从静态配置获取该房间的设备列表
                room_devices = static_settings.mushroom.rooms.get(room_id, {}).get('devices', [])
                if room_devices:
                    # 根据设备别名过滤DataFrame
                    filtered_df = df[df['device_alias'].isin(room_devices)]
                    if not filtered_df.empty:
                        all_configs[device_type] = filtered_df
                        logger.debug(f"Found {len(filtered_df)} devices for room {room_id}, device_type {device_type}")
                else:
                    logger.warning(f"No devices configured for room {room_id} in static settings")
            except Exception as e:
                logger.error(f"Error filtering devices for room {room_id}: {e}")
                # 如果静态配置读取失败，回退到原来的逻辑
                filtered_df = df[df['device_name'].str.endswith(f'_{room_id}')]
                if not filtered_df.empty:
                    all_configs[device_type] = filtered_df
        else:
            all_configs[device_type] = df
    
    return all_configs