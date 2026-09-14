#!/usr/bin/env python3
"""
检查12月份的环境统计数据
"""

import sys
from pathlib import Path
import pandas as pd

# 使用BASE_DIR统一管理路径
from global_const.paths import ensure_src_path
ensure_src_path()

from global_const.global_const import pgsql_engine

def check_december_data():
    """检查12月份的数据"""
    
    try:
        # 查询12月份的数据
        query = """
        SELECT 
            room_id,
            stat_date,
            temp_median,
            humidity_median,
            co2_median,
            temp_count,
            humidity_count,
            co2_count
        FROM mushroom_env_daily_stats 
        WHERE stat_date >= '2024-12-19' AND stat_date < '2025-01-01'
        ORDER BY stat_date, room_id
        """
        
        print("🔍 查询2024年12月19日以后的数据...")
        df = pd.read_sql(query, pgsql_engine)
        
        if df.empty:
            print("❌ 没有找到2024年12月的数据")
            
            # 查询所有数据的日期范围
            all_query = """
            SELECT 
                MIN(stat_date) as min_date,
                MAX(stat_date) as max_date,
                COUNT(*) as total_records
            FROM mushroom_env_daily_stats
            """
            
            all_df = pd.read_sql(all_query, pgsql_engine)
            if not all_df.empty:
                print(f"📅 数据库中的数据日期范围: {all_df.iloc[0]['min_date']} 到 {all_df.iloc[0]['max_date']}")
                print(f"📊 总记录数: {all_df.iloc[0]['total_records']}")
            
            return
        
        print(f"✅ 找到 {len(df)} 条2024年12月的记录")
        
        # 显示12月份的数据
        print("\n📅 2024年12月19日以后的数据:")
        print("日期       | 房间 | 温度  | 湿度  | CO2   | 温度记录数 | 湿度记录数 | CO2记录数")
        print("-" * 85)
        
        for _, row in df.iterrows():
            print(f"{row['stat_date']} | {row['room_id']:4} | {row['temp_median']:5.1f} | {row['humidity_median']:5.1f} | {row['co2_median']:5.0f} | {row['temp_count']:8} | {row['humidity_count']:8} | {row['co2_count']:7}")
        
        # 按日期统计
        date_stats = df.groupby('stat_date').size()
        print(f"\n📊 按日期统计 (每天应该有4个房间的记录):")
        for date, count in date_stats.items():
            status = "✅" if count == 4 else "⚠️"
            print(f"{status} {date}: {count} 条记录")
        
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()

def main():
    """主函数"""
    print("=== 2024年12月环境数据检查 ===")
    check_december_data()

if __name__ == "__main__":
    main()