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


def _is_connection_error(error_msg: str) -> bool:
    return any(keyword in error_msg.lower() for keyword in [
        'timeout', 'connection', 'connect', 'database', 'server', 'redis', 'minio'
    ])


def safe_hourly_clip_inference() -> None:
    """兼容入口：保留旧函数名（不再用于调度）"""
    from vision.executor import safe_hourly_clip_inference as _safe_hourly

    _safe_hourly()


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
            if _is_connection_error(error_msg) and attempt < max_retries:
                logger.warning(f"[TEXT_QUALITY_TASK] 检测到连接类错误，将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            else:
                logger.error("[TEXT_QUALITY_TASK] 任务失败且不再重试")
                return


def safe_daily_top_quality_clip_inference() -> None:
    """每天凌晨执行Top质量图像编码任务"""
    max_retries = CLIP_INFERENCE_MAX_RETRIES
    retry_delay = CLIP_INFERENCE_RETRY_DELAY

    for attempt in range(1, max_retries + 1):
        try:
            target_date = (datetime.now() - timedelta(days=1)).date()
            logger.info(f"[TOP_QUALITY_TASK] 开始执行Top质量图像任务 (日期 {target_date}, 尝试 {attempt}/{max_retries})")

            from vision.mushroom_image_encoder import create_mushroom_encoder
            encoder = create_mushroom_encoder()

            stats = encoder.process_top_quality_embeddings_for_date(
                target_date=target_date,
                top_k=5,
                batch_size=CLIP_INFERENCE_BATCH_SIZE,
            )

            total = stats.get('total', 0)
            success = stats.get('success', 0)
            failed = stats.get('failed', 0)
            skipped = stats.get('skipped', 0)
            success_rate = (success / total) * 100 if total > 0 else 0

            logger.info(
                f"[TOP_QUALITY_TASK] 任务完成: 总计={total}, 成功={success}, 失败={failed}, 跳过={skipped}, "
                f"成功率={success_rate:.1f}%"
            )
            return
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TOP_QUALITY_TASK] 任务失败 (尝试 {attempt}/{max_retries}): {error_msg}")
            if _is_connection_error(error_msg) and attempt < max_retries:
                logger.warning(f"[TOP_QUALITY_TASK] 检测到连接类错误，将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            else:
                logger.error("[TOP_QUALITY_TASK] 任务失败且不再重试")
                return


__all__ = [
    "safe_hourly_clip_inference",
    "safe_hourly_text_quality_inference",
    "safe_daily_top_quality_clip_inference",
]