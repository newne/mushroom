import sys
import os
from datetime import datetime

# 添加项目根目录到 python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src"))

from src.vision.offline.processor import OfflineProcessor
from loguru import logger

def main():
    # 设置日志
    logger.add("logs/offline_processing_{time}.log")
    
    start_date = "2025-12-22"
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f"Starting offline image processing task from {start_date} to {end_date}")
    
    processor = OfflineProcessor()
    
    # 运行处理任务
    # 默认 limit_per_room_day=2，如果需要更多可以调整
    processor.process_daily_batch(
        limit_per_room_day=2,
        start_date=start_date,
        end_date=end_date
    )
    
    logger.info("Offline image processing task completed")

if __name__ == "__main__":
    main()
