"""
数据加载与缓存模块
"""
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import random
from sqlalchemy import text
from PIL import Image
from loguru import logger
import io

from global_const.global_const import pgsql_engine
from utils.minio_client import MinIOClient
from .config import config
from global_const.const_config import MINIO_BUCKET_NAME

class DataLoader:
    """数据加载器：负责从数据库查询和MinIO下载图片"""

    def __init__(self):
        self.minio_client = MinIOClient()
        self.cache_dir = Path(config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 兼容性处理：如果 MinIOClient 封装没有暴露 bucket_name，尝试从配置获取
        self.bucket_name = getattr(self.minio_client, "bucket_name", MINIO_BUCKET_NAME)

    def get_random_images_metadata(self, limit_per_room_day: int = 2, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        从数据库获取每库房每天随机N张图片的元数据
        
        Args:
            limit_per_room_day: 每天每库房限制数量
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
        
        Returns:
            List[Dict]: 包含 image_path, room_id, collection_datetime 的列表
        """
        # 构建日期过滤条件
        date_filter = ""
        params = {"limit": limit_per_room_day}
        
        if start_date:
            date_filter += " AND collection_datetime >= :start_date"
            params["start_date"] = start_date
            
        if end_date:
            date_filter += " AND collection_datetime <= :end_date"
            params["end_date"] = f"{end_date} 23:59:59"

        query = text(f"""
            WITH ranked_images AS (
                SELECT 
                    image_path, 
                    room_id, 
                    collection_datetime,
                    ROW_NUMBER() OVER (PARTITION BY room_id, DATE(collection_datetime) ORDER BY RANDOM()) as rn
                FROM mushroom_embedding
                WHERE 1=1 {date_filter}
            )
            SELECT image_path, room_id, collection_datetime
            FROM ranked_images
            WHERE rn <= :limit
        """)
        
        results = []
        try:
            with pgsql_engine.connect() as conn:
                rows = conn.execute(query, params).fetchall()
                for row in rows:
                    results.append({
                        "image_path": row.image_path,
                        "room_id": row.room_id,
                        "collection_datetime": row.collection_datetime
                    })
            logger.info(f"Query returned {len(results)} images from database.")
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            raise
            
        return results

    def get_images_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        获取指定日期的所有图像元数据 (所有库房)
        """
        # 扩展查询以包含MinIO中可能存在但未入库的图片（如果需要的话，但这里我们只能查库）
        # 注意：这里我们只查询数据库，如果数据库没有记录，就无法获取。
        # 考虑到用户提到 MinIO 中有记录但可能数据库没有？
        # 用户输入："607在minio存在记录... run_reproducible_sampling未获取到607和608的数据"
        # 且之前的数据库查询只返回了 611 和 612。
        # 这意味着 607/7 和 608/8 的数据根本不在 mushroom_embedding 表中。
        # 如果数据不在 DB 中，DataLoader 无法通过 SQL 获取。
        # 除非我们直接列出 MinIO 中的文件。
        
        # 既然要求是从"当前存在的4个库房中... 随机抽取"，且用户明确指出了MinIO路径。
        # 我们需要修改逻辑：如果数据库没有数据，尝试直接列出 MinIO 目录。
        # 但 DataLoader 的职责是从 DB 获取元数据。
        # 我们可以在这里增加一个 fallback：列出 MinIO 目录。
        
        query = text("""
            SELECT image_path, room_id, collection_datetime
            FROM mushroom_embedding
            WHERE DATE(collection_datetime) = :date
            ORDER BY collection_datetime
        """)
        
        results = []
        
        # 1. 尝试从数据库获取
        try:
            with pgsql_engine.connect() as conn:
                rows = conn.execute(query, {"date": date_str}).fetchall()
                for row in rows:
                    results.append({
                        "image_path": row.image_path,
                        "room_id": str(row.room_id),
                        "collection_datetime": row.collection_datetime
                    })
        except Exception as e:
            logger.warning(f"Database query failed for {date_str}, falling back to MinIO listing: {e}")

        # 2. 如果数据库结果不完整（比如缺少某些库房），尝试从 MinIO 补充
        # 假设我们需要 7, 8, 611, 612
        required_rooms = ['7', '8', '611', '612']
        found_rooms = set(r['room_id'] for r in results)
        missing_rooms = [r for r in required_rooms if r not in found_rooms]
        
        if missing_rooms:
            # logger.info(f"Missing data for rooms {missing_rooms} in DB, listing MinIO...")
            # 构建日期前缀: YYYYMMDD
            # MinIO 路径结构: mogu/{room_id}/{date_compact}/{filename}
            # date_str: YYYY-MM-DD -> date_compact: YYYYMMDD
            date_compact = date_str.replace("-", "")
            
            for room_id in missing_rooms:
                prefix = f"{room_id}/{date_compact}/"
                try:
                    # 使用 self.minio_client.client (原始 Minio 对象) 进行操作
                    # 确保使用正确的 bucket_name
                    objects = self.minio_client.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
                    # objects 是 minio.datatypes.Object 的列表
                    for obj in objects:
                        # 解析文件名获取时间戳
                        # filename: 7_1921681234_20260127_20260211110130.jpg
                        # 格式: {room}_{ip}_{date1}_{date2}.jpg
                        # date2 是采集时间: YYYYMMDDHHMMSS
                        try:
                            filename = obj.object_name.split("/")[-1]
                            parts = filename.replace(".jpg", "").split("_")
                            timestamp_str = parts[-1]
                            collection_dt = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                            
                            results.append({
                                "image_path": obj.object_name,
                                "room_id": room_id,
                                "collection_datetime": collection_dt
                            })
                        except Exception as e:
                            # logger.debug(f"Failed to parse timestamp from {obj.object_name}: {e}")
                            # 如果无法解析，使用当前日期作为 fallback? 或者跳过
                            continue
                except Exception as e:
                    logger.warning(f"Failed to list MinIO for {room_id} on {date_str}: {e}")

        return results

    def get_images_by_date_and_room(self, room_id: str, date_str: str) -> List[Dict[str, Any]]:
        """
        获取指定库房指定日期的所有图像元数据
        """
        query = text("""
            SELECT image_path, room_id, collection_datetime
            FROM mushroom_embedding
            WHERE room_id = :room_id 
            AND DATE(collection_datetime) = :date
            ORDER BY collection_datetime
        """)
        
        results = []
        try:
            with pgsql_engine.connect() as conn:
                rows = conn.execute(query, {"room_id": room_id, "date": date_str}).fetchall()
                for row in rows:
                    results.append({
                        "image_path": row.image_path,
                        "room_id": row.room_id,
                        "collection_datetime": row.collection_datetime
                    })
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            raise
            
        return results

    def download_image(self, image_path: str) -> Optional[Image.Image]:
        """
        下载图片，支持本地缓存
        
        Args:
            image_path: MinIO中的对象路径
            
        Returns:
            PIL Image对象，如果失败返回None
        """
        # 缓存文件路径 (将MinIO路径中的/替换为_以作为文件名)
        safe_filename = image_path.replace("/", "_")
        local_file_path = self.cache_dir / safe_filename
        
        # 1. 检查缓存
        if local_file_path.exists():
            try:
                logger.debug(f"Cache hit for {image_path}")
                return Image.open(local_file_path).convert("RGB")
            except Exception as e:
                logger.warning(f"Failed to load cached image {local_file_path}, redownloading: {e}")
                # 删除损坏的缓存文件
                os.remove(local_file_path)

        # 2. 从MinIO下载
        try:
            image_bytes = self.minio_client.get_image_bytes(image_path)
            if not image_bytes:
                logger.warning(f"Image not found in MinIO: {image_path}")
                return None
            
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            
            # 3. 写入缓存
            try:
                image.save(local_file_path)
            except Exception as e:
                logger.warning(f"Failed to write cache for {image_path}: {e}")
                
            return image
        except Exception as e:
            logger.error(f"Failed to download image {image_path}: {e}")
            return None

    def clear_cache(self):
        """清理缓存目录"""
        try:
            for file in self.cache_dir.iterdir():
                if file.is_file():
                    file.unlink()
            logger.info("Cache cleared.")
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
