#!/usr/bin/env python3
"""
离线图像分析运行脚本
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vision.offline.processor import OfflineProcessor
from loguru import logger

def main():
    logger.info("启动离线图像分析任务...")
    
    try:
        processor = OfflineProcessor()
        # 执行批量处理
        processor.process_daily_batch(limit_per_room_day=2)
        logger.info("离线图像分析任务完成")
    except Exception as e:
        logger.error(f"任务执行失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
