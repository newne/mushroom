"""
环境相关定时任务
"""
from datetime import date, timedelta
from global_const.const_config import MUSHROOM_ROOM_IDS
from utils.loguru_setting import logger
from .processor import process_daily_env_stats

def safe_daily_env_stats() -> None:
    """
    每日环境统计任务
    
    功能:
    1. 遍历所有蘑菇房
    2. 计算前一天的环境统计数据
    3. 存储到数据库
    """
    try:
        logger.info("[ENV_TASK] 开始执行每日环境统计任务")
        
        # 统计前一天的数据
        stat_date = date.today() - timedelta(days=1)
        
        success_count = 0
        failed_count = 0
        
        for room_id in MUSHROOM_ROOM_IDS:
            try:
                result = process_daily_env_stats(room_id, stat_date)
                if result.get('success', False):
                    success_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(f"[ENV_TASK] 处理库房 {room_id} 失败: {e}")
                failed_count += 1
                
        logger.info(f"[ENV_TASK] 任务完成 - 成功: {success_count}, 失败: {failed_count}")
        
    except Exception as e:
        logger.error(f"[ENV_TASK] 任务执行异常: {e}")
