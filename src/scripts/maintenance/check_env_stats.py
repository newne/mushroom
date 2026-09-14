#!/usr/bin/env python3
"""
检查环境统计数据
查看计算结果
"""

import sys
from pathlib import Path
import pandas as pd
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.paths import ensure_src_path
ensure_src_path()

from global_const.global_const import pgsql_engine
from utils.loguru_setting import logger

def check_env_stats():
    """检查环境统计数据"""
    
    try:
        # 查询统计数据
        query = """
        SELECT 
            room_id,
            stat_date,
            temp_median,
            humidity_median,
            co2_median,
            temp_count,
            humidity_count,
            co2_count,
            in_day_num,
            is_growth_phase
        FROM mushroom_env_daily_stats 
        WHERE stat_date >= '2024-12-19'
        ORDER BY room_id, stat_date
        """
        
        print("🔍 查询环境统计数据...")
        df = pd.read_sql(query, pgsql_engine)
        
        if df.empty:
            print("❌ 没有找到环境统计数据")
            return
        
        print(f"✅ 找到 {len(df)} 条环境统计记录")
        
        # 按房间统计
        room_stats = df.groupby('room_id').agg({
            'stat_date': ['count', 'min', 'max'],
            'temp_median': 'mean',
            'humidity_median': 'mean',
            'co2_median': 'mean'
        }).round(2)
        
        print("\n📊 按房间统计:")
        print("房间 | 记录数 | 开始日期 | 结束日期 | 平均温度 | 平均湿度 | 平均CO2")
        print("-" * 80)
        
        for room_id in room_stats.index:
            count = room_stats.loc[room_id, ('stat_date', 'count')]
            min_date = room_stats.loc[room_id, ('stat_date', 'min')]
            max_date = room_stats.loc[room_id, ('stat_date', 'max')]
            avg_temp = room_stats.loc[room_id, ('temp_median', 'mean')]
            avg_humidity = room_stats.loc[room_id, ('humidity_median', 'mean')]
            avg_co2 = room_stats.loc[room_id, ('co2_median', 'mean')]
            
            print(f"{room_id:4} | {count:6} | {min_date} | {max_date} | {avg_temp:8.1f} | {avg_humidity:8.1f} | {avg_co2:8.1f}")
        
        # 显示最近几天的数据
        print("\n📅 最近5天的数据:")
        recent_data = df.sort_values(['stat_date', 'room_id']).tail(20)
        
        print("日期       | 房间 | 温度  | 湿度  | CO2   | 温度记录数 | 湿度记录数 | CO2记录数")
        print("-" * 85)
        
        for _, row in recent_data.iterrows():
            print(f"{row['stat_date']} | {row['room_id']:4} | {row['temp_median']:5.1f} | {row['humidity_median']:5.1f} | {row['co2_median']:5.0f} | {row['temp_count']:8} | {row['humidity_count']:8} | {row['co2_count']:7}")
        
        # 检查数据完整性
        print("\n🔍 数据完整性检查:")
        
        # 检查每个房间的日期连续性
        for room_id in df['room_id'].unique():
            room_data = df[df['room_id'] == room_id].sort_values('stat_date')
            date_range = pd.date_range(
                start=room_data['stat_date'].min(),
                end=room_data['stat_date'].max(),
                freq='D'
            )
            
            missing_dates = set(date_range.date) - set(room_data['stat_date'])
            if missing_dates:
                print(f"⚠️  房间 {room_id} 缺少 {len(missing_dates)} 天的数据")
            else:
                print(f"✅ 房间 {room_id} 数据完整")
        
        print(f"\n🎉 环境统计数据检查完成！")
        
    except Exception as e:
        print(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()

def main():
    """主函数"""
    print("=== 环境统计数据检查工具 ===")
    check_env_stats()

if __name__ == "__main__":
    main()