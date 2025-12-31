"""
环境数据处理器
根据图像解析出的时间和库房号，查询历史环境数据并生成结构化记录
集成 get_env_status.py 中的数据查询和处理逻辑
"""

import json
from datetime import datetime, timedelta, date
from typing import Dict, Optional, Any

import pandas as pd
from loguru import logger

from utils.data_preprocessing import (
    query_data_by_batch_time,
)
from utils.dataframe_utils import get_all_device_configs


class EnvironmentDataProcessor:
    """环境数据处理器"""
    
    def __init__(self):
        """初始化环境数据处理器"""
        try:
            # 设备配置缓存，避免重复查询
            self._device_config_cache = {}
            self._cache_timestamp = {}
            self._cache_ttl = 300  # 缓存5分钟
            
            logger.info("Environment data processor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize environment data processor: {e}")
            raise
    
    def _get_device_configs_cached(self, room_id: str) -> Optional[Dict]:
        """
        获取设备配置，使用缓存避免重复查询
        
        Args:
            room_id: 库房号
            
        Returns:
            设备配置字典
        """
        current_time = datetime.now()
        
        # 检查缓存是否有效
        if (room_id in self._device_config_cache and 
            room_id in self._cache_timestamp and
            (current_time - self._cache_timestamp[room_id]).total_seconds() < self._cache_ttl):
            
            logger.debug(f"使用缓存的设备配置: 库房 {room_id}")
            return self._device_config_cache[room_id]
        
        # 重新查询并缓存
        logger.debug(f"查询设备配置: 库房 {room_id}")
        room_configs = get_all_device_configs(room_id=room_id)
        
        if room_configs:
            self._device_config_cache[room_id] = room_configs
            self._cache_timestamp[room_id] = current_time
            logger.debug(f"缓存设备配置: 库房 {room_id}, 设备类型数量: {len(room_configs)}")
        
        return room_configs
    
    def get_environment_data(self, room_id: str, collection_time: datetime, 
                           image_path: str, time_window_minutes: int = 1) -> Optional[Dict[str, Any]]:
        """
        获取指定时间和库房的环境数据
        
        Args:
            room_id: 库房号
            collection_time: 采集时间
            image_path: 图像路径
            time_window_minutes: 时间窗口（分钟）
            
        Returns:
            结构化的环境数据记录，失败返回None
        """
        try:
            # 获取特定库房的设备配置（使用缓存）
            room_configs = self._get_device_configs_cached(room_id)
            
            if not room_configs:
                logger.warning(f"No device configuration found for room {room_id}")
                return None
            
            # 合并所有设备类型的配置
            all_query_df = pd.concat(room_configs.values(), ignore_index=True)
            
            if all_query_df.empty:
                logger.warning(f"No device data available for room {room_id}")
                return None
            
            # 计算查询时间范围 - 查询历史2分钟数据
            start_time = collection_time - timedelta(minutes=2)
            end_time = collection_time
            
            logger.info(f"Querying environment data for room {room_id}, time range: {start_time} ~ {end_time}")
            
            # 查询历史数据 - 使用与 get_env_status.py 相同的逻辑
            df = all_query_df.groupby("device_alias").apply(
                query_data_by_batch_time, 
                start_time, 
                end_time, 
                include_groups=True  # 包含分组列，避免KeyError
            ).reset_index().sort_values("time")
            
            if df.empty:
                logger.warning(f"No historical data found for room {room_id} in time range {start_time} ~ {end_time}")
                return None
            
            # 添加库房信息 - 从设备名称中提取库房号
            df['room'] = df['device_name'].apply(lambda x: x.split('_')[-1])
            
            # 创建透视表 - 与 get_env_status.py 中的 df1 相同结构
            df1 = df.pivot_table(
                index='time',
                columns=['room', 'device_name', 'point_name'],
                values='value'
            )
            
            if df1.empty:
                logger.warning(f"Empty pivot table for room {room_id}")
                return None
            
            # 获取最新时间点的数据
            latest_time = df1.index[-1]
            
            # 构建 iot_data 字典 - 格式: {(room_id, device_name, point_name): value}
            iot_data = {}
            for col in df1.columns:
                room, device_name, point_name = col
                if room == room_id:  # 只处理指定库房的数据
                    key = (room, device_name, point_name)
                    value = df1.loc[latest_time, col]
                    if pd.notna(value):
                        iot_data[key] = value
            
            if not iot_data:
                logger.warning(f"No valid IoT data found for room {room_id}")
                return None
            
            # 使用 build_structured_record 函数构建结构化记录
            structured_record = self.build_structured_record(
                room_id=room_id,
                iot_data=iot_data,
                image_path=image_path,
                collection_time=collection_time
            )
            
            logger.info(f"Successfully retrieved environment data for room {room_id}")
            logger.debug(f"Semantic description for room {room_id}: {structured_record.get('semantic_description', 'N/A')}")
            return structured_record
                
        except Exception as e:
            logger.error(f"Failed to get environment data for room {room_id}: {e}")
            return None
    
    def build_structured_record(
        self,
        room_id: str,
        iot_data: dict,  # 单库房原始测点字典
        image_path: str,
        collection_time: datetime
    ) -> dict:
        """
        构建结构化的环境数据记录
        使用与 get_env_status.py 中 build_structured_record 函数相同的逻辑
        
        Args:
            room_id: 库房号
            iot_data: IoT数据字典，格式: {(room_id, device_name, point_name): value}
            image_path: 图像路径
            collection_time: 采集时间
            
        Returns:
            结构化的环境数据记录
        """
        try:
            # === 1. 基础信息 ===
            in_year = int(iot_data.get((room_id, f"mushroom_info_{room_id}", "in_year"), 0))
            in_month = int(iot_data.get((room_id, f"mushroom_info_{room_id}", "in_month"), 0))
            in_day = int(iot_data.get((room_id, f"mushroom_info_{room_id}", "in_day"), 0))
            in_num = int(iot_data.get((room_id, f"mushroom_info_{room_id}", "in_num"), 0))
            growth_day = int(iot_data.get((room_id, f"mushroom_info_{room_id}", "in_day_num"), 0))

            # 处理日期，避免无效日期
            try:
                if in_year > 0 and in_month > 0 and in_day > 0:
                    in_date = date(in_year, in_month, in_day)
                else:
                    in_date = collection_time.date()  # 使用采集日期作为默认值
            except ValueError:
                in_date = collection_time.date()
            
            growth_stage = "normal" if 1 <= growth_day <= 27 else "non_growth"

            # === 2. 冷风机 ===
            air_cooler_config = {
                "on_off": int(iot_data.get((room_id, f"air_cooler_{room_id}", "on_off"), 0)),
                "status": int(iot_data.get((room_id, f"air_cooler_{room_id}", "status"), 0)),
                "temp_set": float(iot_data.get((room_id, f"air_cooler_{room_id}", "temp_set"), 0.0)),
                "temp": float(iot_data.get((room_id, f"air_cooler_{room_id}", "temp"), 0.0)),
                "temp_diffset": float(iot_data.get((room_id, f"air_cooler_{room_id}", "temp_diffset"), 0.0)),
            }

            # === 3. 新风机 ===
            fresh_fan_config = {
                "mode": int(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "mode"), 0)),
                "control": int(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "control"), 0)),
                "status": int(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "status"), 0)),
                "time_on": int(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "on"), 0)),
                "time_off": int(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "off"), 0)),
                "co2_on": float(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "co2_on"), 0.0)),
                "co2_off": float(iot_data.get((room_id, f"fresh_air_fan_{room_id}", "co2_off"), 0.0)),
            }

            # === 4. 补光灯 ===
            light_model = int(iot_data.get((room_id, f"grow_light_{room_id}", "model"), 0))
            light_status = int(iot_data.get((room_id, f"grow_light_{room_id}", "mode"), 0))  # 注意：status alias 是 mode
            light_on = int(iot_data.get((room_id, f"grow_light_{room_id}", "on_mset"), 0))
            light_off = int(iot_data.get((room_id, f"grow_light_{room_id}", "off_mset"), 0))

            light_active = (light_model != 0) and (light_status < 3)
            light_count = 1 if light_active else 0
            light_config = {
                "model": light_model,
                "status": light_status,
                "on_mset": light_on,
                "off_mset": light_off
            }

            # === 5. 加湿器 ===
            def _parse_humidifier(side: str):
                alias = f"{side}_humidifier_{room_id}"
                mode = int(iot_data.get((room_id, alias, "mode"), 0))
                status = int(iot_data.get((room_id, alias, "status"), 3))  # 默认关闭
                on_val = int(iot_data.get((room_id, alias, "on"), 0))
                off_val = int(iot_data.get((room_id, alias, "off"), 0))
                return {
                    "on": on_val,
                    "off": off_val,
                    "mode": mode,
                    "status": status  # 用于判断是否启用
                }

            left_humid = _parse_humidifier("left")
            right_humid = _parse_humidifier("right")

            # 启用条件：status != 3（非"加湿关闭"）
            humidifier_count = sum(
                1 for h in [left_humid, right_humid] if h["status"] != 3
            )

            humidifier_config = {
                "left": left_humid,
                "right": right_humid
            }

            # === 6. 环境传感器 ===
            env_sensor_status = {
                "temperature": float(iot_data.get((room_id, f"env_status_{room_id}", "temperature"), 0.0)),
                "humidity": float(iot_data.get((room_id, f"env_status_{room_id}", "humidity"), 0.0)),
                "co2": float(iot_data.get((room_id, f"env_status_{room_id}", "co2"), 0.0)),
            }

            # === 7. 语义描述（用于 CLIP 嵌入）===
            # 使用简化的身份元数据模板，专注于以图搜图
            text_for_embedding = f"Mushroom Room {room_id}, {growth_stage} stage, Day {growth_day}."
            semantic_description = text_for_embedding

            # === 8. 构建完整记录 ===
            return {
                "room_id": room_id,
                "collection_datetime": collection_time,
                "image_path": image_path,
                "file_name": image_path.split("/")[-1],
                "in_date": in_date,
                "in_num": in_num,
                "growth_day": growth_day,
                "growth_stage": growth_stage,
                "air_cooler_config": air_cooler_config,  # 直接传递字典，不序列化
                "fresh_fan_config": fresh_fan_config,    # 直接传递字典，不序列化
                "light_count": light_count,
                "light_config": light_config,            # 直接传递字典，不序列化
                "humidifier_count": humidifier_count,
                "humidifier_config": humidifier_config,  # 直接传递字典，不序列化
                "env_sensor_status": env_sensor_status,  # 直接传递字典，不序列化
                "semantic_description": semantic_description,
            }
            
        except Exception as e:
            logger.error(f"Failed to build structured record for room {room_id}: {e}")
            raise


def create_env_data_processor() -> EnvironmentDataProcessor:
    """创建环境数据处理器实例"""
    return EnvironmentDataProcessor()


if __name__ == "__main__":
    # 测试代码
    processor = create_env_data_processor()
    
    # 测试获取环境数据
    test_room_id = "611"
    test_time = datetime(2025, 12, 30, 16, 2)
    test_image_path = "611/20251230/611_1921681237_20251230_20251230160200.jpg"
    
    env_data = processor.get_environment_data(
        room_id=test_room_id,
        collection_time=test_time,
        image_path=test_image_path
    )
    
    if env_data:
        print("✅ 环境数据获取成功:")
        print(f"   语义描述: {env_data['semantic_description']}")
        print(f"   生长阶段: {env_data['growth_stage']}")
        print(f"   补光数量: {env_data['light_count']}")
    else:
        print("❌ 环境数据获取失败")