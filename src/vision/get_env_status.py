import json
from datetime import date
from datetime import datetime
from typing import List, Dict, Any, Tuple

import numpy as np
import pandas as pd

from utils.data_preprocessing import (
    query_data_by_batch_time,
)
# 按设备类型获取配置
from utils.dataframe_utils import get_all_device_configs


def parse_iot_dataframe_to_records(
        df: pd.DataFrame,
        image_paths: Dict[Tuple[pd.Timestamp, str], str],
        collection_times: pd.Series
) -> List[Dict[str, Any]]:
    """
    高效将 IoT DataFrame 转换为结构化记录列表。
    修复了 fillna downcasting 的 warning。
    """
    rooms = ["607", "608", "611", "612"]
    all_records = []

    for room_id in rooms:
        # 1. 基础信息提取
        info_df = pd.DataFrame({
            'in_year': df.get((room_id, 'mushroom_info', 'in_year')),
            'in_month': df.get((room_id, 'mushroom_info', 'in_month')),
            'in_day': df.get((room_id, 'mushroom_info', 'in_day')),
            'in_num': df.get((room_id, 'mushroom_info', 'in_num')),
            'growth_day': df.get((room_id, 'mushroom_info', 'in_day_num'))
        }, index=df.index).fillna(0).infer_objects(copy=False)  # <--- ✅ 修复点

        # 注意：growth_stage 字段已从数据库表中删除，此处不再需要计算

        # 2. 环境传感器
        env_df = pd.DataFrame({
            'temp': df.get((room_id, 'mushroom_env_status', 'temperature')),
            'hum': df.get((room_id, 'mushroom_env_status', 'humidity')),
            'co2': df.get((room_id, 'mushroom_env_status', 'co2'))
        }, index=df.index).infer_objects(copy=False)

        # 3. 设备配置
        # 冷风机
        ac_df = pd.DataFrame({
            'on_off': df.get((room_id, 'air_cooler', 'on_off')),
            'status': df.get((room_id, 'air_cooler', 'status')),
            'temp_set': df.get((room_id, 'air_cooler', 'temp_set')),
            'temp': df.get((room_id, 'air_cooler', 'temp')),
            'diff': df.get((room_id, 'air_cooler', 'temp_diffset'))
        }, index=df.index).fillna(0).infer_objects(copy=False)  # <--- ✅ 修复点

        # 新风机
        ff_df = pd.DataFrame({
            'mode': df.get((room_id, 'fresh_air_fan', 'mode')),
            'control': df.get((room_id, 'fresh_air_fan', 'control')),
            'status': df.get((room_id, 'fresh_air_fan', 'status')),
            'time_on': df.get((room_id, 'fresh_air_fan', 'on')),
            'time_off': df.get((room_id, 'fresh_air_fan', 'off')),
            'co2_on': df.get((room_id, 'fresh_air_fan', 'co2_on')),
            'co2_off': df.get((room_id, 'fresh_air_fan', 'co2_off'))
        }, index=df.index).fillna(0).infer_objects(copy=False)  # <--- ✅ 修复点

        # 补光灯
        lt_df = pd.DataFrame({
            'model': df.get((room_id, 'grow_light', 'model')),
            'status': df.get((room_id, 'grow_light', 'mode')),
            'on_mset': df.get((room_id, 'grow_light', 'on_mset')),
            'off_mset': df.get((room_id, 'grow_light', 'off_mset'))
        }, index=df.index).fillna(0).infer_objects(copy=False)  # <--- ✅ 修复点

        # 补光灯逻辑
        light_count = ((lt_df['model'] != 0) & (lt_df['status'] < 3)).astype(int)

        # 加湿器
        hu_df = pd.DataFrame({
            'l_on': df.get((room_id, 'left_humidifier', 'on')),
            'l_off': df.get((room_id, 'left_humidifier', 'off')),
            'l_status': df.get((room_id, 'left_humidifier', 'status')),
            'r_on': df.get((room_id, 'right_humidifier', 'on')),
            'r_off': df.get((room_id, 'right_humidifier', 'off')),
            'r_status': df.get((room_id, 'right_humidifier', 'status'))
        }, index=df.index).fillna(0).infer_objects(copy=False)  # <--- ✅ 修复点

        humidifier_count = ((hu_df['l_status'] != 3).astype(int) + (hu_df['r_status'] != 3).astype(int))

        # 4. 记录生成
        indices = df.index.to_list()
        times = collection_times.to_list()

        # 转为字典列表加速迭代
        ac_data = ac_df.to_dict('records')
        ff_data = ff_df.to_dict('records')
        lt_data = lt_df.to_dict('records')
        hu_data = hu_df.to_dict('records')
        env_data = env_df.to_dict('records')
        i_data = info_df.to_dict('records')

        l_counts = light_count.tolist()
        h_counts = humidifier_count.tolist()

        records = [
            {
                "room_id": room_id,
                "collection_datetime": ct,
                "image_path": image_paths.get((idx, room_id), f"/default/{room_id}_{ct.strftime('%Y%m%d_%H%M%S')}.jpg"),
                "in_date": date(i['in_year'], i['in_month'], i['in_day']) if i['in_year'] and i['in_month'] and i[
                    'in_day'] else date(1970, 1, 1),
                "in_num": i['in_num'],
                "growth_day": i['growth_day'],
                "air_cooler_config": json.dumps(ac, ensure_ascii=False),
                "fresh_fan_config": json.dumps({
                    "mode": ff['mode'], "control": ff['control'], "status": ff['status'],
                    "time_on": ff['time_on'], "time_off": ff['time_off'],
                    "co2_on": ff['co2_on'], "co2_off": ff['co2_off']
                }, ensure_ascii=False),
                "light_count": lc,
                "light_config": json.dumps(lt, ensure_ascii=False),
                "humidifier_count": hc,
                "humidifier_config": json.dumps({
                    "left": {"on": hu['l_on'], "off": hu['l_off'], "status": hu['l_status']},
                    "right": {"on": hu['r_on'], "off": hu['r_off'], "status": hu['r_status']}
                }, ensure_ascii=False),
                "env_sensor_status": json.dumps(env, ensure_ascii=False),
                "semantic_description": _build_semantic_desc(
                    temp_set=ac['temp_set'],
                    light_count=lc, light_on=lt['on_mset'], light_off=lt['off_mset'],
                    left_status=hu['l_status'], left_on=hu['l_on'], left_off=hu['l_off'],
                    right_status=hu['r_status'], right_on=hu['r_on'], right_off=hu['r_off']
                )
            }
            for idx, ct, i, ac, ff, lt, hu, env, lc, hc in zip(
                indices, times, i_data, ac_data, ff_data, lt_data, hu_data, env_data, l_counts, h_counts
            )
        ]
        all_records.extend(records)

    return all_records


def _build_semantic_desc(
        temp_set: float, light_count: int, light_on: int, light_off: int,
        left_status: int, left_on: int, left_off: int,
        right_status: int, right_on: int, right_off: int
) -> str:
    """构建语义描述文本"""
    parts = []
    if temp_set > 0:
        parts.append(f"温控设定：{temp_set:.1f}℃")

    if light_count == 1:
        parts.append(f"补光：{light_on}-{light_off}分钟")

    humid_strs = []
    if left_status != 3:
        humid_strs.append(f"左({left_on}-{left_off})")
    if right_status != 3:
        humid_strs.append(f"右({right_on}-{right_off})")

    if humid_strs:
        parts.append(f"加湿：{', '.join(humid_strs)}")

    return "。".join(parts) + "。" if parts else "无环境控制策略。"


all_query_list=get_all_device_configs()
all_query_df=pd.concat(all_query_list.values())
start_time=datetime(2025,12,30,16,1)
end_time=datetime(2025,12,30,16,3)
df = all_query_df.groupby("device_alias").apply(query_data_by_batch_time, start_time, end_time,include_groups=True).reset_index().sort_values("time")
df['room']=df['device_name'].apply(lambda x:x.split('_')[-1])
df1=df.pivot_table(index='time',columns=['room','device_name','point_name'],values='value')
res=parse_iot_dataframe_to_records(df1, {}, df1.index)
print(res)
