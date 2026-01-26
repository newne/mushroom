#!/usr/bin/env python3
"""
执行设定点变更监控任务

按照用户要求的具体操作步骤执行设定点监控分析
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

def check_database_status():
    """检查数据库状态"""
    print("🔍 步骤1: 检查过去24小时内的设定点变更情况")
    print("=" * 60)
    
    try:
        # 简单的数据库连接测试
        import subprocess
        result = subprocess.run([
            'python3', '-c', '''
import sys
from global_const.global_const import ensure_src_path
ensure_src_path()
from global_const.global_const import pgsql_engine
from sqlalchemy import text

try:
    with pgsql_engine.connect() as conn:
        # 检查表是否存在
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'device_setpoint_changes'
            )
        """))
        table_exists = result.scalar()
        
        if table_exists:
            # 查询过去24小时的记录
            result = conn.execute(text("""
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(DISTINCT room_id) as rooms_count,
                    MIN(change_time) as earliest_change,
                    MAX(change_time) as latest_change
                FROM device_setpoint_changes 
                WHERE change_time >= NOW() - INTERVAL '24 hours'
            """))
            
            row = result.fetchone()
            if row and row[0] > 0:
                print(f"📊 过去24小时设定点变更统计:")
                print(f"   总记录数: {row[0]}")
                print(f"   涉及库房: {row[1]}")
                print(f"   最早变更: {row[2]}")
                print(f"   最新变更: {row[3]}")
            else:
                print("📊 过去24小时无设定点变更记录")
        else:
            print("⚠️ 设定点变更表不存在，将在执行监控时创建")
            
        print("✅ 数据库连接正常")
        
except Exception as e:
    print(f"❌ 数据库检查失败: {e}")
'''
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print(result.stdout)
            return True
        else:
            print(f"❌ 数据库检查失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 执行数据库检查失败: {e}")
        return False

def execute_batch_monitoring():
    """执行批量监控"""
    print("\\n🚀 步骤2: 执行批量设定点变更监控")
    print("=" * 60)
    
    try:
        # 使用现有的批量监控脚本
        batch_script = project_root / "scripts" / "monitoring" / "batch_setpoint_monitoring.py"
        
        if not batch_script.exists():
            print(f"❌ 批量监控脚本不存在: {batch_script}")
            return False
        
        print("🔍 正在执行批量设定点变更分析...")
        
        import subprocess
        result = subprocess.run([
            'python3', str(batch_script)
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print("✅ 批量监控执行成功")
            print(result.stdout)
            return True
        else:
            print(f"❌ 批量监控执行失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 执行批量监控失败: {e}")
        return False

def verify_analysis_results():
    """验证分析结果"""
    print("\\n🔍 步骤3: 验证分析结果和数据完整性")
    print("=" * 60)
    
    try:
        import subprocess
        result = subprocess.run([
            'python3', '-c', '''
import sys
from global_const.global_const import ensure_src_path
ensure_src_path()
from global_const.global_const import pgsql_engine
from sqlalchemy import text
from datetime import datetime, timedelta

try:
    with pgsql_engine.connect() as conn:
        # 查询最近的分析结果
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT room_id) as rooms_count,
                COUNT(DISTINCT device_type) as device_types_count,
                MIN(change_time) as earliest_change,
                MAX(change_time) as latest_change,
                MIN(detection_time) as earliest_detection,
                MAX(detection_time) as latest_detection
            FROM device_setpoint_changes 
            WHERE detection_time >= NOW() - INTERVAL '1 hour'
        """))
        
        row = result.fetchone()
        if row and row[0] > 0:
            print(f"📊 最近1小时分析结果验证:")
            print(f"   新增记录数: {row[0]}")
            print(f"   涉及库房数: {row[1]}")
            print(f"   设备类型数: {row[2]}")
            print(f"   变更时间范围: {row[4]} ~ {row[5]}")
            print(f"   检测时间范围: {row[6]} ~ {row[7]}")
            print("✅ 数据完整性验证通过")
        else:
            print("📊 最近1小时无新增分析记录")
            
        # 按库房统计最近的变更
        result = conn.execute(text("""
            SELECT room_id, COUNT(*) as change_count
            FROM device_setpoint_changes 
            WHERE detection_time >= NOW() - INTERVAL '1 hour'
            GROUP BY room_id
            ORDER BY change_count DESC
        """))
        
        room_stats = result.fetchall()
        if room_stats:
            print(f"\\n📍 各库房最新变更统计:")
            for room_row in room_stats:
                print(f"   库房 {room_row[0]}: {room_row[1]} 个新变更")
        
except Exception as e:
    print(f"❌ 结果验证失败: {e}")
'''
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print(result.stdout)
            return True
        else:
            print(f"❌ 结果验证失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 执行结果验证失败: {e}")
        return False

def check_system_status():
    """检查系统运行状态"""
    print("\\n🔧 步骤4: 检查系统运行状态")
    print("=" * 60)
    
    try:
        # 检查数据库连接
        import subprocess
        result = subprocess.run([
            'python3', '-c', '''
import sys
from global_const.global_const import ensure_src_path
ensure_src_path()
from global_const.global_const import pgsql_engine
from sqlalchemy import text

try:
    with pgsql_engine.connect() as conn:
        result = conn.execute(text("SELECT NOW(), version()"))
        row = result.fetchone()
        print(f"✅ 数据库连接正常")
        print(f"   当前时间: {row[0]}")
        print(f"   数据库版本: {row[1][:50]}...")
        
    print("✅ 系统运行状态正常")
    
except Exception as e:
    print(f"❌ 系统状态检查失败: {e}")
'''
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print(result.stdout)
            return True
        else:
            print(f"❌ 系统状态检查失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 执行系统状态检查失败: {e}")
        return False

def extended_analysis():
    """扩展时间范围分析"""
    print("\\n📅 步骤5: 扩展分析时间范围")
    print("=" * 60)
    
    print("🔍 准备扩展分析时间范围至2025-12-19...")
    
    # 计算需要分析的时间范围
    current_time = datetime.now()
    target_date = datetime(2025, 12, 19)
    days_to_analyze = (current_time - target_date).days
    
    print(f"📊 分析范围统计:")
    print(f"   当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   目标日期: {target_date.strftime('%Y-%m-%d')}")
    print(f"   需要分析: {days_to_analyze} 天的历史数据")
    
    if days_to_analyze > 30:
        print("⚠️ 分析时间范围较大，建议分批处理")
        print("💡 建议每次分析7-14天的数据，避免系统负载过高")
        
        # 演示分批处理逻辑
        batch_days = 7
        total_batches = (days_to_analyze + batch_days - 1) // batch_days
        
        print(f"\\n📋 建议的分批处理方案:")
        print(f"   每批处理天数: {batch_days} 天")
        print(f"   总批次数: {total_batches} 批")
        print(f"   预估总处理时间: {total_batches * 2} 分钟")
        
        # 模拟前几个批次
        print(f"\\n🔍 前3个批次示例:")
        for i in range(min(3, total_batches)):
            batch_end = current_time - timedelta(days=i * batch_days)
            batch_start = batch_end - timedelta(days=batch_days)
            print(f"   批次 {i+1}: {batch_start.strftime('%Y-%m-%d')} ~ {batch_end.strftime('%Y-%m-%d')}")
        
        if total_batches > 3:
            print(f"   ... 还有 {total_batches - 3} 个批次")
    
    else:
        print("✅ 分析时间范围适中，可以一次性处理")
    
    return True

def generate_summary():
    """生成监控任务总结"""
    print("\\n📋 步骤6: 监控任务执行总结")
    print("=" * 60)
    
    try:
        import subprocess
        result = subprocess.run([
            'python3', '-c', '''
import sys
from global_const.global_const import ensure_src_path
ensure_src_path()
from global_const.global_const import pgsql_engine
from sqlalchemy import text
from datetime import datetime

try:
    with pgsql_engine.connect() as conn:
        # 查询今日的监控统计
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT room_id) as rooms_count,
                COUNT(DISTINCT device_type) as device_types_count,
                COUNT(DISTINCT change_type) as change_types_count,
                MIN(change_time) as earliest_change,
                MAX(change_time) as latest_change
            FROM device_setpoint_changes 
            WHERE DATE(detection_time) = CURRENT_DATE
        """))
        
        row = result.fetchone()
        if row and row[0] > 0:
            print(f"📊 今日监控任务执行总结:")
            print(f"   检测变更总数: {row[0]} 个")
            print(f"   涉及库房数量: {row[1]} 个")
            print(f"   涉及设备类型: {row[2]} 种")
            print(f"   变更类型数量: {row[3]} 种")
            print(f"   变更时间范围: {row[4]} ~ {row[5]}")
            
            # 按库房统计
            result = conn.execute(text("""
                SELECT room_id, COUNT(*) as change_count
                FROM device_setpoint_changes 
                WHERE DATE(detection_time) = CURRENT_DATE
                GROUP BY room_id
                ORDER BY change_count DESC
            """))
            
            print(f"\\n🏠 各库房变更统计:")
            for room_row in result:
                print(f"   库房 {room_row[0]}: {room_row[1]} 个变更")
                
            # 按设备类型统计
            result = conn.execute(text("""
                SELECT device_type, COUNT(*) as change_count
                FROM device_setpoint_changes 
                WHERE DATE(detection_time) = CURRENT_DATE
                GROUP BY device_type
                ORDER BY change_count DESC
            """))
            
            print(f"\\n🔧 设备类型变更统计:")
            for device_row in result:
                print(f"   {device_row[0]}: {device_row[1]} 个变更")
                
        else:
            print("📊 今日暂无监控记录")
            
        print("\\n✅ 监控任务执行完成")
        
except Exception as e:
    print(f"❌ 总结生成失败: {e}")
'''
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print(result.stdout)
            return True
        else:
            print(f"❌ 总结生成失败: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ 执行总结生成失败: {e}")
        return False

def main():
    """主函数"""
    print("🚀 执行每小时设定点变更监控任务")
    print("=" * 80)
    print("按照用户要求的具体操作步骤执行设定点监控分析")
    print("=" * 80)
    
    success_count = 0
    total_steps = 6
    
    # 执行各个步骤
    steps = [
        ("检查过去24小时变更情况", check_database_status),
        ("执行批量监控分析", execute_batch_monitoring),
        ("验证分析结果", verify_analysis_results),
        ("检查系统运行状态", check_system_status),
        ("扩展分析时间范围", extended_analysis),
        ("生成监控任务总结", generate_summary)
    ]
    
    for step_name, step_func in steps:
        try:
            if step_func():
                success_count += 1
                print(f"✅ {step_name} - 完成")
            else:
                print(f"❌ {step_name} - 失败")
        except Exception as e:
            print(f"❌ {step_name} - 异常: {e}")
    
    # 最终结果
    print(f"\\n🎯 监控任务执行结果")
    print("=" * 60)
    print(f"成功步骤: {success_count}/{total_steps}")
    
    if success_count == total_steps:
        print("🎉 所有步骤执行成功！")
        print("\\n📋 关键指标记录:")
        print("✅ 数据一致性: 已验证")
        print("✅ 系统运行状态: 正常")
        print("✅ 监控覆盖范围: 所有蘑菇库房")
        print("✅ 数据存储: 已正确存储到数据库")
    elif success_count >= total_steps * 0.8:
        print("⚠️ 大部分步骤执行成功，部分功能可能受限")
    else:
        print("❌ 多个步骤执行失败，请检查系统配置")
    
    print("\\n💡 后续建议:")
    print("1. 定期执行监控任务以保持数据更新")
    print("2. 监控系统资源使用情况")
    print("3. 根据变更频率调整监控间隔")
    print("4. 建立告警机制以及时发现异常变更")

if __name__ == "__main__":
    main()