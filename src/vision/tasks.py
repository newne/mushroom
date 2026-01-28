"""
CLIP推理任务模块

负责蘑菇图像的CLIP推理处理相关的定时任务。
"""

import time
from datetime import datetime, timedelta

from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    CLIP_INFERENCE_MAX_RETRIES,
    CLIP_INFERENCE_RETRY_DELAY,
    CLIP_INFERENCE_BATCH_SIZE,
    CLIP_INFERENCE_HOUR_LOOKBACK,
)
from utils.loguru_setting import logger


def safe_hourly_clip_inference() -> None:
    """每小时CLIP推理任务 - 处理最近1小时内的新图像（带重试机制）"""
    max_retries = CLIP_INFERENCE_MAX_RETRIES
    retry_delay = CLIP_INFERENCE_RETRY_DELAY
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"[CLIP_TASK] 开始执行每小时CLIP推理任务 (尝试 {attempt}/{max_retries})")
            start_time = datetime.now()
            
            # 计算处理时间范围（最近1小时）
            end_time = datetime.now()
            start_time_filter = end_time - timedelta(hours=CLIP_INFERENCE_HOUR_LOOKBACK)
            
            logger.info(f"[CLIP_TASK] 处理时间范围: {start_time_filter.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 导入蘑菇图像编码器
            from vision.mushroom_image_encoder import create_mushroom_encoder
            
            # 创建编码器
            logger.info("[CLIP_TASK] 初始化蘑菇图像编码器...")
            encoder = create_mushroom_encoder()
            
            # 统计所有库房的处理结果
            total_stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
            room_stats = {}
            
            # 为每个库房处理最近1小时的图像
            for room_id in MUSHROOM_ROOM_IDS:
                try:
                    logger.info(f"[CLIP_TASK] 开始处理库房 {room_id} 的图像...")
                    
                    # 执行增量处理 - 处理指定库房最近1小时的图像
                    # 注意：这里不使用date_filter，而是依赖图像处理器的时间过滤逻辑
                    stats = encoder.batch_process_images(
                        mushroom_id=room_id,
                        date_filter=None,  # 不使用日期过滤，依赖时间范围过滤
                        batch_size=CLIP_INFERENCE_BATCH_SIZE
                    )
                    
                    # 累计统计
                    for key in total_stats:
                        total_stats[key] += stats.get(key, 0)
                    
                    room_stats[room_id] = stats
                    
                    logger.info(f"[CLIP_TASK] 库房 {room_id} 处理完成: "
                               f"总计={stats['total']}, 成功={stats['success']}, "
                               f"失败={stats['failed']}, 跳过={stats['skipped']}")
                    
                except Exception as e:
                    logger.error(f"[CLIP_TASK] 处理库房 {room_id} 时出错: {e}")
                    room_stats[room_id] = {'total': 0, 'success': 0, 'failed': 1, 'skipped': 0}
                    total_stats['failed'] += 1
            
            # 记录总体处理结果
            duration = (datetime.now() - start_time).total_seconds()
            success_rate = (total_stats['success'] / total_stats['total']) * 100 if total_stats['total'] > 0 else 0
            
            logger.info(f"[CLIP_TASK] 每小时CLIP推理任务完成，耗时: {duration:.2f}秒")
            logger.info(f"[CLIP_TASK] 总体统计: 总计={total_stats['total']}, 成功={total_stats['success']}, "
                       f"失败={total_stats['failed']}, 跳过={total_stats['skipped']}, 成功率={success_rate:.1f}%")
            
            # 记录各库房处理情况
            logger.info("[CLIP_TASK] 各库房处理统计:")
            for room_id in MUSHROOM_ROOM_IDS:
                stats = room_stats.get(room_id, {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0})
                logger.info(f"[CLIP_TASK]   库房{room_id}: 总计={stats['total']}, 成功={stats['success']}, "
                           f"失败={stats['failed']}, 跳过={stats['skipped']}")
            
            # 获取详细统计信息
            try:
                processing_stats = encoder.get_processing_statistics()
                if processing_stats:
                    logger.info(f"[CLIP_TASK] 数据库总记录: {processing_stats.get('total_processed', 0)}")
                    logger.info(f"[CLIP_TASK] 有环境控制的记录: {processing_stats.get('with_environmental_control', 0)}")
            except Exception as e:
                logger.warning(f"[CLIP_TASK] 获取处理统计失败: {e}")
            
            # 如果处理失败率过高，记录警告
            if total_stats['total'] > 0 and (total_stats['failed'] / total_stats['total']) > 0.1:
                logger.warning(f"[CLIP_TASK] 处理失败率较高: {total_stats['failed']}/{total_stats['total']} = {(total_stats['failed']/total_stats['total']*100):.1f}%")
            
            # 如果没有找到图像，记录信息
            if total_stats['total'] == 0:
                logger.info(f"[CLIP_TASK] 最近 {CLIP_INFERENCE_HOUR_LOOKBACK} 小时内未找到新图像数据")
            
            # 成功执行，退出重试循环
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[CLIP_TASK] 每小时CLIP推理任务失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                'timeout', 'connection', 'connect', 'database', 'server'
            ])
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[CLIP_TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[CLIP_TASK] CLIP推理任务失败，已达到最大重试次数 ({max_retries})")
                return
            else:
                logger.error(f"[CLIP_TASK] CLIP推理任务遇到非连接错误，不再重试")
                return