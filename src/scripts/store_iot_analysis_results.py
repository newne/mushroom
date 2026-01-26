#!/usr/bin/env python3
"""
存储决策分析结果到静态配置表和动态结果表

该脚本用于将增强决策分析的JSON输出结果存储到决策分析静态配置表和动态结果表中，
支持静态配置的版本管理和动态结果的批次追踪。

使用方法:
    # 存储指定的JSON文件
    python scripts/store_iot_analysis_results.py --file output/enhanced_decision_analysis_611_20260123_122501.json
    
    # 存储最新的输出文件
    python scripts/store_iot_analysis_results.py --latest
    
    # 存储指定房间的最新输出文件
    python scripts/store_iot_analysis_results.py --latest --room-id 611
    
    # 指定批次ID
    python scripts/store_iot_analysis_results.py --latest --batch-id custom_batch_001
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import store_decision_analysis_results
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
        raise FileNotFoundError(f"No files found matching pattern: {pattern}")
    
    # 按修改时间排序，返回最新的
    latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
    return latest_file


def store_json_file(file_path: Path, batch_id: str = None) -> dict:
    """
    存储JSON文件到IoT表
    
    Args:
        file_path: JSON文件路径
        batch_id: 可选的批次ID
        
    Returns:
        存储结果统计信息
    """
    try:
        logger.info(f"Processing file: {file_path}")
        
        # 读取JSON数据
        with open(file_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # 提取基本信息
        room_id = json_data.get("room_id")
        if not room_id:
            raise ValueError("No room_id found in JSON data")
        
        # 从文件名提取分析时间
        filename = file_path.stem
        # 格式: enhanced_decision_analysis_611_20260123_122501
        parts = filename.split('_')
        if len(parts) >= 5:
            date_str = parts[-2]  # 20260123
            time_str = parts[-1]  # 122501
            try:
                analysis_time = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            except ValueError:
                logger.warning(f"Could not parse datetime from filename, using current time")
                analysis_time = datetime.now()
        else:
            analysis_time = datetime.now()
        
        # 检测输出格式
        if "devices" in json_data and "enhanced_decision" in json_data:
            output_format = "both"
        elif "devices" in json_data:
            output_format = "monitoring"
        else:
            output_format = "enhanced"
        
        logger.info(f"Detected: Room ID={room_id}, Format={output_format}, Analysis Time={analysis_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 存储到决策分析表
        storage_result = store_decision_analysis_results(
            json_data=json_data,
            room_id=room_id,
            analysis_time=analysis_time,
            batch_id=batch_id
        )
        
        # 添加文件信息到结果中
        storage_result.update({
            "source_file": str(file_path),
            "file_size": file_path.stat().st_size,
            "output_format": output_format
        })
        
        logger.info(f"Successfully stored decision analysis results:")
        logger.info(f"  - Batch ID: {storage_result['batch_id']}")
        logger.info(f"  - Static configs: {storage_result['static_configs_stored']}")
        logger.info(f"  - Dynamic results: {storage_result['dynamic_results_stored']}")
        
        return storage_result
        
    except Exception as e:
        logger.error(f"Failed to store JSON file {file_path}: {e}")
        raise


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Store decision analysis results to database")
    
    # 文件选择参数
    file_group = parser.add_mutually_exclusive_group(required=True)
    file_group.add_argument("--file", type=str, help="Specific JSON file to store")
    file_group.add_argument("--latest", action="store_true", help="Store the latest output file")
    
    # 可选参数
    parser.add_argument("--room-id", type=str, help="Room ID filter for latest file selection")
    parser.add_argument("--batch-id", type=str, help="Custom batch ID")
    
    args = parser.parse_args()
    
    try:
        # 确定要处理的文件
        if args.file:
            file_path = Path(args.file)
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                sys.exit(1)
        else:  # --latest
            file_path = find_latest_output_file(args.room_id)
            logger.info(f"Found latest file: {file_path}")
        
        # 存储文件
        storage_result = store_json_file(file_path, args.batch_id)
        
        logger.info("✅ Storage completed successfully!")
        logger.info(f"Batch ID: {storage_result['batch_id']}")
        logger.info(f"File: {storage_result['source_file']}")
        
        # 显示详细统计
        print("\n" + "="*60)
        print("STORAGE SUMMARY")
        print("="*60)
        print(f"Source File: {storage_result['source_file']}")
        print(f"File Size: {storage_result['file_size']:,} bytes")
        print(f"Room ID: {storage_result['room_id']}")
        print(f"Batch ID: {storage_result['batch_id']}")
        print(f"Analysis Time: {storage_result['analysis_time']}")
        print(f"Output Format: {storage_result['output_format']}")
        print(f"Static Configs Stored: {storage_result['static_configs_stored']}")
        print(f"Dynamic Results Stored: {storage_result['dynamic_results_stored']}")
        print(f"Total Points Processed: {storage_result['total_points_processed']}")
        print("="*60)
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Storage failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()