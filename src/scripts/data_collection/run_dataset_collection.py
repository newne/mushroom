import sys
import os
import argparse
from datetime import datetime
from pathlib import Path
from loguru import logger
import pandas as pd
from sqlalchemy import text

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src"))

from src.data_collection.collector import DatasetCollector
from src.global_const.global_const import pgsql_engine
from src.global_const.const_config import ROOM_ID_MAPPING

def get_room_ids():
    """尝试从数据库获取库房列表，失败则返回默认列表"""
    # 更新为正确的默认列表：7, 8, 611, 612
    # 这些是 MinIO 中实际使用的 ID
    default_rooms = ['7', '8', '611', '612']
    try:
        with pgsql_engine.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT room_id FROM mushroom_embedding")).fetchall()
            rooms = [str(row[0]) for row in result]
            if rooms:
                logger.info(f"Found rooms in database: {rooms}")
                # 过滤出我们在 MAPPING 中关注的或者符合预期的 ID
                # 这里简单返回所有，但在实际使用中可能需要根据 default_rooms 进行校验
                return rooms
    except Exception as e:
        logger.warning(f"Failed to fetch room IDs from DB, using default: {e}")
    
    return default_rooms

def generate_report(output_dir: Path, dataset_version: str):
    """生成采集报告"""
    manifest_path = output_dir / "manifest.csv"
    if not manifest_path.exists():
        logger.error("Manifest file not found, cannot generate report.")
        return

    df = pd.read_csv(manifest_path)
    
    report_path = output_dir / "DATASET_README.md"
    
    total_images = len(df)
    date_range_start = df['collection_date'].min() if not df.empty else "N/A"
    date_range_end = df['collection_date'].max() if not df.empty else "N/A"
    
    stats_by_room = df.groupby('room_id').size().to_frame('count')
    
    with open(report_path, 'w') as f:
        f.write(f"# Mushroom Offline Dataset {dataset_version}\n\n")
        f.write(f"**Generated Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## Overview\n")
        f.write(f"- **Total Images:** {total_images}\n")
        f.write(f"- **Date Range:** {date_range_start} to {date_range_end}\n")
        f.write(f"- **Rooms:** {', '.join(map(str, stats_by_room.index.tolist()))}\n\n")
        
        f.write("## Statistics by Room\n")
        f.write(stats_by_room.to_markdown())
        f.write("\n\n")
        
        f.write("## Directory Structure\n")
        f.write("```\n")
        f.write("dataset_root/\n")
        f.write("  ├── manifest.csv          # Metadata index\n")
        f.write("  ├── DATASET_README.md     # This file\n")
        f.write("  └── <room_id>/\n")
        f.write("      └── <date>/\n")
        f.write("          └── <filename>\n")
        f.write("```\n\n")
        
        f.write("## Usage\n")
        f.write("Use `manifest.csv` to load the dataset. Do NOT query the raw database or MinIO for experiments.\n")

    logger.info(f"Report generated at {report_path}")

def main():
    parser = argparse.ArgumentParser(description="Mushroom Dataset Collector")
    parser.add_argument("--output_dir", type=str, default="data/mushroom_offline_dataset_v1", help="Output directory")
    parser.add_argument("--start_date", type=str, default="2025-12-22", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end_date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=2, help="Images per room per day")
    
    args = parser.parse_args()
    
    logger.add(os.path.join(args.output_dir, "collection.log"))
    
    room_ids = get_room_ids()
    
    collector = DatasetCollector(
        output_dir=args.output_dir,
        room_ids=room_ids,
        start_date=args.start_date,
        end_date=args.end_date,
        limit_per_day=args.limit
    )
    
    collector.collect()
    
    generate_report(Path(args.output_dir), "v1.0")

if __name__ == "__main__":
    main()
