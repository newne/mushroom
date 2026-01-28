#!/usr/bin/env python3
"""
导入静态配置到IoT静态配置表

该脚本用于将static_config.json中定义的库房信息及设备、测点配置导入到
iot_static_point_config表中，并按文件修改时间进行版本管理。

使用方法:
    # 导入所有静态配置
    python scripts/import_static_config.py
    
    # 导入指定库房的配置
    python scripts/import_static_config.py --room-id 611
    
    # 导入指定设备类型的配置
    python scripts/import_static_config.py --device-type air_cooler
    
    # 强制更新（即使版本没有变化）
    python scripts/import_static_config.py --force-update
    
    # 预览模式（不实际写入数据库）
    python scripts/import_static_config.py --dry-run
"""

import sys
import json
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from global_const.global_const import static_settings
from utils.create_table import (
    store_decision_analysis_static_configs,
    query_decision_analysis_static_configs,
    DecisionAnalysisStaticConfig
)
from utils.loguru_setting import loguru_setting
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def get_config_file_info() -> Dict[str, Any]:
    """
    获取配置文件信息
    
    Returns:
        配置文件信息字典
    """
    config_file_path = Path(__file__).parent.parent / "src" / "configs" / "static_config.json"
    
    if not config_file_path.exists():
        raise FileNotFoundError(f"Static config file not found: {config_file_path}")
    
    # 获取文件修改时间
    file_mtime = datetime.fromtimestamp(config_file_path.stat().st_mtime)
    
    # 读取文件内容并计算哈希
    with open(config_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
    
    return {
        "file_path": str(config_file_path),
        "file_size": config_file_path.stat().st_size,
        "modified_time": file_mtime,
        "content_hash": content_hash,
        "content": content
    }


def extract_room_id_from_device_alias(device_alias: str) -> str:
    """
    从设备别名中提取库房ID
    
    Args:
        device_alias: 设备别名，如 "air_cooler_611"
        
    Returns:
        库房ID，如 "611"
    """
    # 设备别名格式通常是 {device_type}_{room_id}
    parts = device_alias.split('_')
    if len(parts) >= 2:
        return parts[-1]  # 取最后一部分作为库房ID
    return "unknown"


def determine_change_type_and_threshold(point_config: Dict[str, Any]) -> tuple:
    """
    根据点位配置确定变更类型和阈值
    
    Args:
        point_config: 点位配置字典
        
    Returns:
        (change_type, threshold) 元组
    """
    # 如果有enum字段，则为枚举类型
    if "enum" in point_config:
        return "enum_state", None
    
    # 根据点位名称和备注判断类型
    point_name = point_config.get("point_name", "").lower()
    remark = point_config.get("remark", "").lower()
    
    # 开关类型
    if any(keyword in point_name for keyword in ["onoff", "on_off"]) or \
       any(keyword in remark for keyword in ["开关", "开启", "关闭"]):
        return "digital_on_off", None
    
    # 模拟量类型
    if any(keyword in remark for keyword in ["设定", "温度", "湿度", "时间", "分钟"]):
        # 根据不同类型设置不同的阈值
        if "温度" in remark:
            return "analog_value", 0.5
        elif "湿度" in remark:
            return "analog_value", 2.0
        elif "时间" in remark or "分钟" in remark:
            return "analog_value", 1.0
        elif "co2" in remark.lower():
            return "analog_value", 50.0
        else:
            return "analog_value", 1.0
    
    # 默认为枚举状态
    return "enum_state", None


def extract_static_configs_from_settings(room_filter: Optional[str] = None,
                                        device_type_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    从static_settings中提取静态配置信息
    
    Args:
        room_filter: 库房ID过滤器
        device_type_filter: 设备类型过滤器
        
    Returns:
        静态配置记录列表
    """
    try:
        static_configs = []
        file_info = get_config_file_info()
        
        # 获取蘑菇房配置
        mushroom_config = static_settings.get("mushroom", {})
        datapoint_config = mushroom_config.get("datapoint", {})
        
        logger.info(f"Processing static config from: {file_info['file_path']}")
        logger.info(f"File modified time: {file_info['modified_time']}")
        logger.info(f"Content hash: {file_info['content_hash']}")
        
        # 遍历所有设备类型
        for device_type, device_config in datapoint_config.items():
            if device_type == "remark":  # 跳过备注字段
                continue
                
            if device_type_filter and device_type != device_type_filter:
                continue
            
            device_list = device_config.get("device_list", [])
            point_list = device_config.get("point_list", [])
            
            logger.info(f"Processing device type: {device_type} ({len(device_list)} devices, {len(point_list)} points)")
            
            # 为每个设备的每个点位创建配置记录
            for device in device_list:
                device_name = device.get("device_name")
                device_alias = device.get("device_alias")
                room_id = extract_room_id_from_device_alias(device_alias)
                
                if room_filter and room_id != room_filter:
                    continue
                
                for point in point_list:
                    point_alias = point.get("point_alias")
                    point_name = point.get("point_name")
                    remark = point.get("remark")
                    enum_mapping = point.get("enum")
                    
                    # 确定变更类型和阈值
                    change_type, threshold = determine_change_type_and_threshold(point)
                    
                    config = {
                        "room_id": room_id,
                        "device_type": device_type,
                        "device_name": device_name,
                        "device_alias": device_alias,
                        "point_alias": point_alias,
                        "point_name": point_name,
                        "remark": remark,
                        "change_type": change_type,
                        "threshold": threshold,
                        "enum_mapping": enum_mapping,
                        "source": "static_config_import",
                        "operator": "system",
                        "effective_time": file_info["modified_time"],
                        "comment": f"Imported from static_config.json (hash: {file_info['content_hash'][:8]})"
                    }
                    
                    static_configs.append(config)
        
        logger.info(f"Extracted {len(static_configs)} static configurations")
        return static_configs
        
    except Exception as e:
        logger.error(f"Failed to extract static configs from settings: {e}")
        raise


def get_current_config_version(room_id: str = None, device_type: str = None) -> int:
    """
    获取当前配置的最大版本号
    
    Args:
        room_id: 库房ID过滤
        device_type: 设备类型过滤
        
    Returns:
        当前最大版本号
    """
    try:
        existing_configs = query_decision_analysis_static_configs(
            room_id=room_id,
            device_type=device_type,
            is_active=None,  # 查询所有配置，包括非活跃的
            limit=1000
        )
        
        if not existing_configs:
            return 0
        
        max_version = max(config.config_version for config in existing_configs)
        return max_version
        
    except Exception as e:
        logger.warning(f"Failed to get current config version: {e}")
        return 0


def check_config_changes(new_configs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    检查配置是否有变化
    
    Args:
        new_configs: 新的配置列表
        
    Returns:
        变化统计信息
    """
    try:
        # 获取现有配置
        existing_configs = query_decision_analysis_static_configs(is_active=True, limit=10000)
        
        # 创建现有配置的索引
        existing_index = {}
        for config in existing_configs:
            key = f"{config.room_id}_{config.device_alias}_{config.point_alias}"
            existing_index[key] = {
                "device_name": config.device_name,
                "point_name": config.point_name,
                "remark": config.remark,
                "change_type": config.change_type,
                "threshold": config.threshold,
                "enum_mapping": config.enum_mapping
            }
        
        # 比较配置
        stats = {
            "total_new": len(new_configs),
            "total_existing": len(existing_configs),
            "new_points": 0,
            "updated_points": 0,
            "unchanged_points": 0,
            "changes": []
        }
        
        for new_config in new_configs:
            key = f"{new_config['room_id']}_{new_config['device_alias']}_{new_config['point_alias']}"
            
            if key not in existing_index:
                stats["new_points"] += 1
                stats["changes"].append({
                    "type": "new",
                    "key": key,
                    "config": new_config
                })
            else:
                existing = existing_index[key]
                
                # 检查是否有变化
                has_changes = False
                changes = {}
                
                for field in ["device_name", "point_name", "remark", "change_type", "threshold", "enum_mapping"]:
                    if existing[field] != new_config.get(field):
                        has_changes = True
                        changes[field] = {
                            "old": existing[field],
                            "new": new_config.get(field)
                        }
                
                if has_changes:
                    stats["updated_points"] += 1
                    stats["changes"].append({
                        "type": "updated",
                        "key": key,
                        "changes": changes
                    })
                else:
                    stats["unchanged_points"] += 1
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to check config changes: {e}")
        return {"error": str(e)}


def import_static_configs(room_filter: Optional[str] = None,
                         device_type_filter: Optional[str] = None,
                         force_update: bool = False,
                         dry_run: bool = False) -> Dict[str, Any]:
    """
    导入静态配置到数据库
    
    Args:
        room_filter: 库房ID过滤器
        device_type_filter: 设备类型过滤器
        force_update: 是否强制更新
        dry_run: 是否为预览模式
        
    Returns:
        导入结果统计信息
    """
    try:
        logger.info("Starting static config import...")
        
        # 1. 提取配置
        new_configs = extract_static_configs_from_settings(room_filter, device_type_filter)
        
        if not new_configs:
            logger.warning("No configurations found to import")
            return {"status": "no_data", "message": "No configurations found"}
        
        # 2. 检查变化
        change_stats = check_config_changes(new_configs)
        
        if "error" in change_stats:
            return {"status": "error", "message": change_stats["error"]}
        
        logger.info("Configuration change analysis:")
        logger.info(f"  - Total new configs: {change_stats['total_new']}")
        logger.info(f"  - Total existing configs: {change_stats['total_existing']}")
        logger.info(f"  - New points: {change_stats['new_points']}")
        logger.info(f"  - Updated points: {change_stats['updated_points']}")
        logger.info(f"  - Unchanged points: {change_stats['unchanged_points']}")
        
        # 3. 决定是否需要导入
        if not force_update and change_stats["new_points"] == 0 and change_stats["updated_points"] == 0:
            logger.info("No changes detected, skipping import")
            return {
                "status": "no_changes",
                "message": "No changes detected",
                "stats": change_stats
            }
        
        # 4. 预览模式
        if dry_run:
            logger.info("DRY RUN MODE - No actual changes will be made")
            
            if change_stats["changes"]:
                logger.info("Changes that would be made:")
                for change in change_stats["changes"][:10]:  # 只显示前10个变化
                    if change["type"] == "new":
                        logger.info(f"  NEW: {change['key']}")
                    elif change["type"] == "updated":
                        logger.info(f"  UPDATE: {change['key']} - {list(change['changes'].keys())}")
            
            return {
                "status": "dry_run",
                "message": "Dry run completed",
                "stats": change_stats
            }
        
        # 5. 执行导入
        logger.info("Importing configurations to database...")
        
        # 获取当前版本号
        current_version = get_current_config_version(room_filter, device_type_filter)
        new_version = current_version + 1
        
        # 为所有配置设置新版本号
        for config in new_configs:
            config["config_version"] = new_version
        
        # 存储配置
        stored_count = store_decision_analysis_static_configs(new_configs)
        
        result = {
            "status": "success",
            "message": f"Successfully imported {stored_count} configurations",
            "stats": change_stats,
            "version": new_version,
            "stored_count": stored_count
        }
        
        logger.info(f"Import completed successfully:")
        logger.info(f"  - Stored configurations: {stored_count}")
        logger.info(f"  - New version: {new_version}")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to import static configs: {e}")
        return {"status": "error", "message": str(e)}


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Import static configurations to IoT static config table")
    
    # 过滤参数
    parser.add_argument("--room-id", type=str, help="Room ID filter")
    parser.add_argument("--device-type", type=str, help="Device type filter")
    
    # 控制参数
    parser.add_argument("--force-update", action="store_true", 
                       help="Force update even if no changes detected")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Preview mode - don't actually import")
    
    args = parser.parse_args()
    
    try:
        logger.info("Static Config Import Tool")
        logger.info("=" * 50)
        
        if args.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")
        
        if args.room_id:
            logger.info(f"📍 Room filter: {args.room_id}")
        
        if args.device_type:
            logger.info(f"🔧 Device type filter: {args.device_type}")
        
        # 执行导入
        result = import_static_configs(
            room_filter=args.room_id,
            device_type_filter=args.device_type,
            force_update=args.force_update,
            dry_run=args.dry_run
        )
        
        # 显示结果
        print("\n" + "="*60)
        print("IMPORT SUMMARY")
        print("="*60)
        
        if result["status"] == "success":
            print(f"✅ Status: {result['message']}")
            print(f"📊 Version: {result['version']}")
            print(f"💾 Stored: {result['stored_count']} configurations")
            
            stats = result["stats"]
            print(f"📈 Changes:")
            print(f"   - New points: {stats['new_points']}")
            print(f"   - Updated points: {stats['updated_points']}")
            print(f"   - Unchanged points: {stats['unchanged_points']}")
            
        elif result["status"] == "no_changes":
            print(f"ℹ️  Status: {result['message']}")
            
        elif result["status"] == "dry_run":
            print(f"🔍 Status: {result['message']}")
            stats = result["stats"]
            print(f"📈 Would make changes:")
            print(f"   - New points: {stats['new_points']}")
            print(f"   - Updated points: {stats['updated_points']}")
            
        elif result["status"] == "error":
            print(f"❌ Status: Error - {result['message']}")
            sys.exit(1)
            
        print("="*60)
        
    except Exception as e:
        logger.error(f"Import failed: {e}")
        print(f"❌ Import failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()