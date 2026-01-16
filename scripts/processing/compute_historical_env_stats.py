#!/usr/bin/env python3
"""
计算历史环境统计数据
从2024年12月19日到当前时间的所有日期环境统计
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from utils.env_data_processor import create_env_data_processor
from utils.loguru_setting import logger


def compute_historical_stats():
    """计算历史环境统计数据"""
    
    # 设置时间范围：从2024年12月19日到当前时间
    start_date = datetime(2024, 12, 19)
    end_date = datetime.now()
    
    logger.info(f"开始计算历史环境统计数据")
    logger.info(f"时间范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
    
    # 创建环境数据处理器
    try:
        processor = create_env_data_processor()
        logger.info("环境数据处理器创建成功")
    except Exception as e:
        logger.error(f"创建环境数据处理器失败: {e}")
        return False
    
    # 计算总天数
    total_days = (end_date - start_date).days + 1
    logger.info(f"需要处理 {total_days} 天的数据")
    
    # 执行批量计算
    try:
        logger.info("开始执行批量环境统计计算...")
        
        # 使用批量计算方法，传入时间范围
        processor.compute_and_store_daily_stats(
            start_time=start_date,
            end_time=end_date,
            rooms=None  # 自动推断所有房间
        )
        
        logger.info("✅ 历史环境统计计算完成")
        return True
        
    except Exception as e:
        logger.error(f"❌ 历史环境统计计算失败: {e}", exc_info=True)
        return False


def compute_daily_stats():
    """逐日计算环境统计数据（备用方法）"""
    
    # 设置时间范围
    start_date = datetime(2024, 12, 19)
    end_date = datetime.now()
    
    logger.info(f"开始逐日计算环境统计数据")
    logger.info(f"时间范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
    
    # 创建环境数据处理器
    try:
        processor = create_env_data_processor()
        logger.info("环境数据处理器创建成功")
    except Exception as e:
        logger.error(f"创建环境数据处理器失败: {e}")
        return False
    
    # 逐日处理
    current_date = start_date
    success_count = 0
    error_count = 0
    
    while current_date <= end_date:
        try:
            logger.info(f"处理日期: {current_date.strftime('%Y-%m-%d')}")
            
            # 计算单日统计
            processor.compute_and_store_daily_stats(
                start_time=current_date,
                end_time=None,  # None表示只计算当天
                rooms=None  # 自动推断所有房间
            )
            
            success_count += 1
            logger.info(f"✅ {current_date.strftime('%Y-%m-%d')} 处理成功")
            
        except Exception as e:
            error_count += 1
            logger.error(f"❌ {current_date.strftime('%Y-%m-%d')} 处理失败: {e}")
        
        # 移动到下一天
        current_date += timedelta(days=1)
    
    logger.info(f"逐日计算完成: 成功 {success_count} 天, 失败 {error_count} 天")
    return error_count == 0


def main():
    """主函数"""
    logger.info("=== 历史环境统计计算工具 ===")
    
    # 首先尝试批量计算
    logger.info("方法1: 尝试批量计算...")
    if compute_historical_stats():
        logger.info("🎉 批量计算成功完成")
        return
    
    # 如果批量计算失败，尝试逐日计算
    logger.warning("批量计算失败，尝试逐日计算...")
    logger.info("方法2: 逐日计算...")
    if compute_daily_stats():
        logger.info("🎉 逐日计算成功完成")
    else:
        logger.error("💥 所有计算方法都失败了")
        sys.exit(1)


if __name__ == "__main__":
    main()