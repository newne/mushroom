#!/usr/bin/env python3
"""
最新批次环境数据可视化脚本

基于 MushroomEnvDailyStats 表数据，生成类似于 visualization.py 的可视化图表。
包含温度、湿度、CO2浓度的分布情况，使用 Violin 图展示每日环境数据的分布特征。
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from utils.daily_stats_visualization import (
    analyze_and_visualize_latest_batch,
    get_latest_batch_data,
    plot_room_daily_stats_violin,
    plot_multi_room_comparison
)
from utils.loguru_setting import loguru_setting


def main():
    """主函数"""
    # 设置日志
    loguru_setting()
    
    print("=" * 60)
    print("最新批次环境数据可视化")
    print("=" * 60)
    
    # 方案1: 完整分析（推荐）
    print("\n1. 执行完整的最新批次数据分析...")
    results = analyze_and_visualize_latest_batch(
        rooms=None,  # 分析所有库房
        days_back=45,  # 查询最近45天的数据
        show_individual=True,  # 显示单个库房详细图表
        show_comparison=True,  # 显示多库房对比图表
        return_figs=True  # 返回图表对象以便进一步处理
    )
    
    if results:
        print(f"\n✅ 分析完成！")
        print(f"   - 总记录数: {results['summary']['total_records']}")
        print(f"   - 涉及库房: {results['summary']['rooms']}")
        print(f"   - 数据时间范围: {results['summary']['date_range']['start']} 到 {results['summary']['date_range']['end']}")
        
        # 显示批次信息
        if 'batch_info' in results['summary']:
            print(f"\n📊 批次信息:")
            for room, info in results['summary']['batch_info'].items():
                print(f"   库房 {room}: {info['nunique']} 个批次, 时间范围 {info['min']} 到 {info['max']}")
        
        # 显示生成的图表信息
        print(f"\n📈 生成的图表:")
        print(f"   - 单库房详细图表: {len(results['individual_figs'])} 个")
        print(f"   - 多库房对比图表: {len(results['comparison_figs'])} 个")
        
        # 列出对比图表类型
        if results['comparison_figs']:
            print(f"   对比图表类型: {list(results['comparison_figs'].keys())}")
    
    else:
        print("❌ 未找到有效数据或分析失败")
        return
    
    # 方案2: 指定库房分析
    print(f"\n2. 针对特定库房进行详细分析...")
    specific_rooms = ['611', '612']  # 可以根据实际情况修改
    
    # 查询特定库房数据
    df_specific = get_latest_batch_data(rooms=specific_rooms, days_back=30)
    
    if not df_specific.empty:
        print(f"   查询到库房 {specific_rooms} 的 {len(df_specific)} 条记录")
        
        # 为每个库房生成详细图表
        for room in specific_rooms:
            room_data = df_specific[df_specific['room_id'] == room]
            if not room_data.empty:
                print(f"   正在生成库房 {room} 的详细图表...")
                fig = plot_room_daily_stats_violin(room_data, room, show=True)
                print(f"   ✅ 库房 {room} 图表生成完成")
        
        # 生成对比图表
        if len(df_specific['room_id'].unique()) > 1:
            print(f"   正在生成多库房对比图表...")
            comparison_figs = plot_multi_room_comparison(df_specific, rooms=specific_rooms, show=True)
            print(f"   ✅ 对比图表生成完成，包含 {len(comparison_figs)} 个图表")
    
    else:
        print(f"   ❌ 未找到库房 {specific_rooms} 的有效数据")
    
    # 方案3: 数据质量检查
    print(f"\n3. 数据质量检查...")
    all_data = get_latest_batch_data(days_back=60)
    
    if not all_data.empty:
        print(f"   总数据量: {len(all_data)} 条记录")
        print(f"   涉及库房: {sorted(all_data['room_id'].unique())}")
        print(f"   时间跨度: {all_data['stat_date'].min()} 到 {all_data['stat_date'].max()}")
        
        # 检查数据完整性
        print(f"\n   数据完整性检查:")
        for param in ['temp', 'humidity', 'co2']:
            median_col = f'{param}_median'
            if median_col in all_data.columns:
                non_null_count = all_data[median_col].notna().sum()
                total_count = len(all_data)
                completeness = (non_null_count / total_count) * 100
                print(f"   - {param.title()}: {non_null_count}/{total_count} ({completeness:.1f}%)")
        
        # 检查生长阶段数据
        if 'is_growth_phase' in all_data.columns:
            growth_records = all_data['is_growth_phase'].sum()
            total_records = len(all_data)
            growth_percentage = (growth_records / total_records) * 100
            print(f"   - 生长阶段记录: {growth_records}/{total_records} ({growth_percentage:.1f}%)")
        
        # 检查批次信息
        if 'batch_date' in all_data.columns:
            batch_records = all_data['batch_date'].notna().sum()
            unique_batches = all_data['batch_date'].nunique()
            print(f"   - 批次信息: {batch_records} 条记录, {unique_batches} 个不同批次")
    
    print(f"\n" + "=" * 60)
    print("可视化脚本执行完成")
    print("=" * 60)


if __name__ == "__main__":
    main()