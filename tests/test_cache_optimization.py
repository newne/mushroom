#!/usr/bin/env python3
"""
测试缓存优化功能的脚本
验证基于文件修改时间的缓存时效性检查
"""

import sys
import time
from pathlib import Path
from datetime import datetime

# 添加src目录到路径
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

from utils.dataframe_utils import (
    get_static_config_by_device_type,
    get_all_device_configs,
    clear_device_config_cache,
    get_cache_info,
    STATIC_CONFIG_FILE_PATH
)
from utils.loguru_setting import loguru_setting
from loguru import logger


def test_cache_basic_functionality():
    """测试缓存基本功能"""
    print("🔧 测试缓存基本功能")
    print("-" * 40)
    
    try:
        # 清除所有缓存
        print("1. 清除所有缓存...")
        clear_success = clear_device_config_cache()
        print(f"   清除结果: {'成功' if clear_success else '失败'}")
        
        # 获取缓存信息
        print("\n2. 获取缓存信息...")
        cache_info = get_cache_info()
        summary = cache_info.get('_summary', {})
        print(f"   设备类型总数: {summary.get('total_device_types', 0)}")
        print(f"   已缓存类型: {summary.get('cached_types', 0)}")
        print(f"   有效缓存: {summary.get('valid_caches', 0)}")
        print(f"   配置文件存在: {summary.get('config_file_exists', False)}")
        
        # 测试获取配置（触发缓存生成）
        print("\n3. 获取设备配置（触发缓存生成）...")
        device_types = ['air_cooler', 'fresh_air_fan', 'grow_light']
        
        for device_type in device_types:
            try:
                df = get_static_config_by_device_type(device_type)
                print(f"   {device_type}: {len(df)} 条记录")
            except Exception as e:
                print(f"   {device_type}: 失败 - {e}")
        
        # 再次获取缓存信息
        print("\n4. 缓存生成后的信息...")
        cache_info = get_cache_info()
        summary = cache_info.get('_summary', {})
        print(f"   已缓存类型: {summary.get('cached_types', 0)}")
        print(f"   有效缓存: {summary.get('valid_caches', 0)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 基本功能测试失败: {e}")
        return False


def test_cache_validity_check():
    """测试缓存有效性检查"""
    print("\n🕒 测试缓存有效性检查")
    print("-" * 40)
    
    try:
        # 确保有缓存存在
        print("1. 生成初始缓存...")
        df = get_static_config_by_device_type('air_cooler')
        print(f"   生成缓存: {len(df)} 条记录")
        
        # 获取缓存信息
        print("\n2. 检查缓存状态...")
        cache_info = get_cache_info('air_cooler')
        print(f"   缓存存在: {cache_info.get('cache_exists', False)}")
        print(f"   缓存有效: {cache_info.get('cache_valid', False)}")
        
        if cache_info.get('metadata'):
            created_at = cache_info['metadata'].get('created_at')
            if created_at:
                created_time = datetime.fromtimestamp(created_at)
                print(f"   缓存创建时间: {created_time}")
        
        file_mtime = cache_info.get('file_mtime')
        if file_mtime:
            file_time = datetime.fromtimestamp(file_mtime)
            print(f"   文件修改时间: {file_time}")
        
        # 模拟文件修改（通过touch命令更新文件时间）
        print("\n3. 模拟配置文件更新...")
        try:
            # 更新文件的访问和修改时间
            STATIC_CONFIG_FILE_PATH.touch()
            print("   配置文件时间已更新")
            
            # 等待一秒确保时间差异
            time.sleep(1)
            
            # 再次检查缓存状态
            print("\n4. 文件更新后的缓存状态...")
            cache_info = get_cache_info('air_cooler')
            print(f"   缓存存在: {cache_info.get('cache_exists', False)}")
            print(f"   缓存有效: {cache_info.get('cache_valid', False)}")
            
            new_file_mtime = cache_info.get('file_mtime')
            if new_file_mtime:
                new_file_time = datetime.fromtimestamp(new_file_mtime)
                print(f"   新文件修改时间: {new_file_time}")
            
            # 重新获取配置（应该重新生成缓存）
            print("\n5. 重新获取配置（应触发缓存更新）...")
            df = get_static_config_by_device_type('air_cooler')
            print(f"   获取配置: {len(df)} 条记录")
            
            # 检查缓存是否已更新
            cache_info = get_cache_info('air_cooler')
            print(f"   更新后缓存有效: {cache_info.get('cache_valid', False)}")
            
        except Exception as e:
            print(f"   文件操作失败: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ 缓存有效性测试失败: {e}")
        return False


def test_cache_management_functions():
    """测试缓存管理功能"""
    print("\n🛠️ 测试缓存管理功能")
    print("-" * 40)
    
    try:
        # 生成一些缓存
        print("1. 生成测试缓存...")
        device_types = ['air_cooler', 'fresh_air_fan']
        for device_type in device_types:
            df = get_static_config_by_device_type(device_type)
            print(f"   {device_type}: {len(df)} 条记录")
        
        # 获取详细缓存信息
        print("\n2. 获取详细缓存信息...")
        for device_type in device_types:
            cache_info = get_cache_info(device_type)
            print(f"   {device_type}:")
            print(f"     缓存存在: {cache_info.get('cache_exists', False)}")
            print(f"     元数据存在: {cache_info.get('metadata_exists', False)}")
            print(f"     TTL: {cache_info.get('ttl', 'N/A')} 秒")
            print(f"     缓存有效: {cache_info.get('cache_valid', False)}")
        
        # 清除单个缓存
        print("\n3. 清除单个设备类型缓存...")
        clear_success = clear_device_config_cache('air_cooler')
        print(f"   清除 air_cooler 缓存: {'成功' if clear_success else '失败'}")
        
        # 验证清除结果
        cache_info = get_cache_info('air_cooler')
        print(f"   清除后缓存存在: {cache_info.get('cache_exists', False)}")
        
        # 清除所有缓存
        print("\n4. 清除所有缓存...")
        clear_success = clear_device_config_cache()
        print(f"   清除所有缓存: {'成功' if clear_success else '失败'}")
        
        # 验证清除结果
        cache_info = get_cache_info()
        summary = cache_info.get('_summary', {})
        print(f"   清除后已缓存类型: {summary.get('cached_types', 0)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 缓存管理功能测试失败: {e}")
        return False


def test_error_handling():
    """测试错误处理"""
    print("\n⚠️ 测试错误处理")
    print("-" * 40)
    
    try:
        # 测试不存在的设备类型
        print("1. 测试不存在的设备类型...")
        try:
            df = get_static_config_by_device_type('non_existent_device')
            print("   意外成功（应该失败）")
        except ValueError as e:
            print(f"   正确抛出异常: {e}")
        except Exception as e:
            print(f"   意外异常类型: {e}")
        
        # 测试获取不存在设备类型的缓存信息
        print("\n2. 测试不存在设备类型的缓存信息...")
        cache_info = get_cache_info('non_existent_device')
        print(f"   缓存存在: {cache_info.get('cache_exists', False)}")
        print(f"   缓存有效: {cache_info.get('cache_valid', False)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 错误处理测试失败: {e}")
        return False


def test_performance_comparison():
    """测试性能对比"""
    print("\n⚡ 测试性能对比")
    print("-" * 40)
    
    try:
        # 清除缓存
        clear_device_config_cache()
        
        # 测试首次加载（从配置文件）
        print("1. 首次加载性能测试...")
        start_time = time.time()
        configs = get_all_device_configs()
        first_load_time = time.time() - start_time
        print(f"   首次加载时间: {first_load_time:.3f} 秒")
        print(f"   加载设备类型数: {len(configs)}")
        
        # 测试缓存加载
        print("\n2. 缓存加载性能测试...")
        start_time = time.time()
        configs = get_all_device_configs()
        cache_load_time = time.time() - start_time
        print(f"   缓存加载时间: {cache_load_time:.3f} 秒")
        print(f"   加载设备类型数: {len(configs)}")
        
        # 计算性能提升
        if first_load_time > 0:
            speedup = first_load_time / cache_load_time if cache_load_time > 0 else float('inf')
            print(f"\n   性能提升: {speedup:.2f}x")
        
        return True
        
    except Exception as e:
        print(f"❌ 性能测试失败: {e}")
        return False


def main():
    """主测试函数"""
    # 初始化日志
    loguru_setting()
    
    print("🧪 缓存优化功能测试")
    print("=" * 60)
    print(f"⏰ 测试开始时间: {datetime.now()}")
    print()
    
    test_results = []
    
    try:
        # 运行各项测试
        tests = [
            ("基本功能测试", test_cache_basic_functionality),
            ("缓存有效性检查", test_cache_validity_check),
            ("缓存管理功能", test_cache_management_functions),
            ("错误处理测试", test_error_handling),
            ("性能对比测试", test_performance_comparison)
        ]
        
        for test_name, test_func in tests:
            print(f"\n{'='*60}")
            print(f"🔍 {test_name}")
            print(f"{'='*60}")
            
            try:
                result = test_func()
                test_results.append((test_name, result))
                
                if result:
                    print(f"✅ {test_name} 通过")
                else:
                    print(f"❌ {test_name} 失败")
                    
            except Exception as e:
                print(f"💥 {test_name} 异常: {e}")
                test_results.append((test_name, False))
                logger.error(f"Test {test_name} failed with exception: {e}")
        
        # 显示测试结果摘要
        print(f"\n{'='*60}")
        print("📊 测试结果摘要")
        print(f"{'='*60}")
        
        passed = sum(1 for _, result in test_results if result)
        total = len(test_results)
        
        print(f"总测试数: {total}")
        print(f"通过测试: {passed}")
        print(f"失败测试: {total - passed}")
        print(f"通过率: {passed/total*100:.1f}%")
        
        print("\n详细结果:")
        for test_name, result in test_results:
            status = "✅ 通过" if result else "❌ 失败"
            print(f"  {test_name}: {status}")
        
        print(f"\n⏰ 测试结束时间: {datetime.now()}")
        
        if passed == total:
            print("\n🎉 所有测试通过！缓存优化功能正常工作。")
        else:
            print(f"\n⚠️ 有 {total - passed} 个测试失败，请检查相关功能。")
        
    except KeyboardInterrupt:
        print("\n⚠️ 测试被用户中断")
    except Exception as e:
        print(f"\n💥 测试过程中发生异常: {e}")
        logger.error(f"Test process failed: {e}")


if __name__ == "__main__":
    main()