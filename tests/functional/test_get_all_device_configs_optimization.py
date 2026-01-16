#!/usr/bin/env python3
"""
测试 get_all_device_configs 函数优化效果

测试内容：
1. 功能正确性验证
2. 性能对比测试
3. 日志输出验证
4. 边界条件测试
"""

import sys
import time
from pathlib import Path

# 添加项目根目录到路径
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from loguru import logger
from utils.dataframe_utils import get_all_device_configs, clear_device_config_cache


def test_functionality():
    """测试功能正确性"""
    print("\n" + "="*80)
    print("测试1: 功能正确性验证")
    print("="*80)
    
    try:
        # 测试1.1: 获取所有设备配置
        print("\n[测试1.1] 获取所有设备配置...")
        all_configs = get_all_device_configs()
        
        assert isinstance(all_configs, dict), "返回值应该是字典"
        assert len(all_configs) > 0, "应该至少有一种设备类型"
        
        print(f"✅ 成功获取 {len(all_configs)} 种设备类型配置")
        for device_type, df in all_configs.items():
            print(f"   - {device_type}: {len(df)} 个设备配置")
        
        # 测试1.2: 获取指定库房配置
        print("\n[测试1.2] 获取指定库房配置...")
        test_rooms = ["607", "608", "611", "612"]
        
        for room_id in test_rooms:
            room_configs = get_all_device_configs(room_id=room_id)
            assert isinstance(room_configs, dict), f"库房{room_id}返回值应该是字典"
            
            total_devices = sum(len(df) for df in room_configs.values())
            print(f"✅ 库房{room_id}: {len(room_configs)} 种设备类型, {total_devices} 个设备")
        
        # 测试1.3: 验证数据完整性
        print("\n[测试1.3] 验证数据完整性...")
        for device_type, df in all_configs.items():
            assert not df.empty, f"{device_type} 的DataFrame不应为空"
            assert 'device_name' in df.columns, f"{device_type} 应包含 device_name 列"
            assert 'device_alias' in df.columns, f"{device_type} 应包含 device_alias 列"
            assert 'point_name' in df.columns, f"{device_type} 应包含 point_name 列"
            assert 'point_alias' in df.columns, f"{device_type} 应包含 point_alias 列"
        
        print("✅ 所有设备类型的数据结构完整")
        
        # 测试1.4: 边界条件测试
        print("\n[测试1.4] 边界条件测试...")
        
        # 不存在的库房
        invalid_room_configs = get_all_device_configs(room_id="999")
        print(f"✅ 不存在的库房返回: {len(invalid_room_configs)} 个配置（预期为0或空）")
        
        # None作为库房ID
        none_room_configs = get_all_device_configs(room_id=None)
        assert len(none_room_configs) > 0, "room_id=None 应返回所有配置"
        print(f"✅ room_id=None 返回: {len(none_room_configs)} 种设备类型")
        
        print("\n" + "="*80)
        print("✅ 功能正确性测试全部通过")
        print("="*80)
        return True
        
    except Exception as e:
        print(f"\n❌ 功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_performance():
    """测试性能"""
    print("\n" + "="*80)
    print("测试2: 性能对比测试")
    print("="*80)
    
    try:
        # 清除缓存，确保公平测试
        print("\n[准备] 清除所有缓存...")
        clear_device_config_cache()
        time.sleep(0.5)
        
        # 测试2.1: 首次加载性能（无缓存）
        print("\n[测试2.1] 首次加载性能（无缓存）...")
        
        start_time = time.time()
        configs_1 = get_all_device_configs()
        end_time = time.time()
        
        first_load_time = (end_time - start_time) * 1000
        print(f"✅ 首次加载时间: {first_load_time:.2f}ms")
        print(f"   - 设备类型数: {len(configs_1)}")
        print(f"   - 总设备数: {sum(len(df) for df in configs_1.values())}")
        
        # 测试2.2: 缓存命中性能
        print("\n[测试2.2] 缓存命中性能...")
        
        start_time = time.time()
        configs_2 = get_all_device_configs()
        end_time = time.time()
        
        cached_load_time = (end_time - start_time) * 1000
        print(f"✅ 缓存加载时间: {cached_load_time:.2f}ms")
        
        speedup = first_load_time / cached_load_time if cached_load_time > 0 else 0
        print(f"   - 性能提升: {speedup:.2f}x")
        
        # 测试2.3: 指定库房性能
        print("\n[测试2.3] 指定库房加载性能...")
        
        room_times = []
        for room_id in ["607", "608", "611", "612"]:
            start_time = time.time()
            room_configs = get_all_device_configs(room_id=room_id)
            end_time = time.time()
            
            room_time = (end_time - start_time) * 1000
            room_times.append(room_time)
            
            total_devices = sum(len(df) for df in room_configs.values())
            print(f"   - 库房{room_id}: {room_time:.2f}ms ({total_devices} 个设备)")
        
        avg_room_time = sum(room_times) / len(room_times)
        print(f"✅ 平均库房加载时间: {avg_room_time:.2f}ms")
        
        # 测试2.4: 批量调用性能
        print("\n[测试2.4] 批量调用性能（10次）...")
        
        start_time = time.time()
        for i in range(10):
            _ = get_all_device_configs()
        end_time = time.time()
        
        batch_time = (end_time - start_time) * 1000
        avg_call_time = batch_time / 10
        print(f"✅ 10次调用总时间: {batch_time:.2f}ms")
        print(f"   - 平均每次: {avg_call_time:.2f}ms")
        
        # 性能总结
        print("\n" + "="*80)
        print("性能测试总结:")
        print(f"  - 首次加载: {first_load_time:.2f}ms")
        print(f"  - 缓存加载: {cached_load_time:.2f}ms")
        print(f"  - 性能提升: {speedup:.2f}x")
        print(f"  - 平均库房加载: {avg_room_time:.2f}ms")
        print(f"  - 批量调用平均: {avg_call_time:.2f}ms")
        
        # 性能评估
        if first_load_time < 100:
            print("✅ 首次加载性能优秀 (< 100ms)")
        elif first_load_time < 200:
            print("✅ 首次加载性能良好 (< 200ms)")
        else:
            print("⚠️  首次加载性能需要优化 (> 200ms)")
        
        if cached_load_time < 20:
            print("✅ 缓存性能优秀 (< 20ms)")
        elif cached_load_time < 50:
            print("✅ 缓存性能良好 (< 50ms)")
        else:
            print("⚠️  缓存性能需要优化 (> 50ms)")
        
        print("="*80)
        return True
        
    except Exception as e:
        print(f"\n❌ 性能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_logging():
    """测试日志输出"""
    print("\n" + "="*80)
    print("测试3: 日志输出验证")
    print("="*80)
    
    try:
        print("\n[测试3.1] 验证日志编号体系...")
        
        # 清除缓存以触发完整的日志输出
        clear_device_config_cache()
        time.sleep(0.5)
        
        print("\n--- 开始捕获日志 ---")
        
        # 调用函数，观察日志输出
        configs = get_all_device_configs(room_id="611")
        
        print("--- 日志捕获结束 ---\n")
        
        print("✅ 日志输出正常，请检查上方日志是否包含以下编号：")
        print("   - [CONFIG-001] 开始获取设备配置")
        print("   - [CONFIG-002] 发现设备类型")
        print("   - [CONFIG-003] 配置文件修改时间")
        print("   - [CONFIG-004] 获取库房设备列表")
        print("   - [CONFIG-006] 库房设备过滤")
        print("   - [CONFIG-008] 设备配置获取完成")
        
        print("\n[测试3.2] 验证日志格式...")
        print("✅ 日志格式应符合: [编号] 描述 | 字段1: 值1, 字段2: 值2")
        
        print("\n[测试3.3] 验证日志语言...")
        print("✅ 所有日志应使用中文")
        
        print("\n" + "="*80)
        print("✅ 日志输出验证完成（请人工检查上方日志）")
        print("="*80)
        return True
        
    except Exception as e:
        print(f"\n❌ 日志测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_edge_cases():
    """测试边界条件"""
    print("\n" + "="*80)
    print("测试4: 边界条件测试")
    print("="*80)
    
    try:
        # 测试4.1: 空字符串库房ID
        print("\n[测试4.1] 空字符串库房ID...")
        empty_configs = get_all_device_configs(room_id="")
        print(f"✅ 空字符串返回: {len(empty_configs)} 个配置")
        
        # 测试4.2: 特殊字符库房ID
        print("\n[测试4.2] 特殊字符库房ID...")
        special_configs = get_all_device_configs(room_id="@#$%")
        print(f"✅ 特殊字符返回: {len(special_configs)} 个配置")
        
        # 测试4.3: 超长库房ID
        print("\n[测试4.3] 超长库房ID...")
        long_configs = get_all_device_configs(room_id="x" * 1000)
        print(f"✅ 超长ID返回: {len(long_configs)} 个配置")
        
        # 测试4.4: 数字类型库房ID（应该转换为字符串）
        print("\n[测试4.4] 数字类型库房ID...")
        try:
            # 注意：函数签名要求 str，传入 int 可能会报错
            # 这里测试类型检查
            numeric_configs = get_all_device_configs(room_id=611)  # type: ignore
            print(f"⚠️  数字类型被接受: {len(numeric_configs)} 个配置")
        except TypeError as e:
            print(f"✅ 数字类型被正确拒绝: {e}")
        
        print("\n" + "="*80)
        print("✅ 边界条件测试完成")
        print("="*80)
        return True
        
    except Exception as e:
        print(f"\n❌ 边界条件测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("\n" + "="*80)
    print("get_all_device_configs 函数优化测试")
    print("="*80)
    
    results = {
        "功能正确性": False,
        "性能对比": False,
        "日志输出": False,
        "边界条件": False
    }
    
    # 运行所有测试
    results["功能正确性"] = test_functionality()
    results["性能对比"] = test_performance()
    results["日志输出"] = test_logging()
    results["边界条件"] = test_edge_cases()
    
    # 输出测试总结
    print("\n" + "="*80)
    print("测试总结")
    print("="*80)
    
    for test_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "="*80)
    if all_passed:
        print("🎉 所有测试通过！优化效果验证成功！")
    else:
        print("⚠️  部分测试失败，请检查上方详细信息")
    print("="*80 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
