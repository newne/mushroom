#!/usr/bin/env python3
"""
运行safe_hourly_text_quality_inference查看最新的图文描述结果，结果不写入数据库
"""

import sys
from datetime import datetime, timedelta

from global_const.const_config import (
    CLIP_INFERENCE_HOUR_LOOKBACK,
    MUSHROOM_ROOM_IDS,
)
from utils.loguru_setting import logger
from vision.mushroom_image_encoder import create_mushroom_encoder


def run_text_quality_inference() -> None:
    """运行文本质量推断并打印结果，不写入数据库"""
    logger.info("[TEXT_QUALITY_TASK] 开始执行文本/质量任务（结果不写入数据库）")
    end_time = datetime.now()
    start_time_filter = end_time - timedelta(hours=CLIP_INFERENCE_HOUR_LOOKBACK)
    
    try:
        # 创建编码器实例，不加载CLIP模型（仅文本/质量分析）
        encoder = create_mushroom_encoder(load_clip=False)
        minio_rooms = set(encoder.minio_client.list_rooms())
        
        # 处理每个库房
        for room_id in MUSHROOM_ROOM_IDS:
            logger.info(f"[TEXT_QUALITY_TASK] 处理库房: {room_id}")
            
            # 获取该库房在时间范围内的图像
            all_images = encoder.processor.get_mushroom_images(
                mushroom_id=room_id,
                date_filter=None,
                start_time=start_time_filter,
                end_time=end_time,
            )
            
            if not all_images:
                logger.info(f"[TEXT_QUALITY_TASK] 库房 {room_id} 未找到符合条件的图像")
                continue
            
            logger.info(f"[TEXT_QUALITY_TASK] 库房 {room_id} 找到 {len(all_images)} 张图像")
            
            # 处理每张图像
            for i, image_info in enumerate(all_images):
                try:
                    logger.info(f"[TEXT_QUALITY_TASK] 处理图像 {i+1}/{len(all_images)}: {image_info.file_name}")
                    
                    # 从MinIO获取图像
                    image = encoder.minio_client.get_image(image_info.file_path)
                    if image is None:
                        logger.warning(f"[TEXT_QUALITY_TASK] 获取图像失败: {image_info.file_path}")
                        continue
                    
                    # 使用LLaMA模型获取描述和质量评分
                    llama_result = encoder._get_llama_description(image)
                    growth_stage_description = llama_result.get("growth_stage_description", "")
                    chinese_description = llama_result.get("chinese_description", None)
                    quality_score = llama_result.get("image_quality_score", None)
                    
                    # 打印结果
                    logger.info(f"[TEXT_QUALITY_TASK] 图像: {image_info.file_path}")
                    logger.info(f"[TEXT_QUALITY_TASK] 生长阶段描述: {growth_stage_description}")
                    logger.info(f"[TEXT_QUALITY_TASK] 中文描述: {chinese_description}")
                    logger.info(f"[TEXT_QUALITY_TASK] 图像质量评分: {quality_score}")
                    logger.info("[TEXT_QUALITY_TASK] " + "-" * 80)
                    
                except Exception as e:
                    logger.error(f"[TEXT_QUALITY_TASK] 处理图像失败 {image_info.file_name}: {e}")
                    continue
        
        logger.info("[TEXT_QUALITY_TASK] 任务完成")
        
    except Exception as e:
        logger.error(f"[TEXT_QUALITY_TASK] 任务失败: {e}")
        return


if __name__ == "__main__":
    run_text_quality_inference()