import argparse
import sys
import os
import pandas as pd
from pathlib import Path
from PIL import Image
from loguru import logger

def verify_dataset(dataset_dir: str, limit_per_day: int = 2):
    dataset_path = Path(dataset_dir)
    manifest_path = dataset_path / "manifest.csv"
    
    if not manifest_path.exists():
        logger.error(f"Manifest not found at {manifest_path}")
        return False
        
    logger.info(f"Verifying dataset at {dataset_dir}")
    
    df = pd.read_csv(manifest_path)
    
    # 1. 验证文件完整性和可读性
    valid_count = 0
    invalid_count = 0
    
    for idx, row in df.iterrows():
        rel_path = row['image_path']
        full_path = dataset_path / rel_path
        
        if not full_path.exists():
            logger.error(f"Missing file: {full_path}")
            invalid_count += 1
            continue
            
        try:
            with Image.open(full_path) as img:
                img.verify() # 验证图像完整性
            valid_count += 1
        except Exception as e:
            logger.error(f"Corrupt image {full_path}: {e}")
            invalid_count += 1
            
    logger.info(f"File check complete. Valid: {valid_count}, Invalid: {invalid_count}")
    
    # 2. 验证每日每库房数量
    # 统计 (date, room_id) 的数量
    stats = df.groupby(['date', 'room_id']).size().reset_index(name='count')
    
    missing_or_insufficient = 0
    exact_match = 0
    
    # 这里的 date 是 manifest 中的 date，可能不全（如果某天没采到）
    # 我们遍历统计结果
    for _, row in stats.iterrows():
        date = row['date']
        room = row['room_id']
        count = row['count']
        
        if count != limit_per_day:
            logger.warning(f"Mismatch for Room {room} on {date}: Found {count}, Expected {limit_per_day}")
            missing_or_insufficient += 1
        else:
            exact_match += 1
            
    logger.info(f"Group check complete. Exact matches: {exact_match}, Mismatches: {missing_or_insufficient}")
    
    if invalid_count == 0:
        logger.info("SUCCESS: All files are valid.")
    else:
        logger.error("FAILURE: Some files are invalid.")
        
    return invalid_count == 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_dir", type=str, required=True)
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    
    verify_dataset(args.dataset_dir, args.limit)
