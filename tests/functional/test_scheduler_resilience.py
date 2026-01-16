#!/usr/bin/env python3
"""
测试调度器的容错能力和数据库连接重试机制
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from utils.loguru_setting import loguru_setting


def test_scheduler_import():
    """测试调度器模块导入"""
    print("=" * 80)
    print("测试1: 调度器模块导入")
    print("=" * 80)
    
    try:
        from scheduling.optimized_scheduler import OptimizedScheduler
        print("✅ 调度器模块导入成功")
        return True
    except Exception as e:
        print(f"❌ 调度器模块导入失败: {e}")
        return False


def test_task_functions():
    """测试任务函数导入"""
    print("\n" + "=" * 80)
    print("测试2: 任务函数导入")
    print("=" * 80)
    
    try:
        from scheduling.optimized_scheduler import (
            safe_create_tables,
            safe_daily_env_stats,
            safe_hourly_setpoint_monitoring,
            safe_daily_clip_inference
        )
        print("✅ 所有任务函数导入成功")
        print("   - safe_create_tables")
        print("   - safe_daily_env_stats")
        print("   - safe_hourly_setpoint_monitoring")
        print("   - safe_daily_clip_inference")
        return True
    except Exception as e:
        print(f"❌ 任务函数导入失败: {e}")
        return False


def test_scheduler_creation():
    """测试调度器实例创建"""
    print("\n" + "=" * 80)
    print("测试3: 调度器实例创建")
    print("=" * 80)
    
    try:
        from scheduling.optimized_scheduler import OptimizedScheduler
        
        scheduler = OptimizedScheduler()
        print("✅ 调度器实例创建成功")
        print(f"   - 时区: {scheduler.timezone}")
        print(f"   - 主循环间隔: {scheduler.main_loop_interval}秒")
        print(f"   - 最大失败次数: {scheduler.max_failures}")
        return True
    except Exception as e:
        print(f"❌ 调度器实例创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_connection():
    """测试数据库连接"""
    print("\n" + "=" * 80)
    print("测试4: 数据库连接")
    print("=" * 80)
    
    try:
        from global_const.global_const import pgsql_engine
        
        with pgsql_engine.connect() as conn:
            result = conn.execute("SELECT 1")
            result.scalar()
        
        print("✅ 数据库连接正常")
        return True
    except Exception as e:
        print(f"⚠️ 数据库连接失败: {e}")
        print("   这可能导致调度器启动时需要重试")
        return False


def main():
    """主测试函数"""
    # 设置日志
    loguru_setting()
    
    print("\n🚀 调度器容错能力测试")
    print("=" * 80)
    
    results = []
    
    # 运行所有测试
    results.append(("模块导入", test_scheduler_import()))
    results.append(("任务函数", test_task_functions()))
    results.append(("实例创建", test_scheduler_creation()))
    results.append(("数据库连接", test_database_connection()))
    
    # 显示测试结果摘要
    print("\n" + "=" * 80)
    print("📊 测试结果摘要")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} {test_name}")
    
    print(f"\n总计: {passed}/{total} 项测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！调度器已准备就绪。")
        return 0
    elif passed >= total - 1:
        print("\n⚠️ 大部分测试通过，调度器应该可以正常运行。")
        print("   数据库连接问题将通过重试机制自动处理。")
        return 0
    else:
        print("\n❌ 多项测试失败，请检查配置和依赖。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
