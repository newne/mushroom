#!/usr/bin/env python3
"""
批量设定点变更监控脚本

使用 DeviceSetpointChangeMonitor 对所有库房在指定时间范围内的监控测点变更情况
进行批量分析和梳理，并将检测到的设定点变更结果存储到数据库中。

使用方法:
    python scripts/batch_setpoint_monitoring.py

功能特性:
1. 支持指定时间范围的批量分析
2. 自动获取所有可用库房列表  
3. 并行处理多个库房的监控任务
4. 完整的错误处理和重试机制
5. 详细的进度反馈和统计信息
6. 高效的批量数据库存储
7. 环境验证和边界条件检查
8. 兼容现有的标识符转换机制
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

# 导入必要的模块
from utils.setpoint_change_monitor import (
    create_setpoint_monitor, 
    create_setpoint_monitor_table, 
    DeviceSetpointChangeMonitor,
    batch_monitor_setpoint_changes,
    validate_batch_monitoring_environment
)
from datetime import datetime
from loguru import logger


def main():
    """主函数 - 演示批量监控功能"""
    print("🚀 批量设定点变更监控系统")
    print("=" * 60)
    
    # 1. 环境准备
    print("\n📋 步骤1: 环境准备")
    print("正在验证批量监控环境...")
    
    if not validate_batch_monitoring_environment():
        print("❌ 环境验证失败，请检查以下项目:")
        print("   - 数据库连接是否可用")
        print("   - 静态配置文件是否存在")
        print("   - Python虚拟环境是否激活")
        return False
    
    print("✅ 环境验证通过")
    
    # 2. 数据库初始化
    print("\n📋 步骤2: 数据库初始化")
    print("正在确保 device_setpoint_changes 表已创建...")
    
    try:
        create_setpoint_monitor_table()
        print("✅ 数据库表创建/验证成功")
    except Exception as e:
        print(f"❌ 数据库表创建失败: {e}")
        return False
    
    # 3. 批量监控函数实现演示
    print("\n📋 步骤3: 批量监控执行")
    
    # 设定分析时间范围
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=6)  # 分析最近6小时
    
    print(f"分析时间范围:")
    print(f"   开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   时间跨度: {(end_time - start_time).total_seconds() / 3600:.1f} 小时")
    
    try:
        # 执行批量监控
        print("\n🔍 正在执行批量监控分析...")
        result = batch_monitor_setpoint_changes(
            start_time=start_time,
            end_time=end_time,
            store_results=True
        )
        
        # 4. 结果分析和展示
        print("\n📋 步骤4: 结果分析")
        
        if result['success']:
            print("✅ 批量监控执行成功")
            
            # 基本统计
            print(f"\n📊 执行统计:")
            print(f"   处理库房数: {result['successful_rooms']}/{result['total_rooms']}")
            print(f"   检测变更数: {result['total_changes']} 个")
            print(f"   存储记录数: {result['stored_records']} 条")
            print(f"   处理耗时: {result['processing_time']:.2f} 秒")
            
            if result['error_rooms']:
                print(f"   失败库房: {result['error_rooms']}")
            
            # 按库房详细统计
            print(f"\n🏠 各库房变更详情:")
            total_rooms_with_changes = 0
            
            for room_id, change_count in result['changes_by_room'].items():
                if change_count > 0:
                    total_rooms_with_changes += 1
                    status = "🔴"
                    detail = f"{change_count} 个变更"
                else:
                    status = "🟢"
                    detail = "无变更"
                
                print(f"   {status} 库房 {room_id}: {detail}")
            
            # 汇总分析
            print(f"\n📈 汇总分析:")
            print(f"   有变更的库房: {total_rooms_with_changes}/{result['total_rooms']}")
            
            if result['total_changes'] > 0:
                avg_changes_per_room = result['total_changes'] / result['total_rooms']
                print(f"   平均每库房变更: {avg_changes_per_room:.1f} 个")
                
                processing_rate = result['total_changes'] / result['processing_time']
                print(f"   处理速度: {processing_rate:.1f} 变更/秒")
            
            # 数据质量检查
            print(f"\n🔍 数据质量检查:")
            if result['stored_records'] == result['total_changes']:
                print("✅ 所有检测到的变更都已成功存储")
            else:
                print(f"⚠️ 存储记录数({result['stored_records']})与检测数({result['total_changes']})不匹配")
            
        else:
            print("❌ 批量监控执行失败")
            return False
            
    except ValueError as e:
        print(f"❌ 参数错误: {e}")
        return False
    except Exception as e:
        print(f"❌ 执行异常: {e}")
        return False
    
    # 5. 边界条件测试
    print("\n📋 步骤5: 边界条件测试")
    
    # 测试无效时间范围
    print("测试无效时间范围...")
    try:
        batch_monitor_setpoint_changes(
            start_time=end_time,
            end_time=start_time,  # 错误的时间顺序
            store_results=False
        )
        print("❌ 应该抛出异常但没有")
    except ValueError:
        print("✅ 正确捕获无效时间范围异常")
    
    # 测试极短时间范围
    print("测试极短时间范围...")
    try:
        short_start = datetime.now() - timedelta(minutes=1)
        short_end = datetime.now()
        short_result = batch_monitor_setpoint_changes(
            start_time=short_start,
            end_time=short_end,
            store_results=False
        )
        print(f"✅ 极短时间范围测试通过: {short_result['total_changes']} 个变更")
    except Exception as e:
        print(f"⚠️ 极短时间范围测试异常: {e}")
    
    print(f"\n🎯 批量监控演示完成！")
    print("=" * 60)
    
    # 使用指南
    print(f"\n📖 使用指南:")
    print("1. 导入模块:")
    print("   from utils.setpoint_change_monitor import batch_monitor_setpoint_changes")
    print("   from datetime import datetime, timedelta")
    print("")
    print("2. 基本用法:")
    print("   start_time = datetime(2026, 1, 13, 8, 0, 0)")
    print("   end_time = datetime(2026, 1, 13, 18, 0, 0)")
    print("   result = batch_monitor_setpoint_changes(start_time, end_time)")
    print("")
    print("3. 结果字段:")
    print("   - success: 执行是否成功")
    print("   - total_rooms: 处理的库房总数")
    print("   - total_changes: 检测到的变更总数")
    print("   - changes_by_room: 按库房分组的变更统计")
    print("   - processing_time: 处理耗时")
    print("   - stored_records: 存储的记录数")
    
    return True


def demo_custom_time_range():
    """演示自定义时间范围的批量监控"""
    print("\n🎯 自定义时间范围演示")
    print("-" * 40)
    
    # 示例1: 分析昨天全天的数据
    yesterday = datetime.now() - timedelta(days=1)
    start_time = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
    
    print(f"示例1: 分析昨天全天数据")
    print(f"时间范围: {start_time} ~ {end_time}")
    
    try:
        result = batch_monitor_setpoint_changes(
            start_time=start_time,
            end_time=end_time,
            store_results=False  # 演示模式，不存储
        )
        
        print(f"结果: 检测到 {result['total_changes']} 个变更")
        
    except Exception as e:
        print(f"执行失败: {e}")
    
    # 示例2: 分析特定时间段
    specific_start = datetime(2026, 1, 13, 9, 0, 0)
    specific_end = datetime(2026, 1, 13, 17, 0, 0)
    
    print(f"\n示例2: 分析工作时间段")
    print(f"时间范围: {specific_start} ~ {specific_end}")
    
    try:
        result = batch_monitor_setpoint_changes(
            start_time=specific_start,
            end_time=specific_end,
            store_results=False
        )
        
        print(f"结果: 检测到 {result['total_changes']} 个变更")
        
    except Exception as e:
        print(f"执行失败: {e}")


if __name__ == "__main__":
    try:
        # 执行主演示
        success = main()
        
        if success:
            # 执行自定义时间范围演示
            demo_custom_time_range()
            
            print(f"\n🎉 所有演示完成！")
        else:
            print(f"\n❌ 演示执行失败")
            
    except KeyboardInterrupt:
        print(f"\n⚠️ 用户中断执行")
    except Exception as e:
        print(f"\n❌ 未预期的错误: {e}")
        import traceback
        traceback.print_exc()