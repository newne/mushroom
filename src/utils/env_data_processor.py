"""
环境数据处理器
根据图像解析出的时间和库房号，查询历史环境数据并生成结构化记录
集成 get_env_status.py 中的数据查询和处理逻辑
"""

import json
from datetime import datetime, timedelta, date
from typing import Dict, Optional, Any

import pandas as pd
import numpy as np
from loguru import logger

from utils.data_preprocessing import (
    query_data_by_batch_time,
)
from utils.dataframe_utils import get_all_device_configs
from global_const.global_const import pgsql_engine, static_settings
from utils.create_table import MushroomEnvDailyStats
from sqlalchemy import text

import sqlalchemy


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
            df = all_query_df.groupby("device_alias", group_keys=False).apply(
                query_data_by_batch_time, 
                start_time, 
                end_time
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
            text_for_embedding = f"Mushroom Room {room_id}, Day {growth_day}."
            semantic_description = text_for_embedding

            # === 8. 构建完整记录 ===
            return {
                "room_id": room_id,
                "collection_datetime": collection_time,
                "image_path": image_path,
                "in_date": in_date,
                "in_num": in_num,
                "growth_day": growth_day,
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

    def compute_and_store_daily_stats(self, start_time: datetime, end_time: Optional[datetime] = None, rooms: Optional[list] = None):
        """
        计算给定时间区间（或单日）内所有库房的日度温/湿/CO2 统计并写入 mushroom_env_daily_stats 表。

        参数:
            start_time: 开始时间（包含）
            end_time: 结束时间（不包含），若为 None 则统计 start_time 当天
            rooms: 可选房间列表，若为 None 则从静态配置或设备列表自动推断
        """
        if end_time is None:
            # 统计单日
            day_start = datetime(
                start_time.year, start_time.month, start_time.day)
            day_end = day_start + timedelta(days=1)
        else:
            day_start = start_time
            day_end = end_time

        # 推断房间列表
        if rooms is None:
            try:
                # 优先从静态配置读取 rooms
                rooms_cfg = getattr(static_settings.mushroom, 'rooms', {})
                rooms = list(rooms_cfg.keys()) if rooms_cfg else []
            except Exception:
                rooms = []

            if not rooms:
                # 回退：从所有设备配置中解析 room 后缀
                all_configs = get_all_device_configs()
                room_set = set()
                for df in all_configs.values():
                    if 'device_name' in df.columns:
                        for name in df['device_name'].astype(str):
                            if '_' in name:
                                room_set.add(name.split('_')[-1])
                rooms = sorted(room_set)

        logger.info(
            f"Computing daily stats for rooms: {rooms}, range: {day_start} ~ {day_end}")

        # For each room, query historical data and compute stats per day (day granularity)
        results = []
        for room in rooms:
            try:
                room_configs = self._get_device_configs_cached(room)
                if not room_configs:
                    logger.debug(f"No configs for room {room}, skipping")
                    continue

                all_query_df = pd.concat(
                    room_configs.values(), ignore_index=True)
                if all_query_df.empty:
                    logger.debug(
                        f"Empty device query df for room {room}, skipping")
                    continue

                df = all_query_df.groupby("device_alias", group_keys=False).apply(
                    query_data_by_batch_time, day_start, day_end
                ).reset_index().sort_values("time")

                if df.empty:
                    logger.debug(
                        f"No historical data for room {room} in range, skipping")
                    continue

                # ensure time column is datetime and derive room
                df['time'] = pd.to_datetime(df['time'])
                df['room'] = df['device_name'].apply(
                    lambda x: str(x).split('_')[-1])

                # filter to this room and time window
                df_room = df[df['room'] == room].copy()
                if df_room.empty:
                    logger.debug(
                        f"No data rows matching room {room}, skipping")
                    continue

                df_room = df_room[(df_room['time'] >= day_start) & (
                    df_room['time'] < day_end)].copy()
                if df_room.empty:
                    logger.debug(
                        f"No data for room {room} in the specified date range after filtering, skipping")
                    continue

                # add a stat_date column once for fast filtering later
                df_room['stat_date'] = df_room['time'].dt.date

                # vectorized aggregation: compute counts, min/max/mean and quantiles per stat_date and point
                mask = df_room['point_name'].isin(
                    ['temperature', 'humidity', 'co2'])
                df_points = df_room[mask]
                if df_points.empty:
                    logger.debug(
                        f"No temperature/humidity/co2 points for room {room}, skipping")
                    continue

                agg = df_points.groupby(['stat_date', 'point_name'])['value'].agg(
                    count='count',
                    min='min',
                    max='max',
                    mean='mean',
                    q25=lambda x: x.quantile(0.25),
                    median=lambda x: x.quantile(0.5),
                    q75=lambda x: x.quantile(0.75),
                ).reset_index()

                if agg.empty:
                    logger.debug(
                        f"Aggregation result empty for room {room}, skipping")
                    continue

                # pivot so we can access metrics per point quickly
                pivot = agg.set_index(
                    ['stat_date', 'point_name']).unstack(level=-1)

                # prepare mushroom_info per stat_date to extract in_day_num and batch_date without per-row loops
                m_info = df_room[df_room['device_name'].astype(
                    str).str.startswith(f'mushroom_info_{room}')]
                m_info_pivot = None
                if not m_info.empty:
                    m_info_pivot = m_info.pivot_table(
                        index='stat_date', columns='point_name', values='value', aggfunc='first')

                # Vectorized: flatten pivot columns to <point>_<metric>, build df per stat_date
                flat = pivot.copy()
                # pivot columns are (metric, point) -> convert to point_metric
                flat.columns = [f"{col[1]}_{col[0]}" for col in flat.columns]
                df_flat = flat.reset_index()
                # ensure stat_date is date
                df_flat['stat_date'] = pd.to_datetime(
                    df_flat['stat_date']).dt.date

                df_rec = pd.DataFrame()
                df_rec['stat_date'] = df_flat['stat_date']

                # helper to safely extract column values or fillna
                def _col(name):
                    return df_flat[name] if name in df_flat.columns else pd.Series([np.nan] * len(df_flat))

                # temperature
                df_rec['temp_count'] = pd.to_numeric(
                    _col('temperature_count'), errors='coerce').fillna(0).astype(int)
                df_rec['temp_min'] = pd.to_numeric(
                    _col('temperature_min'), errors='coerce')
                df_rec['temp_max'] = pd.to_numeric(
                    _col('temperature_max'), errors='coerce')
                df_rec['temp_median'] = pd.to_numeric(
                    _col('temperature_median'), errors='coerce')
                df_rec['temp_q25'] = pd.to_numeric(
                    _col('temperature_q25'), errors='coerce')
                df_rec['temp_q75'] = pd.to_numeric(
                    _col('temperature_q75'), errors='coerce')

                # humidity
                df_rec['humidity_count'] = pd.to_numeric(
                    _col('humidity_count'), errors='coerce').fillna(0).astype(int)
                df_rec['humidity_min'] = pd.to_numeric(
                    _col('humidity_min'), errors='coerce')
                df_rec['humidity_max'] = pd.to_numeric(
                    _col('humidity_max'), errors='coerce')
                df_rec['humidity_median'] = pd.to_numeric(
                    _col('humidity_median'), errors='coerce')
                df_rec['humidity_q25'] = pd.to_numeric(
                    _col('humidity_q25'), errors='coerce')
                df_rec['humidity_q75'] = pd.to_numeric(
                    _col('humidity_q75'), errors='coerce')

                # co2
                df_rec['co2_count'] = pd.to_numeric(
                    _col('co2_count'), errors='coerce').fillna(0).astype(int)
                df_rec['co2_min'] = pd.to_numeric(
                    _col('co2_min'), errors='coerce')
                df_rec['co2_max'] = pd.to_numeric(
                    _col('co2_max'), errors='coerce')
                df_rec['co2_median'] = pd.to_numeric(
                    _col('co2_median'), errors='coerce')
                df_rec['co2_q25'] = pd.to_numeric(
                    _col('co2_q25'), errors='coerce')
                df_rec['co2_q75'] = pd.to_numeric(
                    _col('co2_q75'), errors='coerce')

                # attach room_id
                df_rec['room_id'] = room

                # merge mushroom info if present
                if m_info_pivot is not None and not m_info_pivot.empty:
                    mdf = m_info_pivot.reset_index()
                    mdf['stat_date'] = pd.to_datetime(mdf['stat_date']).dt.date
                    # keep only relevant columns
                    keep_cols = [c for c in (
                        'stat_date', 'in_day_num', 'in_year', 'in_month', 'in_day') if c in mdf.columns]
                    if keep_cols:
                        df_rec = df_rec.merge(
                            mdf[keep_cols], on='stat_date', how='left')
                        # normalize in_day_num
                        if 'in_day_num' in df_rec.columns:
                            df_rec['in_day_num'] = pd.to_numeric(
                                df_rec['in_day_num'], errors='coerce')
                            df_rec['is_growth_phase'] = ((df_rec['in_day_num'] >= 1) & (
                                df_rec['in_day_num'] <= 27)).astype(int).fillna(0)
                        else:
                            df_rec['in_day_num'] = None
                            df_rec['is_growth_phase'] = 0

                        # build batch_date
                        if set(('in_year', 'in_month', 'in_day')).issubset(df_rec.columns):
                            try:
                                df_rec['batch_date'] = pd.to_datetime(
                                    dict(year=df_rec['in_year'].astype(float).astype('Int64'),
                                         month=df_rec['in_month'].astype(
                                             float).astype('Int64'),
                                         day=df_rec['in_day'].astype(float).astype('Int64')),
                                    errors='coerce'
                                ).dt.date
                            except Exception:
                                df_rec['batch_date'] = None
                        else:
                            df_rec['batch_date'] = None
                else:
                    df_rec['in_day_num'] = None
                    df_rec['is_growth_phase'] = 0
                    df_rec['batch_date'] = None

                # finalize records and append
                # normalize is_growth_phase to Python bool or None to match DB boolean column
                if 'is_growth_phase' in df_rec.columns:
                    def _to_bool(v):
                        try:
                            if pd.isna(v):
                                return None
                            iv = int(v)
                            return True if iv == 1 else False
                        except Exception:
                            return None
                    df_rec['is_growth_phase'] = df_rec['is_growth_phase'].apply(
                        _to_bool)

                df_rec = df_rec.replace({np.nan: None})
                # ensure correct column order and names as expected by DB
                out_cols = [
                    'room_id', 'stat_date', 'in_day_num', 'is_growth_phase',
                    'temp_median', 'temp_min', 'temp_max', 'temp_q25', 'temp_q75', 'temp_count',
                    'humidity_median', 'humidity_min', 'humidity_max', 'humidity_q25', 'humidity_q75', 'humidity_count',
                    'co2_median', 'co2_min', 'co2_max', 'co2_q25', 'co2_q75', 'co2_count',
                    'batch_date'
                ]
                # map existing df_rec to out_cols, adding absent cols with None
                records_df = pd.DataFrame()
                for c in out_cols:
                    records_df[c] = df_rec[c] if c in df_rec.columns else None

                results.extend(records_df.to_dict(orient='records'))
            except Exception as e:
                logger.error(f"Failed to compute stats for room {room}: {e}")
                continue

        # After processing all rooms/days, write batch to DB using to_sql
        if not results:
            logger.info("No daily stats to write.")
            return

        try:
            df_out = pd.DataFrame(results)
            # Ensure stat_date is date/datetime
            if 'stat_date' in df_out.columns:
                df_out['stat_date'] = pd.to_datetime(
                    df_out['stat_date']).dt.date

            # Bulk insert via to_sql — 不再删除已有记录，直接按批次追加。
            # 后续若存在多条相同 (room_id, stat_date) 的记录，可以通过索引或筛选最新记录来选择。
            try:
                df_out.to_sql('mushroom_env_daily_stats', con=pgsql_engine,
                              if_exists='append', index=False, method='multi')
                logger.info(
                    f"Batch appended {len(df_out)} daily-stats rows to mushroom_env_daily_stats")
            except Exception as e:
                logger.error(f"Failed to batch write daily stats: {e}")
        except Exception as e:
            logger.error(f"Failed to batch write daily stats: {e}")


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
        print(f"   补光数量: {env_data['light_count']}")
    else:
        print("❌ 环境数据获取失败")