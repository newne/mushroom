#!/usr/bin/env python3
"""
缓存管理工具
提供缓存查看、清理、更新等功能
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.paths import ensure_src_path
ensure_src_path()

from utils.dataframe_utils import (
    get_static_config_by_device_type,
    get_all_device_configs,
    clear_device_config_cache,
    get_cache_info,
    STATIC_CONFIG_FILE_PATH
)
from utils.loguru_setting import loguru_setting
from loguru import logger


def show_cache_status():
    """显示缓存状态"""
    print("📊 缓存状态概览")
    print("=" * 60)
    
    try:
        cache_info = get_cache_info()
        summary = cache_info.get('_summary', {})
        
        print(f"配置文件路径: {summary.get('config_file_path', 'N/A')}")
        print(f"配置文件存在: {'是' if summary.get('config_file_exists', False) else '否'}")
        print(f"设备类型总数: {summary.get('total_device_types', 0)}")
        print(f"已缓存类型: {summary.get('cached_types', 0)}")
        print(f"有效缓存数: {summary.get('valid_caches', 0)}")
        print()
        
        # 显示各设备类型的详细信息
        print("📋 各设备类型缓存详情:")
        print("-" * 60)
        
        for device_type, info in cache_info.items():
            if device_type == '_summary' or not isinstance(info, dict):
                continue
            
            cache_exists = info.get('cache_exists', False)
            cache_valid = info.get('cache_valid', False)
            ttl = info.get('ttl', None)
            
            status_icon = "✅" if cache_valid else "❌" if cache_exists else "⚪"
            status_text = "有效" if cache_valid else "无效" if cache_exists else "无缓存"
            
            print(f"{status_icon} {device_type:<20} {status_text:<8}", end="")
            
            if ttl is not None and ttl > 0:
                hours = ttl // 3600
                minutes = (ttl % 3600) // 60
                print(f" TTL: {hours}h{minutes}m", end="")
            
            # 显示元数据信息
            metadata = info.get('metadata')
            if metadata:
                created_at = metadata.get('created_at')
                if created_at:
                    created_time = datetime.fromtimestamp(created_at)
                    print(f" 创建: {created_time.strftime('%m-%d %H:%M')}", end="")
            
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ 获取缓存状态失败: {e}")
        return False


def show_detailed_cache_info(device_type: str):
    """显示指定设备类型的详细缓存信息"""
    print(f"🔍 设备类型 '{device_type}' 的详细缓存信息")
    print("=" * 60)
    
    try:
        cache_info = get_cache_info(device_type)
        
        if 'error' in cache_info:
            print(f"❌ 获取缓存信息失败: {cache_info['error']}")
            return False
        
        print(f"设备类型: {cache_info.get('device_type', 'N/A')}")
        print(f"缓存存在: {'是' if cache_info.get('cache_exists', False) else '否'}")
        print(f"元数据存在: {'是' if cache_info.get('metadata_exists', False) else '否'}")
        print(f"缓存有效: {'是' if cache_info.get('cache_valid', False) else '否'}")
        
        ttl = cache_info.get('ttl')
        if ttl is not None:
            if ttl > 0:
                hours = ttl // 3600
                minutes = (ttl % 3600) // 60
                seconds = ttl % 60
                print(f"剩余TTL: {hours}h {minutes}m {seconds}s")
            else:
                print("剩余TTL: 已过期")
        else:
            print("剩余TTL: N/A")
        
        file_mtime = cache_info.get('file_mtime')
        if file_mtime:
            file_time = datetime.fromtimestamp(file_mtime)
            print(f"配置文件修改时间: {file_time}")
        
        # 显示元数据
        metadata = cache_info.get('metadata')
        if metadata:
            print("\n📋 缓存元数据:")
            created_at = metadata.get('created_at')
            if created_at:
                created_time = datetime.fromtimestamp(created_at)
                print(f"  创建时间: {created_time}")
            
            config_file = metadata.get('config_file')
            if config_file:
                print(f"  配置文件: {config_file}")
            
            ttl_setting = metadata.get('ttl')
            if ttl_setting:
                print(f"  TTL设置: {ttl_setting} 秒")
        
        return True
        
    except Exception as e:
        print(f"❌ 获取详细缓存信息失败: {e}")
        return False


def clear_cache(device_type: str = None):
    """清除缓存"""
    if device_type:
        print(f"🗑️ 清除设备类型 '{device_type}' 的缓存")
    else:
        print("🗑️ 清除所有设备类型的缓存")
    
    print("-" * 40)
    
    try:
        success = clear_device_config_cache(device_type)
        
        if success:
            if device_type:
                print(f"✅ 设备类型 '{device_type}' 的缓存已清除")
            else:
                print("✅ 所有设备类型的缓存已清除")
        else:
            print("❌ 缓存清除失败")
        
        return success
        
    except Exception as e:
        print(f"❌ 清除缓存时发生异常: {e}")
        return False


def refresh_cache(device_type: str = None):
    """刷新缓存"""
    if device_type:
        print(f"🔄 刷新设备类型 '{device_type}' 的缓存")
        device_types = [device_type]
    else:
        print("🔄 刷新所有设备类型的缓存")
        # 获取所有设备类型
        try:
            from global_const.global_const import static_settings
            datapoint_config = static_settings.mushroom.datapoint
            device_types = [
                key for key, value in datapoint_config.items()
                if isinstance(value, dict) and 'device_list' in value
            ]
        except Exception as e:
            print(f"❌ 获取设备类型列表失败: {e}")
            return False
    
    print("-" * 40)
    
    try:
        # 先清除缓存
        clear_success = clear_device_config_cache(device_type)
        if not clear_success:
            print("⚠️ 清除旧缓存失败，继续尝试刷新")
        
        # 重新生成缓存
        success_count = 0
        for dt in device_types:
            try:
                df = get_static_config_by_device_type(dt)
                print(f"✅ {dt}: {len(df)} 条记录")
                success_count += 1
            except Exception as e:
                print(f"❌ {dt}: 失败 - {e}")
        
        print(f"\n📊 刷新结果: {success_count}/{len(device_types)} 个设备类型成功")
        return success_count == len(device_types)
        
    except Exception as e:
        print(f"❌ 刷新缓存时发生异常: {e}")
        return False


def validate_cache():
    """验证缓存完整性"""
    print("🔍 验证缓存完整性")
    print("=" * 60)
    
    try:
        # 获取所有设备类型
        from global_const.global_const import static_settings
        datapoint_config = static_settings.mushroom.datapoint
        device_types = [
            key for key, value in datapoint_config.items()
            if isinstance(value, dict) and 'device_list' in value
        ]
        
        print(f"检查 {len(device_types)} 个设备类型的缓存...")
        print()
        
        valid_count = 0
        invalid_count = 0
        missing_count = 0
        
        for device_type in device_types:
            try:
                cache_info = get_cache_info(device_type)
                cache_exists = cache_info.get('cache_exists', False)
                cache_valid = cache_info.get('cache_valid', False)
                
                if not cache_exists:
                    print(f"⚪ {device_type:<20} 缓存不存在")
                    missing_count += 1
                elif cache_valid:
                    print(f"✅ {device_type:<20} 缓存有效")
                    valid_count += 1
                else:
                    print(f"❌ {device_type:<20} 缓存无效")
                    invalid_count += 1
                    
            except Exception as e:
                print(f"💥 {device_type:<20} 检查失败: {e}")
                invalid_count += 1
        
        print()
        print("📊 验证结果:")
        print(f"  有效缓存: {valid_count}")
        print(f"  无效缓存: {invalid_count}")
        print(f"  缺失缓存: {missing_count}")
        print(f"  总计: {len(device_types)}")
        
        if invalid_count > 0 or missing_count > 0:
            print(f"\n💡 建议运行 'python {sys.argv[0]} --refresh' 来修复缓存问题")
        
        return invalid_count == 0 and missing_count == 0
        
    except Exception as e:
        print(f"❌ 验证缓存时发生异常: {e}")
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='设备配置缓存管理工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 显示缓存状态
  python scripts/cache_manager.py --status
  
  # 显示指定设备类型的详细信息
  python scripts/cache_manager.py --info air_cooler
  
  # 清除所有缓存
  python scripts/cache_manager.py --clear
  
  # 清除指定设备类型的缓存
  python scripts/cache_manager.py --clear --device-type air_cooler
  
  # 刷新所有缓存
  python scripts/cache_manager.py --refresh
  
  # 刷新指定设备类型的缓存
  python scripts/cache_manager.py --refresh --device-type fresh_air_fan
  
  # 验证缓存完整性
  python scripts/cache_manager.py --validate
        """
    )
    
    parser.add_argument('--status', action='store_true', help='显示缓存状态')
    parser.add_argument('--info', metavar='DEVICE_TYPE', help='显示指定设备类型的详细信息')
    parser.add_argument('--clear', action='store_true', help='清除缓存')
    parser.add_argument('--refresh', action='store_true', help='刷新缓存')
    parser.add_argument('--validate', action='store_true', help='验证缓存完整性')
    parser.add_argument('--device-type', metavar='TYPE', help='指定设备类型')
    
    args = parser.parse_args()
    
    # 初始化日志
    loguru_setting()
    
    print("🛠️ 设备配置缓存管理工具")
    print(f"⏰ 执行时间: {datetime.now()}")
    print()
    
    try:
        success = True
        
        if args.status:
            success = show_cache_status()
        elif args.info:
            success = show_detailed_cache_info(args.info)
        elif args.clear:
            success = clear_cache(args.device_type)
        elif args.refresh:
            success = refresh_cache(args.device_type)
        elif args.validate:
            success = validate_cache()
        else:
            # 默认显示状态
            success = show_cache_status()
        
        print()
        if success:
            print("✅ 操作完成")
        else:
            print("❌ 操作失败")
            sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n⚠️ 操作被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 操作过程中发生异常: {e}")
        logger.error(f"Cache manager operation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()