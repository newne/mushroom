"""
CLIP推理任务执行器

专门负责蘑菇图像的CLIP推理处理。
"""

from datetime import datetime, timedelta
from typing import Dict, Any

from tasks.base_task import BaseTask
from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    CLIP_INFERENCE_MAX_RETRIES,
    CLIP_INFERENCE_RETRY_DELAY,
    CLIP_INFERENCE_BATCH_SIZE,
    CLIP_INFERENCE_HOUR_LOOKBACK,
)
from utils.loguru_setting import logger


class CLIPInferenceTask(BaseTask):
    """CLIP推理任务执行器"""
    
    def __init__(self):
        """初始化CLIP推理任务"""
        super().__init__(
            task_name="CLIP_INFERENCE",
            max_retries=CLIP_INFERENCE_MAX_RETRIES,
            retry_delay=CLIP_INFERENCE_RETRY_DELAY
        )
        
        self.rooms = MUSHROOM_ROOM_IDS
        self.batch_size = CLIP_INFERENCE_BATCH_SIZE
        self.hour_lookback = CLIP_INFERENCE_HOUR_LOOKBACK
    
    def execute_task(self) -> Dict[str, Any]:
        """
        执行每小时CLIP推理任务 - 处理最近1小时内的新图像
        
        Returns:
            Dict[str, Any]: 执行结果
        """
        # 计算处理时间范围（最近1小时）
        end_time = datetime.now()
        start_time_filter = end_time - timedelta(hours=self.hour_lookback)
        
        logger.info(f"[{self.task_name}] 处理时间范围: {start_time_filter.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 导入蘑菇图像编码器
        try:
            from clip.mushroom_image_encoder import create_mushroom_encoder
        except ImportError as e:
            logger.error(f"[{self.task_name}] 导入图像编码器失败: {e}")
            raise
        
        # 创建编码器
        logger.info(f"[{self.task_name}] 初始化蘑菇图像编码器...")
        encoder = create_mushroom_encoder()
        
        # 统计所有库房的处理结果
        total_stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
        room_stats = {}
        
        # 为每个库房处理最近1小时的图像
        for room_id in self.rooms:
            try:
                logger.info(f"[{self.task_name}] 开始处理库房 {room_id} 的图像...")
                
                # 执行增量处理 - 处理指定库房最近1小时的图像
                # 注意：这里不使用date_filter，而是依赖图像处理器的时间过滤逻辑
                stats = encoder.batch_process_images(
                    mushroom_id=room_id,
                    date_filter=None,  # 不使用日期过滤，依赖时间范围过滤
                    batch_size=self.batch_size
                )
                
                # 累计统计
                for key in total_stats:
                    total_stats[key] += stats.get(key, 0)
                
                room_stats[room_id] = stats
                
                logger.info(f"[{self.task_name}] 库房 {room_id} 处理完成: "
                           f"总计={stats['total']}, 成功={stats['success']}, "
                           f"失败={stats['failed']}, 跳过={stats['skipped']}")
                
            except Exception as e:
                logger.error(f"[{self.task_name}] 处理库房 {room_id} 时出错: {e}")
                room_stats[room_id] = {'total': 0, 'success': 0, 'failed': 1, 'skipped': 0}
                total_stats['failed'] += 1
        
        # 计算成功率
        success_rate = (total_stats['success'] / total_stats['total']) * 100 if total_stats['total'] > 0 else 0
        
        logger.info(f"[{self.task_name}] 总体统计: 总计={total_stats['total']}, 成功={total_stats['success']}, "
                   f"失败={total_stats['failed']}, 跳过={total_stats['skipped']}, 成功率={success_rate:.1f}%")
        
        # 记录各库房处理情况
        logger.info(f"[{self.task_name}] 各库房处理统计:")
        for room_id in self.rooms:
            stats = room_stats.get(room_id, {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0})
            logger.info(f"[{self.task_name}]   库房{room_id}: 总计={stats['total']}, 成功={stats['success']}, "
                       f"失败={stats['failed']}, 跳过={stats['skipped']}")
        
        # 获取详细统计信息
        processing_stats = None
        try:
            processing_stats = encoder.get_processing_statistics()
            if processing_stats:
                logger.info(f"[{self.task_name}] 数据库总记录: {processing_stats.get('total_processed', 0)}")
                logger.info(f"[{self.task_name}] 有环境控制的记录: {processing_stats.get('with_environmental_control', 0)}")
        except Exception as e:
            logger.warning(f"[{self.task_name}] 获取处理统计失败: {e}")
        
        # 如果处理失败率过高，记录警告
        if total_stats['total'] > 0 and (total_stats['failed'] / total_stats['total']) > 0.1:
            logger.warning(f"[{self.task_name}] 处理失败率较高: {total_stats['failed']}/{total_stats['total']} = {(total_stats['failed']/total_stats['total']*100):.1f}%")
        
        # 如果没有找到图像，记录信息
        if total_stats['total'] == 0:
            logger.info(f"[{self.task_name}] 最近 {self.hour_lookback} 小时内未找到新图像数据")
        
        return self._create_success_result(
            total_rooms=len(self.rooms),
            processing_period=f"{start_time_filter} ~ {end_time}",
            total_stats=total_stats,
            room_stats=room_stats,
            success_rate=success_rate,
            processing_statistics=processing_stats,
            batch_size=self.batch_size,
            hour_lookback=self.hour_lookback
        )
    
    def get_inference_summary(self, days: int = 7) -> Dict[str, Any]:
        """
        获取CLIP推理摘要
        
        Args:
            days: 查询天数
            
        Returns:
            Dict[str, Any]: 推理摘要
        """
        try:
            from global_const.global_const import pgsql_engine
            from sqlalchemy import text
            
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days)
            
            summary = {
                'query_period': f"{start_date} to {end_date}",
                'total_days': days,
                'rooms_summary': {},
                'overall_stats': {}
            }
            
            with pgsql_engine.connect() as conn:
                # 查询各库房推理记录数
                for room_id in self.rooms:
                    result = conn.execute(text("""
                        SELECT 
                            COUNT(*) as total_records,
                            COUNT(CASE WHEN image_embedding IS NOT NULL THEN 1 END) as with_embedding,
                            COUNT(CASE WHEN environmental_control IS NOT NULL THEN 1 END) as with_env_control,
                            AVG(image_quality_index) as avg_quality
                        FROM mushroom_embedding 
                        WHERE mushroom_id = :room_id 
                        AND DATE(created_at) BETWEEN :start_date AND :end_date
                    """), {
                        "room_id": room_id,
                        "start_date": start_date,
                        "end_date": end_date
                    })
                    
                    row = result.fetchone()
                    if row:
                        summary['rooms_summary'][room_id] = {
                            'total_records': row[0],
                            'with_embedding': row[1],
                            'with_env_control': row[2],
                            'avg_quality': round(row[3], 2) if row[3] else None,
                            'embedding_rate': (row[1] / row[0]) * 100 if row[0] > 0 else 0
                        }
                
                # 查询总体统计
                result = conn.execute(text("""
                    SELECT 
                        COUNT(*) as total_records,
                        COUNT(DISTINCT mushroom_id) as rooms_with_data,
                        COUNT(CASE WHEN image_embedding IS NOT NULL THEN 1 END) as total_with_embedding,
                        AVG(image_quality_index) as overall_avg_quality
                    FROM mushroom_embedding 
                    WHERE DATE(created_at) BETWEEN :start_date AND :end_date
                """), {
                    "start_date": start_date,
                    "end_date": end_date
                })
                
                row = result.fetchone()
                if row:
                    summary['overall_stats'] = {
                        'total_records': row[0],
                        'rooms_with_data': row[1],
                        'total_with_embedding': row[2],
                        'overall_avg_quality': round(row[3], 2) if row[3] else None,
                        'overall_embedding_rate': (row[2] / row[0]) * 100 if row[0] > 0 else 0
                    }
            
            return summary
            
        except Exception as e:
            logger.error(f"[{self.task_name}] 获取推理摘要失败: {e}")
            return {
                'error': str(e),
                'query_time': datetime.now().isoformat()
            }
    
    def validate_inference_quality(self, room_id: str, hours: int = 24) -> Dict[str, Any]:
        """
        验证推理质量
        
        Args:
            room_id: 库房编号
            hours: 验证小时数
            
        Returns:
            Dict[str, Any]: 验证结果
        """
        try:
            from global_const.global_const import pgsql_engine
            from sqlalchemy import text
            
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            with pgsql_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT 
                        COUNT(*) as total_records,
                        COUNT(CASE WHEN image_embedding IS NOT NULL THEN 1 END) as with_embedding,
                        COUNT(CASE WHEN image_quality_index >= 0.7 THEN 1 END) as high_quality,
                        COUNT(CASE WHEN image_quality_index < 0.3 THEN 1 END) as low_quality,
                        AVG(image_quality_index) as avg_quality,
                        MIN(image_quality_index) as min_quality,
                        MAX(image_quality_index) as max_quality
                    FROM mushroom_embedding 
                    WHERE mushroom_id = :room_id 
                    AND created_at BETWEEN :start_time AND :end_time
                """), {
                    "room_id": room_id,
                    "start_time": start_time,
                    "end_time": end_time
                })
                
                row = result.fetchone()
                if row:
                    total_records = row[0]
                    validation_result = {
                        'room_id': room_id,
                        'validation_period': f"{start_time} to {end_time}",
                        'total_records': total_records,
                        'embedding_completeness': row[1] / total_records if total_records > 0 else 0,
                        'quality_distribution': {
                            'high_quality': row[2],
                            'low_quality': row[3],
                            'high_quality_rate': row[2] / total_records if total_records > 0 else 0,
                            'low_quality_rate': row[3] / total_records if total_records > 0 else 0
                        },
                        'quality_stats': {
                            'average': round(row[4], 3) if row[4] else None,
                            'minimum': round(row[5], 3) if row[5] else None,
                            'maximum': round(row[6], 3) if row[6] else None
                        }
                    }
                    
                    # 计算质量分数
                    embedding_score = validation_result['embedding_completeness'] * 50
                    quality_score = (1 - validation_result['quality_distribution']['low_quality_rate']) * 50
                    validation_result['overall_quality_score'] = embedding_score + quality_score
                    
                    return validation_result
                else:
                    return {
                        'room_id': room_id,
                        'error': 'No data found for validation period'
                    }
            
        except Exception as e:
            logger.error(f"[{self.task_name}] 验证推理质量失败: {e}")
            return {
                'room_id': room_id,
                'error': str(e)
            }


# 创建全局实例
clip_inference_task = CLIPInferenceTask()


def safe_hourly_clip_inference() -> None:
    """
    每小时CLIP推理任务（兼容原接口）
    """
    result = clip_inference_task.run()
    
    if not result.get('success', False):
        logger.error(f"[CLIP_TASK] CLIP推理任务失败: {result.get('error', '未知错误')}")
    else:
        logger.info(f"[CLIP_TASK] CLIP推理任务成功完成")


def get_clip_inference_summary(days: int = 7) -> Dict[str, Any]:
    """
    获取CLIP推理摘要（兼容原接口）
    
    Args:
        days: 查询天数
        
    Returns:
        Dict[str, Any]: 推理摘要
    """
    return clip_inference_task.get_inference_summary(days)


def validate_clip_quality(room_id: str, hours: int = 24) -> Dict[str, Any]:
    """
    验证CLIP推理质量
    
    Args:
        room_id: 库房编号
        hours: 验证小时数
        
    Returns:
        Dict[str, Any]: 验证结果
    """
    return clip_inference_task.validate_inference_quality(room_id, hours)