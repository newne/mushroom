#!/usr/bin/env python3
"""
查看enum_mapping字段的正确显示工具

该脚本专门用于正确显示数据库中enum_mapping字段的中文内容，
确保中文字符正确显示而不是Unicode转义序列。

使用方法:
    # 查看所有enum_mapping
    python scripts/view_enum_mapping.py
    
    # 查看特定房间的enum_mapping
    python scripts/view_enum_mapping.py --room-id 611
    
    # 查看特定设备类型的enum_mapping
    python scripts/view_enum_mapping.py --device-type air_cooler
    
    # 输出为JSON格式（正确编码）
    python scripts/view_enum_mapping.py --json
"""

import sys
import argparse
import json
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import query_decision_analysis_static_configs
from utils.loguru_setting import loguru_setting
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def display_enum_mappings(room_id=None, device_type=None, output_json=False, limit=50):
    """
    显示enum_mapping字段内容
    
    Args:
        room_id: 房间ID过滤
        device_type: 设备类型过滤
        output_json: 是否输出JSON格式
        limit: 结果数量限制
    """
    try:
        logger.info("Querying enum_mapping data...")
        
        # 查询数据
        configs = query_decision_analysis_static_configs(
            room_id=room_id,
            device_type=device_type,
            limit=limit
        )
        
        # 过滤出包含enum_mapping的记录
        enum_configs = [config for config in configs if config.enum_mapping]
        
        if not enum_configs:
            print("No records with enum_mapping found.")
            return
        
        logger.info(f"Found {len(enum_configs)} records with enum_mapping")
        
        if output_json:
            # JSON格式输出
            json_data = []
            for config in enum_configs:
                json_data.append({
                    "room_id": config.room_id,
                    "device_type": config.device_type,
                    "device_alias": config.device_alias,
                    "point_alias": config.point_alias,
                    "point_name": config.point_name,
                    "remark": config.remark,
                    "enum_mapping": config.enum_mapping
                })
            
            # 使用ensure_ascii=False确保中文正确显示
            print(json.dumps(json_data, ensure_ascii=False, indent=2))
        else:
            # 表格格式输出
            print("\n" + "="*100)
            print("ENUM_MAPPING 数据查看")
            print("="*100)
            
            for i, config in enumerate(enum_configs, 1):
                print(f"\n记录 {i}:")
                print(f"  🏠 房间: {config.room_id}")
                print(f"  🔧 设备: {config.device_type} ({config.device_alias})")
                print(f"  📍 点位: {config.point_alias} ({config.point_name})")
                print(f"  📝 备注: {config.remark}")
                print(f"  🏷️  枚举映射:")
                
                for key, value in config.enum_mapping.items():
                    print(f"      {key} = {value}")
                
                print("-" * 80)
            
            print(f"\n总计: {len(enum_configs)} 个记录包含enum_mapping")
            print("="*100)
        
    except Exception as e:
        logger.error(f"Failed to display enum_mappings: {e}")
        raise


def compare_json_encodings(room_id=None, limit=5):
    """
    比较不同JSON编码方式的输出差异
    
    Args:
        room_id: 房间ID过滤
        limit: 结果数量限制
    """
    try:
        logger.info("Comparing JSON encoding methods...")
        
        # 查询数据
        configs = query_decision_analysis_static_configs(room_id=room_id, limit=limit)
        enum_configs = [config for config in configs if config.enum_mapping]
        
        if not enum_configs:
            print("No records with enum_mapping found for comparison.")
            return
        
        print("\n" + "="*100)
        print("JSON编码方式对比")
        print("="*100)
        
        for i, config in enumerate(enum_configs[:3], 1):  # 只显示前3个
            print(f"\n示例 {i}: {config.room_id}-{config.device_type}-{config.point_alias}")
            print(f"原始数据: {config.enum_mapping}")
            print(f"数据类型: {type(config.enum_mapping)}")
            print()
            
            # 不同的JSON编码方式
            print("JSON编码对比:")
            print(f"  默认设置:        {json.dumps(config.enum_mapping)}")
            print(f"  ensure_ascii=False: {json.dumps(config.enum_mapping, ensure_ascii=False)}")
            print(f"  ensure_ascii=True:  {json.dumps(config.enum_mapping, ensure_ascii=True)}")
            print()
            
            # 解释差异
            default_json = json.dumps(config.enum_mapping)
            correct_json = json.dumps(config.enum_mapping, ensure_ascii=False)
            
            if default_json != correct_json:
                print("  ⚠️  注意: 默认设置会将中文转义为Unicode序列")
                print("  ✅ 推荐: 使用 ensure_ascii=False 保持中文字符")
            else:
                print("  ✅ 该记录的JSON编码没有差异")
            
            print("-" * 80)
        
        print("\n总结:")
        print("- 数据库中的中文字符存储是正确的")
        print("- 问题出现在JSON序列化时的编码设置")
        print("- 使用 json.dumps(data, ensure_ascii=False) 可以正确显示中文")
        print("="*100)
        
    except Exception as e:
        logger.error(f"Failed to compare JSON encodings: {e}")
        raise


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="View enum_mapping fields with correct Chinese display")
    
    parser.add_argument("--room-id", type=str, help="Room ID filter")
    parser.add_argument("--device-type", type=str, help="Device type filter")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--compare", action="store_true", help="Compare different JSON encoding methods")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of results")
    
    args = parser.parse_args()
    
    try:
        logger.info("Enum Mapping Viewer")
        logger.info("=" * 50)
        
        if args.compare:
            compare_json_encodings(args.room_id, args.limit)
        else:
            display_enum_mappings(
                room_id=args.room_id,
                device_type=args.device_type,
                output_json=args.json,
                limit=args.limit
            )
            
    except Exception as e:
        logger.error(f"Enum mapping viewer failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()