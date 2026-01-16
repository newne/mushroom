#!/usr/bin/env python3
"""
设备设定点变更监控系统演示脚本
展示完整的监控、分析和报告功能
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# 添加src目录到路径
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

from utils.setpoint_change_monitor import create_setpoint_monitor, create_setpoint_monitor_table
from utils.setpoint_analytics import create_setpoint_analytics
from utils.loguru_setting import loguru_setting
from loguru import logger


def demo_monitor_setup():
    """演示监控器设置"""
    print("🔧 设备设定点变更监控系统")
    print("=" * 60)
    
    # 创建监控器
    monitor = create_setpoint_monitor()
    
    print("📋 监控配置概览:")
    print(f"   总监控点数: {len(monitor.setpoint_configs)}")
    
    # 按设备类型统计
    device_types = {}
    for config in monitor.setpoint_configs:
        device_type = config.device_type
        device_types[device_type] = device_types.get(device_type, 0) + 1
    
    for device_type, count in device_types.items():
        print(f"   {device_type}: {count} 个监控点")
    
    print()
    return monitor


def demo_real_time_monitoring(monitor, room_id="611"):
    """演示实时监控功能"""
    print(f"🔍 实时监控演示 - 库房 {room_id}")
    print("-" * 40)
    
    # 监控过去1小时的变更
    changes = monitor.monitor_room_setpoint_changes(room_id, hours_back=1)
    
    if not changes:
        print(f"ℹ️ 库房 {room_id} 过去1小时内无设定点变更")
        
        # 尝试监控更长时间
        print("🔍 扩展监控范围到过去24小时...")
        changes = monitor.monitor_room_setpoint_changes(room_id, hours_back=24)
    
    if changes:
        print(f"✅ 检测到 {len(changes)} 个设定点变更:")
        
        # 显示最近的5个变更
        recent_changes = sorted(changes, key=lambda x: x['change_time'], reverse=True)[:5]
        
        for i, change in enumerate(recent_changes, 1):
            change_time = change['change_time'].strftime('%Y-%m-%d %H:%M:%S')
            print(f"   {i}. {change['device_name']}.{change['point_name']}")
            print(f"      变更: {change['change_detail']}")
            print(f"      时间: {change_time}")
            print(f"      类型: {change['change_type']}")
            print()
        
        # 存储变更记录
        print("💾 存储变更记录...")
        success = monitor.store_setpoint_changes(changes)
        if success:
            print("✅ 变更记录已存储到数据库")
        else:
            print("❌ 变更记录存储失败")
    else:
        print(f"ℹ️ 库房 {room_id} 过去24小时内也无设定点变更")
    
    print()
    return changes


def demo_analytics(analytics, room_id="611"):
    """演示分析功能"""
    print(f"📊 数据分析演示 - 库房 {room_id}")
    print("-" * 40)
    
    # 获取统计信息
    stats = analytics.get_change_statistics(room_id=room_id)
    
    if stats and stats.get('basic_stats', {}).get('total_changes', 0) > 0:
        basic = stats['basic_stats']
        print(f"📈 基础统计:")
        print(f"   总变更次数: {basic['total_changes']}")
        print(f"   涉及设备数: {basic['devices_count']}")
        print(f"   涉及测点数: {basic['points_count']}")
        print(f"   平均变更幅度: {basic['avg_change_magnitude']:.2f}")
        print(f"   最大变更幅度: {basic['max_change_magnitude']:.2f}")
        
        if basic['earliest_change'] and basic['latest_change']:
            print(f"   时间范围: {basic['earliest_change']} ~ {basic['latest_change']}")
        print()
        
        # 设备类型统计
        device_stats = stats.get('device_type_stats', [])
        if device_stats:
            print("🔧 按设备类型统计:")
            for stat in device_stats[:5]:
                print(f"   {stat['device_type']}: {stat['change_count']} 次变更 (平均幅度: {stat['avg_magnitude']:.2f})")
            print()
        
        # 测点统计
        point_stats = stats.get('point_stats', [])
        if point_stats:
            print("📍 变更最频繁的测点:")
            for stat in point_stats[:5]:
                print(f"   {stat['device_type']}.{stat['point_name']}: {stat['change_count']} 次")
                print(f"      描述: {stat['point_description']}")
            print()
    else:
        print("ℹ️ 暂无足够的历史数据进行分析")
        print("💡 建议运行监控一段时间后再查看分析结果")
    
    # 获取小时模式
    hourly_pattern = analytics.get_hourly_change_pattern(room_id=room_id, days_back=7)
    
    if not hourly_pattern.empty:
        print("⏰ 24小时变更模式:")
        for _, row in hourly_pattern.iterrows():
            hour = int(row['hour'])
            count = int(row['change_count'])
            print(f"   {hour:02d}:00 - {count} 次变更")
        print()
    
    print()


def demo_abnormal_detection(analytics, room_id="611"):
    """演示异常检测功能"""
    print(f"🚨 异常检测演示 - 库房 {room_id}")
    print("-" * 40)
    
    # 检测异常变更
    abnormal_changes = analytics.detect_abnormal_changes(room_id=room_id, days_back=7)
    
    if abnormal_changes:
        print(f"⚠️ 检测到 {len(abnormal_changes)} 个异常变更:")
        
        for change in abnormal_changes:
            print(f"   • 类型: {change['type']}")
            print(f"     设备: {change['device_name']} ({change['device_type']})")
            print(f"     描述: {change['description']}")
            
            if 'change_time' in change:
                print(f"     时间: {change['change_time']}")
            
            print()
    else:
        print("✅ 未检测到异常变更模式")
    
    print()


def demo_summary_report(analytics, room_id="611"):
    """演示摘要报告功能"""
    print(f"📋 摘要报告演示 - 库房 {room_id}")
    print("-" * 40)
    
    # 生成摘要报告
    report = analytics.generate_summary_report(room_id=room_id, days_back=7)
    
    if not report:
        print("❌ 报告生成失败")
        return
    
    period = report['report_period']
    summary = report['summary']
    
    print(f"📅 报告期间: {period['start_date'].strftime('%Y-%m-%d')} ~ {period['end_date'].strftime('%Y-%m-%d')} ({period['days']} 天)")
    print()
    
    print("📊 总体概况:")
    print(f"   总变更次数: {summary.get('total_changes', 0)}")
    print(f"   涉及设备数: {summary.get('devices_count', 0)}")
    print(f"   涉及测点数: {summary.get('points_count', 0)}")
    print(f"   平均变更幅度: {summary.get('avg_change_magnitude', 0):.2f}")
    print()
    
    # 活跃时段
    active_hours = report.get('active_hours', [])
    if active_hours:
        hours_str = ', '.join([f"{h:02d}:00" for h in active_hours])
        print(f"⏰ 活跃时段: {hours_str}")
        print()
    
    # 最活跃设备
    most_active = report.get('most_active_devices', [])
    if most_active:
        print("🔥 最活跃设备:")
        for device in most_active[:3]:
            print(f"   • {device['device_name']} ({device['device_type']})")
            print(f"     总变更: {device['total_changes']} 次")
            print(f"     日均变更: {device['changes_per_day']:.1f} 次/天")
        print()
    
    # 异常情况
    abnormal = report.get('abnormal_changes', [])
    if abnormal:
        print(f"⚠️ 异常变更: {len(abnormal)} 个")
        for change in abnormal[:3]:
            print(f"   • {change['description']}")
        print()
    else:
        print("✅ 无异常变更")
        print()
    
    print(f"📝 报告生成时间: {report['generated_at'].strftime('%Y-%m-%d %H:%M:%S')}")
    print()


def demo_multi_room_monitoring(monitor):
    """演示多库房监控功能"""
    print("🏢 多库房监控演示")
    print("-" * 40)
    
    # 监控所有库房
    all_changes = monitor.monitor_all_rooms_setpoint_changes(hours_back=24)
    
    total_changes = sum(len(changes) for changes in all_changes.values())
    
    if total_changes == 0:
        print("ℹ️ 所有库房在过去24小时内均无设定点变更")
        return
    
    print(f"✅ 总共检测到 {total_changes} 个设定点变更")
    print()
    
    # 按库房显示统计
    for room_id, changes in all_changes.items():
        if not changes:
            print(f"📍 库房 {room_id}: 无变更")
            continue
        
        print(f"📍 库房 {room_id}: {len(changes)} 个变更")
        
        # 按设备类型统计
        device_stats = {}
        for change in changes:
            device_type = change['device_type']
            device_stats[device_type] = device_stats.get(device_type, 0) + 1
        
        for device_type, count in device_stats.items():
            print(f"   • {device_type}: {count} 个变更")
        
        # 显示最近变更
        if changes:
            latest_change = max(changes, key=lambda x: x['change_time'])
            latest_time = latest_change['change_time'].strftime('%H:%M:%S')
            print(f"   最近变更: {latest_change['device_name']}.{latest_change['point_name']} ({latest_time})")
        
        print()


def main():
    """主演示函数"""
    # 初始化日志
    loguru_setting()
    
    print("🚀 设备设定点变更监控系统演示")
    print("=" * 60)
    print(f"⏰ 演示开始时间: {datetime.now()}")
    print()
    
    try:
        # 1. 创建数据库表
        print("🗄️ 初始化数据库表...")
        create_setpoint_monitor_table()
        print("✅ 数据库表初始化完成")
        print()
        
        # 2. 设置监控器
        monitor = demo_monitor_setup()
        
        # 3. 创建分析器
        analytics = create_setpoint_analytics()
        
        # 4. 实时监控演示
        changes = demo_real_time_monitoring(monitor, room_id="611")
        
        # 5. 多库房监控演示
        demo_multi_room_monitoring(monitor)
        
        # 6. 数据分析演示
        demo_analytics(analytics, room_id="611")
        
        # 7. 异常检测演示
        demo_abnormal_detection(analytics, room_id="611")
        
        # 8. 摘要报告演示
        demo_summary_report(analytics, room_id="611")
        
        print("🎉 演示完成！")
        print()
        print("💡 使用建议:")
        print("   1. 定期运行监控脚本收集数据")
        print("   2. 设置定时任务自动监控设定点变更")
        print("   3. 结合分析功能识别设备操作模式")
        print("   4. 关注异常变更，及时发现设备问题")
        print()
        
    except KeyboardInterrupt:
        print("\n⚠️ 演示被用户中断")
    except Exception as e:
        print(f"\n❌ 演示过程中发生错误: {e}")
        logger.error(f"Demo failed: {e}")
    
    print(f"⏰ 演示结束时间: {datetime.now()}")


if __name__ == "__main__":
    main()