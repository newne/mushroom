"""
最近图片处理器
用于处理最近时间段内的图片数据，支持定期处理和增量处理
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from loguru import logger

from utils.minio_client import create_minio_client
from utils.mushroom_image_encoder import create_mushroom_encoder
from utils.mushroom_image_processor import MushroomImagePathParser


class RecentImageProcessor:
    """最近图片处理器"""
    
    def __init__(self, shared_encoder=None, shared_minio_client=None):
        """
        初始化处理器
        
        Args:
            shared_encoder: 共享的编码器实例，避免重复初始化
            shared_minio_client: 共享的MinIO客户端实例，避免重复初始化
        """
        # 使用共享实例或创建新实例
        self.minio_client = shared_minio_client or create_minio_client()
        self.encoder = shared_encoder or create_mushroom_encoder()
        self.parser = MushroomImagePathParser()
        
        # 缓存最近查询的图片数据，避免重复查询
        self._cached_images = None
        self._cache_timestamp = None
        self._cache_hours = None
        
        logger.info("Recent image processor initialized successfully")
    
    def _get_recent_images_cached(self, hours: int = 1, room_id: Optional[str] = None) -> List[Dict]:
        """
        获取最近图片数据，使用缓存避免重复查询
        
        Args:
            hours: 查询最近多少小时的数据
            room_id: 指定库房号，如果为None则查询所有库房
            
        Returns:
            图片数据列表
        """
        current_time = datetime.now()
        
        # 检查缓存是否有效（5分钟内的查询结果可以复用）
        cache_valid = (
            self._cached_images is not None and
            self._cache_timestamp is not None and
            self._cache_hours == hours and
            (current_time - self._cache_timestamp).total_seconds() < 300  # 5分钟缓存
        )
        
        if cache_valid:
            logger.debug(f"使用缓存的图片数据: {len(self._cached_images)} 张")
            cached_images = self._cached_images
        else:
            # 重新查询并缓存
            logger.debug(f"查询最近 {hours} 小时的图片数据")
            cached_images = self.minio_client.list_recent_images(hours=hours)
            self._cached_images = cached_images
            self._cache_timestamp = current_time
            self._cache_hours = hours
            logger.debug(f"缓存图片数据: {len(cached_images)} 张")
        
        # 如果指定了库房，进行过滤
        if room_id:
            filtered_images = [img for img in cached_images if img['room_id'] == room_id]
            logger.debug(f"库房 {room_id} 过滤后图片数量: {len(filtered_images)}")
            return filtered_images
        
        return cached_images
    
    def get_recent_image_summary_and_process(
        self, 
        hours: int = 1,
        room_ids: Optional[List[str]] = None,
        max_images_per_room: Optional[int] = None,
        save_to_db: bool = True,
        show_summary: bool = True
    ) -> Dict[str, Any]:
        """
        整合的方法：获取摘要并处理图片，避免重复查询
        
        Args:
            hours: 查询最近多少小时的数据
            room_ids: 指定库房列表，如果为None则处理所有库房
            max_images_per_room: 每个库房最多处理多少张图片
            save_to_db: 是否保存到数据库
            show_summary: 是否显示摘要信息
            
        Returns:
            包含摘要和处理结果的统计
        """
        logger.info(f"开始整合处理最近 {hours} 小时的图片数据")
        
        # 一次性获取所有图片数据
        recent_images = self._get_recent_images_cached(hours=hours)
        
        if not recent_images:
            logger.warning(f"未找到最近 {hours} 小时的图片")
            return {
                'summary': {
                    'total_images': 0,
                    'time_range': {
                        'start': datetime.now() - timedelta(hours=hours),
                        'end': datetime.now()
                    },
                    'room_stats': {}
                },
                'processing': {
                    'total_found': 0,
                    'total_processed': 0,
                    'total_success': 0,
                    'total_failed': 0,
                    'total_skipped': 0,
                    'room_stats': {}
                }
            }
        
        # 生成摘要信息
        summary = self._generate_summary(recent_images, hours)
        
        if show_summary:
            self._print_summary(summary)
        
        # 按库房分组并处理
        room_groups = {}
        for img in recent_images:
            room_id = img['room_id']
            
            # 库房过滤
            if room_ids and room_id not in room_ids:
                continue
                
            if room_id not in room_groups:
                room_groups[room_id] = []
            room_groups[room_id].append(img)
        
        logger.info(f"涉及库房: {sorted(room_groups.keys())}")
        
        # 处理统计
        processing_stats = {
            'total_found': len(recent_images),
            'total_processed': 0,
            'total_success': 0,
            'total_failed': 0,
            'total_skipped': 0,
            'room_stats': {}
        }
        
        # 处理每个库房的图片
        for room_id, images in room_groups.items():
            logger.info(f"处理库房 {room_id} 的图片: {len(images)} 张")
            
            # 按时间排序，处理最新的图片
            images.sort(key=lambda x: x['capture_time'], reverse=True)
            
            # 限制处理数量
            if max_images_per_room:
                images = images[:max_images_per_room]
                logger.info(f"限制库房 {room_id} 处理数量为: {len(images)} 张")
            
            room_stats = self._process_room_images(room_id, images, save_to_db)
            processing_stats['room_stats'][room_id] = room_stats
            
            # 更新总统计
            processing_stats['total_processed'] += room_stats['processed']
            processing_stats['total_success'] += room_stats['success']
            processing_stats['total_failed'] += room_stats['failed']
            processing_stats['total_skipped'] += room_stats['skipped']
        
        logger.info(f"最近 {hours} 小时图片处理完成 - 总计: 找到={processing_stats['total_found']}, "
                   f"处理={processing_stats['total_processed']}, 成功={processing_stats['total_success']}, "
                   f"失败={processing_stats['total_failed']}, 跳过={processing_stats['total_skipped']}")
        
        return {
            'summary': summary,
            'processing': processing_stats
        }
    
    def _generate_summary(self, recent_images: List[Dict], hours: int) -> Dict[str, Any]:
        """生成图片摘要信息"""
        # 按库房统计
        room_stats = {}
        for img in recent_images:
            room_id = img['room_id']
            if room_id not in room_stats:
                room_stats[room_id] = {
                    'count': 0,
                    'latest_time': None,
                    'earliest_time': None
                }
            
            room_stats[room_id]['count'] += 1
            
            capture_time = img['capture_time']
            if not room_stats[room_id]['latest_time'] or capture_time > room_stats[room_id]['latest_time']:
                room_stats[room_id]['latest_time'] = capture_time
            
            if not room_stats[room_id]['earliest_time'] or capture_time < room_stats[room_id]['earliest_time']:
                room_stats[room_id]['earliest_time'] = capture_time
        
        # 整体时间范围
        all_times = [img['capture_time'] for img in recent_images]
        time_range = {
            'start': min(all_times),
            'end': max(all_times)
        }
        
        return {
            'total_images': len(recent_images),
            'time_range': time_range,
            'room_stats': room_stats
        }
    
    def _print_summary(self, summary: Dict[str, Any]):
        """打印摘要信息"""
        print(f"总图片数: {summary['total_images']}")
        print(f"时间范围: {summary['time_range']['start']} ~ {summary['time_range']['end']}")
        print("各库房统计:")
        for room_id, stats in summary['room_stats'].items():
            print(f"库房{room_id}: {stats['count']}张 (最新: {stats['latest_time']})")
    
    def _process_room_images(self, room_id: str, images: List[Dict], save_to_db: bool) -> Dict[str, int]:
        """处理单个库房的图片"""
        room_stats = {
            'found': len(images),
            'processed': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }
        
        for img in images:
            try:
                # 解析图片路径
                image_info = self.parser.parse_path(img['object_name'])
                
                if not image_info:
                    logger.warning(f"无法解析图片路径: {img['object_name']}")
                    room_stats['failed'] += 1
                    continue
                
                # 检查是否已处理
                if save_to_db and self.encoder._is_already_processed(image_info.file_path):
                    logger.info(f"跳过已处理图片: {image_info.file_name}")
                    room_stats['skipped'] += 1
                    continue
                
                # 处理图片
                logger.info(f"处理图片: {image_info.file_name}")
                result = self.encoder.process_single_image(image_info, save_to_db=save_to_db)
                
                if result:
                    if result.get('saved_to_db', False):
                        room_stats['success'] += 1
                        logger.info(f"成功处理并保存: {image_info.file_name}")
                    elif result.get('skip_reason') == 'no_environment_data':
                        room_stats['success'] += 1  # 算作成功，只是没有环境数据
                        logger.info(f"成功处理但无环境数据: {image_info.file_name}")
                    else:
                        room_stats['failed'] += 1
                        logger.warning(f"处理失败: {image_info.file_name}")
                else:
                    room_stats['failed'] += 1
                    logger.error(f"处理返回None: {image_info.file_name}")
                
                room_stats['processed'] += 1
                
            except Exception as e:
                logger.error(f"处理图片异常 {img['object_name']}: {e}")
                room_stats['failed'] += 1
                room_stats['processed'] += 1
        
        logger.info(f"库房 {room_id} 处理完成: 处理={room_stats['processed']}, "
                   f"成功={room_stats['success']}, 失败={room_stats['failed']}, "
                   f"跳过={room_stats['skipped']}")
        
        return room_stats
    def process_recent_images(
        self, 
        hours: int = 1,
        room_ids: Optional[List[str]] = None,
        max_images_per_room: Optional[int] = None,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        处理最近指定小时内的图片（保持向后兼容）
        
        Args:
            hours: 查询最近多少小时的数据
            room_ids: 指定库房列表，如果为None则处理所有库房
            max_images_per_room: 每个库房最多处理多少张图片
            save_to_db: 是否保存到数据库
            
        Returns:
            处理结果统计
        """
        result = self.get_recent_image_summary_and_process(
            hours=hours,
            room_ids=room_ids,
            max_images_per_room=max_images_per_room,
            save_to_db=save_to_db,
            show_summary=False
        )
        return result['processing']
    
    def get_recent_image_summary(self, hours: int = 1) -> Dict[str, Any]:
        """
        获取最近图片的摘要信息（保持向后兼容）
        
        Args:
            hours: 查询最近多少小时的数据
            
        Returns:
            摘要信息
        """
        recent_images = self._get_recent_images_cached(hours=hours)
        
        if not recent_images:
            return {
                'total_images': 0,
                'time_range': {
                    'start': datetime.now() - timedelta(hours=hours),
                    'end': datetime.now()
                },
                'room_stats': {}
            }
        
        summary = self._generate_summary(recent_images, hours)
        logger.info(f"最近 {hours} 小时图片摘要: 总计 {len(recent_images)} 张, "
                   f"涉及库房 {sorted(summary['room_stats'].keys())}")
        
        return summary
    
    def process_room_recent_images(
        self,
        room_id: str,
        hours: int = 1,
        max_images: Optional[int] = None,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        处理指定库房最近的图片
        
        Args:
            room_id: 库房号
            hours: 查询最近多少小时的数据
            max_images: 最多处理多少张图片
            save_to_db: 是否保存到数据库
            
        Returns:
            处理结果统计
        """
        logger.info(f"处理库房 {room_id} 最近 {hours} 小时的图片")
        
        # 获取指定库房的最近图片
        recent_images = self.minio_client.list_recent_images(room_id=room_id, hours=hours)
        
        if not recent_images:
            logger.warning(f"库房 {room_id} 未找到最近 {hours} 小时的图片")
            return {
                'room_id': room_id,
                'found': 0,
                'processed': 0,
                'success': 0,
                'failed': 0,
                'skipped': 0
            }
        
        logger.info(f"库房 {room_id} 找到最近 {hours} 小时的图片: {len(recent_images)} 张")
        
        # 按时间排序，处理最新的图片
        recent_images.sort(key=lambda x: x['capture_time'], reverse=True)
        
        # 限制处理数量
        if max_images:
            recent_images = recent_images[:max_images]
            logger.info(f"限制库房 {room_id} 处理数量为: {len(recent_images)} 张")
        
        stats = {
            'room_id': room_id,
            'found': len(recent_images),
            'processed': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }
        
        for img in recent_images:
            try:
                # 解析图片路径
                image_info = self.parser.parse_path(img['object_name'])
                
                if not image_info:
                    logger.warning(f"无法解析图片路径: {img['object_name']}")
                    stats['failed'] += 1
                    continue
                
                # 检查是否已处理
                if save_to_db and self.encoder._is_already_processed(image_info.file_path):
                    logger.info(f"跳过已处理图片: {image_info.file_name}")
                    stats['skipped'] += 1
                    continue
                
                # 处理图片
                logger.info(f"处理图片: {image_info.file_name}")
                result = self.encoder.process_single_image(image_info, save_to_db=save_to_db)
                
                if result:
                    if result.get('saved_to_db', False):
                        stats['success'] += 1
                        logger.info(f"成功处理并保存: {image_info.file_name}")
                    elif result.get('skip_reason') == 'no_environment_data':
                        stats['success'] += 1  # 算作成功，只是没有环境数据
                        logger.info(f"成功处理但无环境数据: {image_info.file_name}")
                    else:
                        stats['failed'] += 1
                        logger.warning(f"处理失败: {image_info.file_name}")
                else:
                    stats['failed'] += 1
                    logger.error(f"处理返回None: {image_info.file_name}")
                
                stats['processed'] += 1
                
            except Exception as e:
                logger.error(f"处理图片异常 {img['object_name']}: {e}")
                stats['failed'] += 1
                stats['processed'] += 1
        
        logger.info(f"库房 {room_id} 处理完成: 找到={stats['found']}, 处理={stats['processed']}, "
                   f"成功={stats['success']}, 失败={stats['failed']}, 跳过={stats['skipped']}")
        
        return stats
    
    def get_recent_image_summary(self, hours: int = 1) -> Dict[str, Any]:
        """
        获取最近图片的摘要信息
        
        Args:
            hours: 查询最近多少小时的数据
            
        Returns:
            摘要信息
        """
        logger.info(f"获取最近 {hours} 小时的图片摘要")
        
        # 获取最近的图片
        recent_images = self.minio_client.list_recent_images(hours=hours)
        
        if not recent_images:
            return {
                'total_images': 0,
                'time_range': {
                    'start': datetime.now() - timedelta(hours=hours),
                    'end': datetime.now()
                },
                'room_stats': {}
            }
        
        # 按库房统计
        room_stats = {}
        for img in recent_images:
            room_id = img['room_id']
            if room_id not in room_stats:
                room_stats[room_id] = {
                    'count': 0,
                    'latest_time': None,
                    'earliest_time': None
                }
            
            room_stats[room_id]['count'] += 1
            
            capture_time = img['capture_time']
            if not room_stats[room_id]['latest_time'] or capture_time > room_stats[room_id]['latest_time']:
                room_stats[room_id]['latest_time'] = capture_time
            
            if not room_stats[room_id]['earliest_time'] or capture_time < room_stats[room_id]['earliest_time']:
                room_stats[room_id]['earliest_time'] = capture_time
        
        # 整体时间范围
        all_times = [img['capture_time'] for img in recent_images]
        time_range = {
            'start': min(all_times),
            'end': max(all_times)
        }
        
        summary = {
            'total_images': len(recent_images),
            'time_range': time_range,
            'room_stats': room_stats
        }
        
        logger.info(f"最近 {hours} 小时图片摘要: 总计 {len(recent_images)} 张, "
                   f"涉及库房 {sorted(room_stats.keys())}")
        
        return summary


def create_recent_image_processor(shared_encoder=None, shared_minio_client=None) -> RecentImageProcessor:
    """
    创建最近图片处理器实例
    
    Args:
        shared_encoder: 共享的编码器实例，避免重复初始化
        shared_minio_client: 共享的MinIO客户端实例，避免重复初始化
    """
    return RecentImageProcessor(shared_encoder=shared_encoder, shared_minio_client=shared_minio_client)


if __name__ == "__main__":
    # 测试代码 - 使用优化后的整合方法
    print("=== 初始化共享组件 ===")
    from utils.mushroom_image_encoder import create_mushroom_encoder
    from utils.minio_client import create_minio_client
    
    # 创建共享实例，避免重复初始化
    shared_encoder = create_mushroom_encoder()
    shared_minio_client = create_minio_client()
    
    processor = create_recent_image_processor(
        shared_encoder=shared_encoder,
        shared_minio_client=shared_minio_client
    )
    
    # 使用整合的方法：一次调用完成摘要和处理
    print("\n=== 整合处理最近1小时图片 ===")
    result = processor.get_recent_image_summary_and_process(
        hours=1,
        max_images_per_room=1,
        save_to_db=True,
        show_summary=True
    )
    
    print(f"\n处理结果: 找到={result['processing']['total_found']}, "
          f"处理={result['processing']['total_processed']}, "
          f"成功={result['processing']['total_success']}, "
          f"失败={result['processing']['total_failed']}, "
          f"跳过={result['processing']['total_skipped']}")
    
    print("各库房详情:")
    for room_id, stats in result['processing']['room_stats'].items():
        print(f"  库房{room_id}: 找到={stats['found']}, 处理={stats['processed']}, "
              f"成功={stats['success']}, 失败={stats['failed']}, 跳过={stats['skipped']}")