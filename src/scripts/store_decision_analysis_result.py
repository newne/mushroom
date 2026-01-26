#!/usr/bin/env python3
"""
存储增强决策分析结果到数据库

该脚本用于将增强决策分析的JSON输出结果存储到数据库中，
支持监控点格式和完整决策格式的数据。

使用方法:
    # 存储指定的JSON文件
    python scripts/store_decision_analysis_result.py --file output/enhanced_decision_analysis_611_20260123_122501.json
    
    # 存储最新的输出文件
    python scripts/store_decision_analysis_result.py --latest
    
    # 存储指定房间的最新输出文件
    python scripts/store_decision_analysis_result.py --latest --room-id 611
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import store_enhanced_decision_analysis_result
from utils.loguru_setting import loguru_setting
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def find_latest_output_file(room_id: str = None) -> Path:
    """
    查找最新的输出文件
    
    Args:
        room_id: 可选的房间ID过滤
        
    Returns:
        最新输出文件的路径
    """
    output_dir = Path(__file__).parent.parent / "output"
    
    if room_id:
        pattern = f"enhanced_decision_analysis_{room_id}_*.json"
    else:
        pattern = "enhanced_decision_analysis_*.json"
    
    json_files = list(output_dir.glob(pattern))
    
    if not json_files:
        raise FileNotFoundError(f"No output files found matching pattern: {pattern}")
    
    # 按修改时间排序，返回最新的
    latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
    return latest_file


def extract_metadata_from_filename(file_path: Path) -> dict:
    """
    从文件名中提取元数据
    
    Args:
        file_path: 文件路径
        
    Returns:
        包含房间ID和分析时间的字典
    """
    filename = file_path.stem
    parts = filename.split('_')
    
    metadata = {}
    
    # 提取房间ID (格式: enhanced_decision_analysis_611_20260123_122501)
    if len(parts) >= 4:
        metadata['room_id'] = parts[3]
    
    # 提取分析时间
    if len(parts) >= 6:
        date_str = parts[4]  # 20260123
        time_str = parts[5]  # 122501
        
        try:
            # 解析日期时间
            datetime_str = f"{date_str}_{time_str}"
            analysis_datetime = datetime.strptime(datetime_str, "%Y%m%d_%H%M%S")
            metadata['analysis_datetime'] = analysis_datetime
        except ValueError as e:
            logger.warning(f"Could not parse datetime from filename: {e}")
            metadata['analysis_datetime'] = datetime.now()
    else:
        metadata['analysis_datetime'] = datetime.now()
    
    return metadata


def determine_output_format(data: dict) -> str:
    """
    确定输出格式类型
    
    Args:
        data: JSON数据
        
    Returns:
        输出格式字符串
    """
    if "enhanced_decision" in data and "monitoring_points" in data:
        return "both"
    elif "devices" in data and "metadata" in data:
        return "monitoring"
    elif "strategy" in data and "device_recommendations" in data:
        return "enhanced"
    else:
        logger.warning("Could not determine output format, defaulting to 'monitoring'")
        return "monitoring"


def store_json_file(file_path: Path) -> str:
    """
    存储JSON文件到数据库
    
    Args:
        file_path: JSON文件路径
        
    Returns:
        记录ID
    """
    logger.info(f"Processing file: {file_path}")
    
    # 读取JSON数据
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON file: {e}")
        raise
    
    # 提取元数据
    metadata = extract_metadata_from_filename(file_path)
    
    # 从数据中获取房间ID（优先使用数据中的值）
    if "room_id" in data:
        room_id = data["room_id"]
    elif "metadata" in data and "room_id" in data["metadata"]:
        room_id = data["metadata"]["room_id"]
    else:
        room_id = metadata.get("room_id", "unknown")
    
    # 确定输出格式
    output_format = determine_output_format(data)
    
    logger.info(f"Detected: Room ID={room_id}, Format={output_format}, Analysis Time={metadata['analysis_datetime']}")
    
    # 存储到数据库
    try:
        record_id = store_enhanced_decision_analysis_result(
            result_data=data,
            room_id=room_id,
            analysis_datetime=metadata['analysis_datetime'],
            output_format=output_format,
            output_file_path=str(file_path)
        )
        
        logger.info(f"Successfully stored record with ID: {record_id}")
        return record_id
        
    except Exception as e:
        logger.error(f"Failed to store data to database: {e}")
        raise


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Store enhanced decision analysis results to database"
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file",
        type=str,
        help="Path to the JSON file to store"
    )
    group.add_argument(
        "--latest",
        action="store_true",
        help="Store the latest output file"
    )
    
    parser.add_argument(
        "--room-id",
        type=str,
        help="Room ID filter (only used with --latest)"
    )
    
    args = parser.parse_args()
    
    try:
        if args.file:
            # 存储指定文件
            file_path = Path(args.file)
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                sys.exit(1)
        else:
            # 查找最新文件
            file_path = find_latest_output_file(args.room_id)
            logger.info(f"Found latest file: {file_path}")
        
        # 存储文件
        record_id = store_json_file(file_path)
        
        logger.info("✅ Storage completed successfully!")
        logger.info(f"Record ID: {record_id}")
        logger.info(f"File: {file_path}")
        
    except Exception as e:
        logger.error(f"❌ Storage failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()