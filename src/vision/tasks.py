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
            
            target_date = (datetime.now() - timedelta(days=1)).date()
            logger.info(f"[CLIP_TASK] 处理目标日期: {target_date}")
            
            # 导入蘑菇图像编码器
            from vision.mushroom_image_encoder import create_mushroom_encoder
            
            # 创建编码器
            logger.info("[CLIP_TASK] 初始化蘑菇图像编码器...")
        """兼容入口：保留旧函数名（不再用于调度）"""
            
            # 统计所有库房的处理结果
            total_stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
            room_stats = {}
            
                logger.info(f"[CLIP_TASK] 开始执行图像编码任务 (兼容入口, 尝试 {attempt}/{max_retries})")
            for room_id in MUSHROOM_ROOM_IDS:
                try:
                    logger.info(f"[CLIP_TASK] 开始处理库房 {room_id} 的图像...")
                    
                    # 执行增量处理 - 处理指定库房最近1小时的图像
                    # 注意：这里不使用date_filter，而是依赖图像处理器的时间过滤逻辑
                    stats = encoder.batch_process_images(
                        mushroom_id=room_id,
                        date_filter=None,  # 不使用日期过滤，依赖时间范围过滤
                        start_time=start_time_filter,
                        end_time=end_time,
                        batch_size=CLIP_INFERENCE_BATCH_SIZE
                    )
                    
                    # 累计统计
                    for key in total_stats:
                stats = encoder.process_top_quality_embeddings_for_date(
                    target_date=target_date,
                    top_k=5,
                    batch_size=CLIP_INFERENCE_BATCH_SIZE,
                )
                    logger.info(f"[CLIP_TASK] 数据库总记录: {processing_stats.get('total_processed', 0)}")
                    logger.info(f"[CLIP_TASK] 有环境控制的记录: {processing_stats.get('with_environmental_control', 0)}")
                success_rate = (stats['success'] / stats['total']) * 100 if stats['total'] > 0 else 0
                logger.info(f"[CLIP_TASK] 图像编码任务完成，耗时: {duration:.2f}秒")
                logger.info(f"[CLIP_TASK] 总体统计: 总计={stats['total']}, 成功={stats['success']}, "
                           f"失败={stats['failed']}, 跳过={stats['skipped']}, 成功率={success_rate:.1f}%")
            return
                if stats['total'] > 0 and (stats['failed'] / stats['total']) > 0.1:
                    logger.warning(f"[CLIP_TASK] 处理失败率较高: {stats['failed']}/{stats['total']} = {(stats['failed']/stats['total']*100):.1f}%")
            error_msg = str(e)
                if stats['total'] == 0:
                    logger.info("[CLIP_TASK] 未找到可处理的Top质量图像")
            is_connection_error = any(keyword in error_msg.lower() for keyword in [
                return
            ])
                logger.error(f"[CLIP_TASK] 图像编码任务失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[CLIP_TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(f"[CLIP_TASK] CLIP推理任务失败，已达到最大重试次数 ({max_retries})")
                return
            else:


    def safe_hourly_text_quality_inference() -> None:
        """每小时文本编码与图像质量评估任务"""
        max_retries = CLIP_INFERENCE_MAX_RETRIES
        retry_delay = CLIP_INFERENCE_RETRY_DELAY

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[TEXT_QUALITY_TASK] 开始执行每小时文本/质量任务 (尝试 {attempt}/{max_retries})")
                end_time = datetime.now()
                start_time_filter = end_time - timedelta(hours=CLIP_INFERENCE_HOUR_LOOKBACK)

                from vision.mushroom_image_encoder import create_mushroom_encoder
                encoder = create_mushroom_encoder()

                total_stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}

                for room_id in MUSHROOM_ROOM_IDS:
                    try:
                        stats = encoder.batch_process_text_quality(
                            mushroom_id=room_id,
                            start_time=start_time_filter,
                            end_time=end_time,
                            batch_size=CLIP_INFERENCE_BATCH_SIZE
                        )
                        for key in total_stats:
                            total_stats[key] += stats.get(key, 0)
                    except Exception as e:
                        logger.error(f"[TEXT_QUALITY_TASK] 库房 {room_id} 处理失败: {e}")
                        total_stats['failed'] += 1

                logger.info(
                    f"[TEXT_QUALITY_TASK] 任务完成: 总计={total_stats['total']}, 成功={total_stats['success']}, "
                    f"失败={total_stats['failed']}, 跳过={total_stats['skipped']}"
                )
                return
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[TEXT_QUALITY_TASK] 任务失败 (尝试 {attempt}/{max_retries}): {error_msg}")
                is_connection_error = any(keyword in error_msg.lower() for keyword in [
                    'timeout', 'connection', 'connect', 'database', 'server'
                ])
                if is_connection_error and attempt < max_retries:
                    logger.warning(f"[TEXT_QUALITY_TASK] 检测到连接类错误，将在 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    logger.error("[TEXT_QUALITY_TASK] 任务失败且不再重试")
                    return


    def safe_daily_top_quality_clip_inference() -> None:
        """每天凌晨执行Top质量图像编码任务"""
        safe_hourly_clip_inference()
                logger.error(f"[CLIP_TASK] CLIP推理任务遇到非连接错误，不再重试")
                return