"""
MinIO客户端工具类
用于连接MinIO存储服务并进行文件操作
"""

import io
import os
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from PIL import Image
from loguru import logger
from minio import Minio

from global_const.global_const import settings


class MinIOClient:
    """MinIO客户端类"""
    
    def __init__(self):
        """
        初始化MinIO客户端
        """
        self.config = self._load_config()
        self.client = self._create_client()
        
    def _load_config(self) -> Dict[str, Any]:
        """从全局settings加载配置"""
        try:
            # 使用全局settings获取MinIO配置
            # Dynaconf将配置键转换为大写
            minio_config = settings.MINIO
            
            if not minio_config:
                raise ValueError(f"未找到MinIO配置")
            # 配置已经是字典格式，直接返回
            return minio_config
            
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            raise
    
    def _create_client(self) -> Minio:
        """创建MinIO客户端"""
        try:
            # 从endpoint中提取host和port
            endpoint = self.config['endpoint']

            client = Minio(
                endpoint=endpoint,
                access_key=self.config['access_key'],
                secret_key=self.config['secret_key'],
                secure=False  # HTTP连接
            )
            
            logger.info(f"MinIO客户端创建成功，连接到: {endpoint}")
            return client
            
        except Exception as e:
            logger.error(f"创建MinIO客户端失败: {e}")
            raise
    
    def test_connection(self) -> bool:
        """测试连接"""
        try:
            # 尝试列出存储桶来测试连接
            buckets = list(self.client.list_buckets())
            logger.info(f"连接测试成功，找到 {len(buckets)} 个存储桶")
            return True
        except Exception as e:
            logger.error(f"连接测试失败: {e}")
            return False
    
    def ensure_bucket_exists(self, bucket_name: Optional[str] = None) -> bool:
        """确保存储桶存在"""
        bucket_name = bucket_name or self.config['bucket']
        
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name, location=self.config.get('region', 'us-east-1'))
                logger.info(f"创建存储桶: {bucket_name}")
            else:
                logger.info(f"存储桶已存在: {bucket_name}")
            return True
        except Exception as e:
            logger.error(f"确保存储桶存在失败: {e}")
            return False
    
    def list_images(self, bucket_name: Optional[str] = None, prefix: str = "") -> List[str]:
        """
        列出存储桶中的图片文件
        
        Args:
            bucket_name: 存储桶名称
            prefix: 文件前缀过滤
            
        Returns:
            图片文件名列表
        """
        bucket_name = bucket_name or self.config['bucket']
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        
        try:
            objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            image_files = []
            
            for obj in objects:
                file_ext = os.path.splitext(obj.object_name)[1].lower()
                if file_ext in image_extensions:
                    image_files.append(obj.object_name)
            
            logger.info(f"找到 {len(image_files)} 个图片文件")
            return image_files
            
        except Exception as e:
            logger.error(f"列出图片文件失败: {e}")
            return []
    
    def get_image(self, object_name: str, bucket_name: Optional[str] = None) -> Optional[Image.Image]:
        """
        从MinIO获取图片
        
        Args:
            object_name: 对象名称
            bucket_name: 存储桶名称
            
        Returns:
            PIL Image对象
        """
        bucket_name = bucket_name or self.config['bucket']
        
        try:
            response = self.client.get_object(bucket_name, object_name)
            image_data = response.read()
            response.close()
            response.release_conn()
            
            # 使用PIL打开图片
            image = Image.open(io.BytesIO(image_data))
            logger.info(f"成功获取图片: {object_name}, 尺寸: {image.size}")
            return image
            
        except Exception as e:
            logger.error(f"获取图片失败 {object_name}: {e}")
            return None
    
    def get_image_bytes(self, object_name: str, bucket_name: Optional[str] = None) -> Optional[bytes]:
        """
        从MinIO获取图片字节数据
        
        Args:
            object_name: 对象名称
            bucket_name: 存储桶名称
            
        Returns:
            图片字节数据
        """
        bucket_name = bucket_name or self.config['bucket']
        
        try:
            response = self.client.get_object(bucket_name, object_name)
            image_data = response.read()
            response.close()
            response.release_conn()
            
            logger.info(f"成功获取图片字节数据: {object_name}, 大小: {len(image_data)} bytes")
            return image_data
            
        except Exception as e:
            logger.error(f"获取图片字节数据失败 {object_name}: {e}")
            return None
    
    def upload_image(self, file_path: str, object_name: Optional[str] = None, 
                    bucket_name: Optional[str] = None) -> bool:
        """
        上传图片到MinIO
        
        Args:
            file_path: 本地文件路径
            object_name: 对象名称，如果为None则使用文件名
            bucket_name: 存储桶名称
            
        Returns:
            是否上传成功
        """
        bucket_name = bucket_name or self.config['bucket']
        object_name = object_name or os.path.basename(file_path)
        
        try:
            # 确保存储桶存在
            self.ensure_bucket_exists(bucket_name)
            
            # 上传文件
            self.client.fput_object(bucket_name, object_name, file_path)
            logger.info(f"成功上传图片: {file_path} -> {bucket_name}/{object_name}")
            return True
            
        except Exception as e:
            logger.error(f"上传图片失败 {file_path}: {e}")
            return False
    
    def delete_image(self, object_name: str, bucket_name: Optional[str] = None) -> bool:
        """
        删除图片
        
        Args:
            object_name: 对象名称
            bucket_name: 存储桶名称
            
        Returns:
            是否删除成功
        """
        bucket_name = bucket_name or self.config['bucket']
        
        try:
            self.client.remove_object(bucket_name, object_name)
            logger.info(f"成功删除图片: {bucket_name}/{object_name}")
            return True
            
        except Exception as e:
            logger.error(f"删除图片失败 {object_name}: {e}")
            return False
    
    def get_image_info(self, object_name: str, bucket_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取图片信息
        
        Args:
            object_name: 对象名称
            bucket_name: 存储桶名称
            
        Returns:
            图片信息字典
        """
        bucket_name = bucket_name or self.config['bucket']
        
        try:
            stat = self.client.stat_object(bucket_name, object_name)
            
            info = {
                'object_name': object_name,
                'size': stat.size,
                'etag': stat.etag,
                'last_modified': stat.last_modified,
                'content_type': stat.content_type,
                'metadata': stat.metadata
            }
            
            logger.info(f"获取图片信息成功: {object_name}")
            return info
            
        except Exception as e:
            logger.error(f"获取图片信息失败 {object_name}: {e}")
            return None
    
    def _parse_image_time_from_path(self, object_name: str) -> Optional[datetime]:
        """
        从图片路径中解析时间信息
        
        Args:
            object_name: 对象名称，格式如 "611/20251219/611_192168001237_20251127_20251219170000.jpg"
            
        Returns:
            解析出的时间对象，失败返回None
        """
        try:
            # 正则表达式匹配路径格式: {库房号}/{日期}/{库房号}_{IP}_{采集日期}_{详细时间}.jpg
            pattern = r'(\d+)/(\d{8})/\d+_\d+_\d{7,8}_(\d{14})\.jpg'
            match = re.match(pattern, object_name)
            
            if match:
                detailed_time = match.group(3)  # 获取详细时间部分
                # 解析时间格式: YYYYMMDDHHMMSS
                return datetime.strptime(detailed_time, "%Y%m%d%H%M%S")
            else:
                logger.warning(f"无法解析图片路径时间: {object_name}")
                return None
                
        except Exception as e:
            logger.error(f"解析图片时间失败 {object_name}: {e}")
            return None
    
    def _parse_room_id_from_path(self, object_name: str) -> Optional[str]:
        """
        从图片路径中解析库房号
        
        Args:
            object_name: 对象名称
            
        Returns:
            库房号，失败返回None
        """
        try:
            # 提取路径开头的库房号
            parts = object_name.split('/')
            if len(parts) >= 1:
                return parts[0]
            return None
        except Exception as e:
            logger.error(f"解析库房号失败 {object_name}: {e}")
            return None
    
    def list_images_by_time_and_room(
        self, 
        room_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        bucket_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        根据时间范围和库房号查询图片
        
        Args:
            room_id: 库房号，如果为None则查询所有库房
            start_time: 开始时间，如果为None则不限制开始时间
            end_time: 结束时间，如果为None则不限制结束时间
            bucket_name: 存储桶名称
            
        Returns:
            符合条件的图片信息列表，每个元素包含：
            {
                'object_name': str,      # 对象名称
                'room_id': str,          # 库房号
                'capture_time': datetime, # 采集时间
                'last_modified': datetime # 最后修改时间
            }
        """
        bucket_name = bucket_name or self.config['bucket']
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        
        try:
            # 构建前缀过滤
            prefix = f"{room_id}/" if room_id else ""
            
            # 获取所有对象
            objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            filtered_images = []
            
            for obj in objects:
                # 检查文件扩展名
                file_ext = os.path.splitext(obj.object_name)[1].lower()
                if file_ext not in image_extensions:
                    continue
                
                # 解析库房号
                parsed_room_id = self._parse_room_id_from_path(obj.object_name)
                if room_id and parsed_room_id != room_id:
                    continue
                
                # 解析采集时间
                capture_time = self._parse_image_time_from_path(obj.object_name)
                if not capture_time:
                    continue
                
                # 时间范围过滤
                if start_time and capture_time < start_time:
                    continue
                if end_time and capture_time > end_time:
                    continue
                
                # 添加到结果列表
                filtered_images.append({
                    'object_name': obj.object_name,
                    'room_id': parsed_room_id,
                    'capture_time': capture_time,
                    'last_modified': obj.last_modified,
                    'size': obj.size
                })
            
            # 按采集时间排序
            filtered_images.sort(key=lambda x: x['capture_time'])
            
            logger.info(f"根据条件查询到 {len(filtered_images)} 张图片 - 库房: {room_id or '全部'}, "
                       f"时间范围: {start_time} ~ {end_time}")
            
            return filtered_images
            
        except Exception as e:
            logger.error(f"按时间和库房查询图片失败: {e}")
            return []
    
    def list_recent_images(
        self, 
        room_id: Optional[str] = None,
        hours: int = 1,
        bucket_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        查询最近指定小时内的图片
        
        Args:
            room_id: 库房号，如果为None则查询所有库房
            hours: 查询最近多少小时的数据，默认1小时
            bucket_name: 存储桶名称
            
        Returns:
            符合条件的图片信息列表
        """
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)
        
        logger.info(f"查询最近 {hours} 小时的图片数据 - 库房: {room_id or '全部'}, "
                   f"时间范围: {start_time} ~ {end_time}")
        
        return self.list_images_by_time_and_room(
            room_id=room_id,
            start_time=start_time,
            end_time=end_time,
            bucket_name=bucket_name
        )
    
    def get_images_by_date_range(
        self,
        room_id: Optional[str] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        bucket_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        根据日期范围查询图片（按日期，不考虑具体时间）
        
        Args:
            room_id: 库房号
            date_start: 开始日期，格式 "YYYYMMDD"
            date_end: 结束日期，格式 "YYYYMMDD"
            bucket_name: 存储桶名称
            
        Returns:
            符合条件的图片信息列表
        """
        try:
            start_time = None
            end_time = None
            
            if date_start:
                start_time = datetime.strptime(date_start, "%Y%m%d")
            
            if date_end:
                end_time = datetime.strptime(date_end + "235959", "%Y%m%d%H%M%S")
            
            return self.list_images_by_time_and_room(
                room_id=room_id,
                start_time=start_time,
                end_time=end_time,
                bucket_name=bucket_name
            )
            
        except Exception as e:
            logger.error(f"按日期范围查询图片失败: {e}")
            return []


def create_minio_client() -> MinIOClient:
    """创建MinIO客户端实例"""
    return MinIOClient()


if __name__ == "__main__":
    # 测试代码
    client = create_minio_client()
    
    # 测试连接
    if client.test_connection():
        print("MinIO连接成功!")
        
        # 测试基本图片列表功能
        print("\n=== 测试基本图片列表功能 ===")
        images = client.list_images()
        print(f"找到图片文件总数: {len(images)}")
        
        # 测试最近1小时的图片查询
        print("\n=== 测试最近1小时图片查询 ===")
        recent_images = client.list_recent_images(hours=1)
        print(f"最近1小时图片数量: {len(recent_images)}")
        for img in recent_images[:5]:  # 显示前5张
            print(f"  - {img['object_name']} (库房: {img['room_id']}, 时间: {img['capture_time']})")
        
        # 测试特定库房的最近图片
        print("\n=== 测试特定库房最近1小时图片 ===")
        room_611_images = client.list_recent_images(room_id="611", hours=1)
        print(f"库房611最近1小时图片数量: {len(room_611_images)}")
        for img in room_611_images[:3]:
            print(f"  - {img['object_name']} (时间: {img['capture_time']})")
        
        # 测试时间范围查询
        print("\n=== 测试时间范围查询 ===")
        from datetime import datetime, timedelta
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=2)
        
        time_range_images = client.list_images_by_time_and_room(
            room_id="612",
            start_time=start_time,
            end_time=end_time
        )
        print(f"库房612过去2小时图片数量: {len(time_range_images)}")
        
        # 测试日期范围查询
        print("\n=== 测试日期范围查询 ===")
        today = datetime.now().strftime("%Y%m%d")
        date_images = client.get_images_by_date_range(
            room_id="611",
            date_start=today,
            date_end=today
        )
        print(f"库房611今天的图片数量: {len(date_images)}")
        
        # 如果有图片，尝试获取第一张
        if images:
            print(f"\n=== 测试图片获取 ===")
            first_image = client.get_image(images[0])
            if first_image:
                print(f"成功获取图片: {images[0]}, 尺寸: {first_image.size}")
    else:
        print("MinIO连接失败!")