#!/usr/bin/env python3
"""
验证中文字符显示

该脚本用于验证数据库中的中文字符是否正确显示，
并提供详细的中文字符统计信息。

使用方法:
    python scripts/verify_chinese_display.py
"""

import sys
from pathlib import Path
from collections import defaultdict

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import query_decision_analysis_static_configs
from utils.loguru_setting import loguru_setting
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def analyze_chinese_characters():
    """
    分析数据库中的中文字符
    """
    try:
        logger.info("Analyzing Chinese characters in database...")
        
        # 查询所有静态配置
        configs = query_decision_analysis_static_configs(limit=1000)
        
        if not configs:
            logger.warning("No configurations found")
            return
        
        # 统计信息
        stats = {
            "total_records": len(configs),
            "chinese_records": 0,
            "chinese_fields": defaultdict(int),
            "device_types": defaultdict(int),
            "rooms": set(),
            "sample_chinese_texts": []
        }
        
        for config in configs:
            has_chinese = False
            stats["rooms"].add(config.room_id)
            stats["device_types"][config.device_type] += 1
            
            # 检查各个字段的中文字符
            fields_to_check = [
                ("device_name", config.device_name),
                ("remark", config.remark),
                ("comment", config.comment)
            ]
            
            for field_name, field_value in fields_to_check:
                if field_value and isinstance(field_value, str):
                    # 检查是否包含中文字符
                    if any('\u4e00' <= char <= '\u9fff' for char in field_value):
                        has_chinese = True
                        stats["chinese_fields"][field_name] += 1
                        
                        # 收集样本文本
                        if len(stats["sample_chinese_texts"]) < 10:
                            stats["sample_chinese_texts"].append({
                                "field": field_name,
                                "text": field_value,
                                "room_id": config.room_id,
                                "device_type": config.device_type
                            })
            
            # 检查JSON字段中的中文
            if config.enum_mapping and isinstance(config.enum_mapping, dict):
                for key, value in config.enum_mapping.items():
                    if isinstance(value, str) and any('\u4e00' <= char <= '\u9fff' for char in value):
                        has_chinese = True
                        stats["chinese_fields"]["enum_mapping"] += 1
                        
                        if len(stats["sample_chinese_texts"]) < 10:
                            stats["sample_chinese_texts"].append({
                                "field": "enum_mapping",
                                "text": f"{key}={value}",
                                "room_id": config.room_id,
                                "device_type": config.device_type
                            })
                        break
            
            if has_chinese:
                stats["chinese_records"] += 1
        
        # 显示统计结果
        print("\n" + "="*60)
        print("CHINESE CHARACTER ANALYSIS REPORT")
        print("="*60)
        
        print(f"📊 总体统计:")
        print(f"   - 总记录数: {stats['total_records']}")
        print(f"   - 包含中文的记录数: {stats['chinese_records']}")
        print(f"   - 中文覆盖率: {stats['chinese_records']/stats['total_records']*100:.1f}%")
        
        print(f"\n🏠 库房分布:")
        for room_id in sorted(stats['rooms']):
            print(f"   - 库房 {room_id}")
        
        print(f"\n🔧 设备类型分布:")
        for device_type, count in sorted(stats['device_types'].items()):
            print(f"   - {device_type}: {count} 个配置")
        
        print(f"\n📝 中文字段统计:")
        for field_name, count in sorted(stats['chinese_fields'].items()):
            print(f"   - {field_name}: {count} 个记录包含中文")
        
        print(f"\n📋 中文文本样本:")
        for i, sample in enumerate(stats['sample_chinese_texts'], 1):
            print(f"   {i}. [{sample['room_id']}] {sample['device_type']}.{sample['field']}: {sample['text']}")
        
        print("="*60)
        
        # 验证特定的中文字符
        test_characters = ["开关", "设定", "温度", "湿度", "关闭", "开启", "自动", "手动"]
        found_characters = set()
        
        for config in configs:
            text_fields = [config.device_name, config.remark, config.comment]
            if config.enum_mapping:
                text_fields.extend(config.enum_mapping.values())
            
            for field_value in text_fields:
                if field_value and isinstance(field_value, str):
                    for test_char in test_characters:
                        if test_char in field_value:
                            found_characters.add(test_char)
        
        print(f"\n✅ 常见中文词汇验证:")
        for char in test_characters:
            status = "✅" if char in found_characters else "❌"
            print(f"   {status} '{char}' - {'找到' if char in found_characters else '未找到'}")
        
        print("="*60)
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to analyze Chinese characters: {e}")
        return None


def main():
    """主函数"""
    try:
        logger.info("Chinese Character Display Verification")
        logger.info("=" * 50)
        
        stats = analyze_chinese_characters()
        
        if stats:
            if stats["chinese_records"] > 0:
                print("\n🎉 中文字符显示验证成功！")
                print("数据库中的中文字符能够正确存储和显示。")
            else:
                print("\n⚠️  未找到包含中文字符的记录")
                print("可能需要重新导入包含中文的数据。")
        else:
            print("\n❌ 中文字符分析失败")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()