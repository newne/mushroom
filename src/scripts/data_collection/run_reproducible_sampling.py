import argparse
import sys
import os
from datetime import datetime
from loguru import logger

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src"))

from src.data_collection.reproducible_sampler import ReproducibleSampler
from src.global_const.const_config import ROOM_ID_MAPPING

def main():
    # 默认使用 MinIO 中实际存在的 room_ids (7, 8, 611, 612)
    # 这里的逻辑是：ROOM_ID_MAPPING 的 keys 包含了 MinIO ID。
    # 根据用户指示，MinIO 中记录的是 7, 8, 611, 612。
    # 我们可以直接指定默认值，或者从配置中推断。
    # 鉴于 keys 中包含 "607" 映射到 "607"，为了避免混淆，我们使用硬编码的正确列表，或者基于用户输入。
    default_rooms = "7,8,611,612"
    
    parser = argparse.ArgumentParser(description="Reproducible Mushroom Image Sampler")
    parser.add_argument("--output_dir", type=str, required=True, help="Output directory for the dataset")
    parser.add_argument("--start_date", type=str, default="2025-12-19", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end_date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date (YYYY-MM-DD)")
    parser.add_argument("--rooms", type=str, default=default_rooms, help="Comma separated room IDs")
    parser.add_argument("--limit", type=int, default=2, help="Images per room per day")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    
    args = parser.parse_args()
    
    room_ids = [r.strip() for r in args.rooms.split(",")]
    
    # Setup logging
    logger.add(os.path.join(args.output_dir, "sampling.log"))
    
    sampler = ReproducibleSampler(
        output_dir=args.output_dir,
        room_ids=room_ids,
        start_date=args.start_date,
        end_date=args.end_date,
        limit_per_day=args.limit,
        seed=args.seed
    )
    
    sampler.sample()

if __name__ == "__main__":
    main()
