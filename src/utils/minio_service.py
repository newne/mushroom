"""
MinIO服务类
提供高级的MinIO操作接口，集成到项目的整体架构中
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from loguru import logger

from utils.minio_client import MinIOClient


class MinIOService:
    """MinIO服务类，提供高级操作接口"""
    
    def __init__(self):
        """初始化MinIO服务"""
        self.client = MinIOClient()
        self.bucket_name = self.client.config['bucket']
        
    def get_image_dataset(self, prefix: str = "", 
                         image_extensions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        获取图片数据集信息
        
        Args:
            prefix: 文件前缀过滤
            image_extensions: 支持的图片扩展名列表
            
        Returns:
            图片数据集信息列表
        """
        if image_extensions is None:
            image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']
        
        try:
            # 获取所有对象
            objects = self.client.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
            dataset = []
            
            for obj in objects:
                file_ext = os.path.splitext(obj.object_name)[1].lower()
                if file_ext in image_extensions:
                    # 获取图片信息
                    info = self.client.get_image_info(obj.object_name)
                    if info:
                        dataset_item = {
                            'object_name': obj.object_name,
                            'file_name': os.path.basename(obj.object_name),
                            'file_path': obj.object_name,
                            'size': info['size'],
                            'last_modified': info['last_modified'],
                            'content_type': info['content_type'],
                            'extension': file_ext,
                            'bucket': self.bucket_name
                        }
                        dataset.append(dataset_item)
            
            logger.info(f"获取图片数据集完成，共 {len(dataset)} 张图片")
            return dataset
            
        except Exception as e:
            logger.error(f"获取图片数据集失败: {e}")
            return []
    
    def get_images_by_category(self, category_mapping: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
        """
        根据文件路径模式对图片进行分类
        
        Args:
            category_mapping: 分类映射，格式为 {'category_name': 'path_pattern'}
            
        Returns:
            分类后的图片字典
        """
        all_images = self.get_image_dataset()
        categorized_images = {category: [] for category in category_mapping.keys()}
        categorized_images['uncategorized'] = []
        
        for image_info in all_images:
            categorized = False
            for category, pattern in category_mapping.items():
                if pattern in image_info['file_path']:
                    categorized_images[category].append(image_info)
                    categorized = True
                    break
            
            if not categorized:
                categorized_images['uncategorized'].append(image_info)
        
        # 记录分类统计
        for category, images in categorized_images.items():
            logger.info(f"分类 '{category}': {len(images)} 张图片")
        
        return categorized_images
    
    def batch_download_images(self, image_list: List[str], 
                            local_dir: str = "downloads") -> List[Tuple[str, bool]]:
        """
        批量下载图片
        
        Args:
            image_list: 图片对象名称列表
            local_dir: 本地保存目录
            
        Returns:
            下载结果列表，格式为 [(object_name, success), ...]
        """
        local_path = Path(local_dir)
        local_path.mkdir(parents=True, exist_ok=True)
        
        results = []
        
        for object_name in image_list:
            try:
                # 创建本地文件路径
                local_file_path = local_path / os.path.basename(object_name)
                
                # 下载文件
                self.client.client.fget_object(self.bucket_name, object_name, str(local_file_path))
                results.append((object_name, True))
                logger.info(f"下载成功: {object_name} -> {local_file_path}")
                
            except Exception as e:
                results.append((object_name, False))
                logger.error(f"下载失败 {object_name}: {e}")
        
        success_count = sum(1 for _, success in results if success)
        logger.info(f"批量下载完成: {success_count}/{len(image_list)} 成功")
        
        return results
    
    def create_image_manifest(self, output_path: str = "image_manifest.json") -> bool:
        """
        创建图片清单文件
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否创建成功
        """
        try:
            dataset = self.get_image_dataset()
            
            manifest = {
                'created_at': datetime.now().isoformat(),
                'environment': self.client.environment,
                'bucket': self.bucket_name,
                'endpoint': self.client.config['endpoint'],
                'total_images': len(dataset),
                'images': dataset
            }
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"图片清单创建成功: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"创建图片清单失败: {e}")
            return False
    
    def get_image_statistics(self) -> Dict[str, Any]:
        """
        获取图片统计信息
        
        Returns:
            统计信息字典
        """
        try:
            dataset = self.get_image_dataset()
            
            if not dataset:
                return {'total_images': 0}
            
            # 基本统计
            total_images = len(dataset)
            total_size = sum(img['size'] for img in dataset)
            
            # 按扩展名统计
            ext_stats = {}
            for img in dataset:
                ext = img['extension']
                if ext not in ext_stats:
                    ext_stats[ext] = {'count': 0, 'size': 0}
                ext_stats[ext]['count'] += 1
                ext_stats[ext]['size'] += img['size']
            
            # 按大小分组
            size_ranges = {
                'small': 0,    # < 100KB
                'medium': 0,   # 100KB - 1MB
                'large': 0,    # 1MB - 10MB
                'xlarge': 0    # > 10MB
            }
            
            for img in dataset:
                size_mb = img['size'] / (1024 * 1024)
                if size_mb < 0.1:
                    size_ranges['small'] += 1
                elif size_mb < 1:
                    size_ranges['medium'] += 1
                elif size_mb < 10:
                    size_ranges['large'] += 1
                else:
                    size_ranges['xlarge'] += 1
            
            statistics = {
                'total_images': total_images,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'average_size_bytes': round(total_size / total_images, 2),
                'extension_stats': ext_stats,
                'size_distribution': size_ranges,
                'environment': self.client.environment,
                'bucket': self.bucket_name
            }
            
            logger.info(f"图片统计完成: {total_images} 张图片，总大小 {statistics['total_size_mb']} MB")
            return statistics
            
        except Exception as e:
            logger.error(f"获取图片统计失败: {e}")
            return {}
    
    def process_images_with_callback(self, callback_func, 
                                   prefix: str = "", 
                                   batch_size: int = 10) -> List[Any]:
        """
        使用回调函数批量处理图片
        
        Args:
            callback_func: 处理函数，接收 (image_object, image_info) 参数
            prefix: 文件前缀过滤
            batch_size: 批处理大小
            
        Returns:
            处理结果列表
        """
        try:
            image_files = self.client.list_images(prefix=prefix)
            results = []
            
            for i in range(0, len(image_files), batch_size):
                batch = image_files[i:i + batch_size]
                logger.info(f"处理批次 {i//batch_size + 1}: {len(batch)} 张图片")
                
                for object_name in batch:
                    try:
                        # 获取图片对象和信息
                        image = self.client.get_image(object_name)
                        image_info = self.client.get_image_info(object_name)
                        
                        if image and image_info:
                            # 调用回调函数
                            result = callback_func(image, image_info)
                            results.append({
                                'object_name': object_name,
                                'result': result,
                                'success': True
                            })
                        else:
                            results.append({
                                'object_name': object_name,
                                'result': None,
                                'success': False,
                                'error': 'Failed to load image or info'
                            })
                            
                    except Exception as e:
                        results.append({
                            'object_name': object_name,
                            'result': None,
                            'success': False,
                            'error': str(e)
                        })
                        logger.error(f"处理图片失败 {object_name}: {e}")
            
            success_count = sum(1 for r in results if r['success'])
            logger.info(f"批量处理完成: {success_count}/{len(results)} 成功")
            
            return results
            
        except Exception as e:
            logger.error(f"批量处理图片失败: {e}")
            return []
    
    def health_check(self) -> Dict[str, Any]:
        """
        健康检查
        
        Returns:
            健康检查结果
        """
        health_status = {
            'timestamp': datetime.now().isoformat(),
            'environment': self.client.environment,
            'endpoint': self.client.config['endpoint'],
            'bucket': self.bucket_name,
            'connection': False,
            'bucket_exists': False,
            'image_count': 0,
            'errors': []
        }
        
        try:
            # 测试连接
            if self.client.test_connection():
                health_status['connection'] = True
            else:
                health_status['errors'].append('Connection test failed')
            
            # 检查存储桶
            if self.client.client.bucket_exists(self.bucket_name):
                health_status['bucket_exists'] = True
                
                # 统计图片数量
                images = self.client.list_images()
                health_status['image_count'] = len(images)
            else:
                health_status['errors'].append(f'Bucket {self.bucket_name} does not exist')
            
        except Exception as e:
            health_status['errors'].append(f'Health check error: {str(e)}')
        
        health_status['healthy'] = len(health_status['errors']) == 0
        
        return health_status


def create_minio_service() -> MinIOService:
    """创建MinIO服务实例"""
    return MinIOService()


if __name__ == "__main__":
    # 测试代码
    service = create_minio_service()
    
    # 健康检查
    health = service.health_check()
    print(f"健康检查: {health}")
    
    # 获取统计信息
    stats = service.get_image_statistics()
    print(f"图片统计: {stats}")