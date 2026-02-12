import os
import csv
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from loguru import logger
import pandas as pd
from PIL import Image

# 引入项目依赖
from src.vision.offline.data_loader import DataLoader
from src.vision.offline.config import config

class DatasetCollector:
    """
    数据集采集器
    负责从原始数据源（数据库+MinIO）采集图像构建离线数据集
    """
    
    def __init__(
        self, 
        output_dir: str, 
        room_ids: List[str], 
        start_date: str, 
        end_date: str, 
        limit_per_day: int = 2,
        max_retries: int = 3
    ):
        """
        Args:
            output_dir: 数据集输出根目录
            room_ids: 目标库房ID列表
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            limit_per_day: 每日每库房采集数量
            max_retries: 异常重试次数
        """
        self.output_dir = Path(output_dir)
        self.room_ids = room_ids
        self.start_date = start_date
        self.end_date = end_date
        self.limit_per_day = limit_per_day
        self.max_retries = max_retries
        
        self.data_loader = DataLoader()
        self.manifest_path = self.output_dir / "manifest.csv"
        self.collected_md5s: Set[str] = set()
        
        # 初始化目录和清单
        self._init_workspace()

    def _init_workspace(self):
        """初始化工作空间"""
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)
            
        # 加载或创建清单
        if self.manifest_path.exists():
            df = pd.read_csv(self.manifest_path)
            self.collected_md5s = set(df['md5'].tolist())
            logger.info(f"Loaded {len(self.collected_md5s)} existing records from manifest")
        else:
            with open(self.manifest_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['room_id', 'filename', 'collection_date', 'md5', 'original_path', 'local_path'])

    def calculate_md5(self, image_path: Path) -> str:
        """计算文件MD5"""
        hash_md5 = hashlib.md5()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def collect(self):
        """执行采集任务"""
        logger.info(f"Starting data collection for rooms {self.room_ids} from {self.start_date} to {self.end_date}")
        
        # 遍历每一天
        current_date = datetime.strptime(self.start_date, "%Y-%m-%d")
        end_date_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
        
        total_collected = 0
        
        while current_date <= end_date_dt:
            date_str = current_date.strftime("%Y-%m-%d")
            logger.info(f"Processing date: {date_str}")
            
            for room_id in self.room_ids:
                self._process_room_day(room_id, date_str)
                
            current_date += timedelta(days=1)
            
        logger.info(f"Data collection completed.")

    def _process_room_day(self, room_id: str, date_str: str):
        """处理指定库房指定日期的采集"""
        # 为了去重，请求比限制更多的元数据 (2倍)
        fetch_limit = self.limit_per_day * 2
        
        retries = 0
        items = []
        
        # 1. 获取元数据 (带重试)
        while retries < self.max_retries:
            try:
                # 这里我们需要修改 DataLoader 或者在外部过滤 room_id
                # 现有的 DataLoader.get_random_images_metadata 是一次性获取所有库房的
                # 为了效率，我们可能需要修改 DataLoader 支持指定 room_id，
                # 或者我们在外部调用时，只能依赖它返回包含目标 room_id 的数据
                # 但 data_loader 的 sql 是对所有 room 排序的。
                # 暂时我们直接调用 data_loader，它会返回所有库房的数据。
                # 但这会导致每次都查询所有库房，效率较低。
                # 更好的方式是修改 DataLoader 支持 room_id 过滤。
                # 为了不破坏现有代码太多，我们先假设 DataLoader 返回了我们需要的数据。
                # 实际上，我们需要修改 data_loader.py 以支持 room_id 过滤。
                
                # 暂时先用现有接口，注意现有接口返回的是所有库房的。
                # 我们可以传入 limit_per_room_day，它会在 SQL 内部对每个 room 做限制。
                # 所以我们只需要传入日期范围即可。
                
                # 但是，collector 的逻辑是按天循环的。
                # 如果调用 data_loader 传入 start=date, end=date，它会返回该天所有库房的数据。
                # 这是可行的。
                
                items = self.data_loader.get_random_images_metadata(
                    limit_per_room_day=fetch_limit,
                    start_date=date_str,
                    end_date=date_str
                )
                break
            except Exception as e:
                retries += 1
                logger.warning(f"Failed to get metadata for {date_str} (Attempt {retries}/{self.max_retries}): {e}")
                time.sleep(2)
        
        if not items:
            logger.warning(f"No metadata found for {date_str}")
            return

        # 过滤出当前库房的数据
        room_items = [item for item in items if str(item['room_id']) == str(room_id)]
        
        collected_count = 0
        for item in room_items:
            if collected_count >= self.limit_per_day:
                break
                
            if self._process_single_item(item, date_str):
                collected_count += 1
                
        if collected_count < self.limit_per_day:
            logger.warning(f"Room {room_id} on {date_str}: Only collected {collected_count}/{self.limit_per_day} images")

    def _process_single_item(self, item: Dict[str, Any], date_str: str) -> bool:
        """处理单张图片"""
        image_path = item['image_path']
        room_id = str(item['room_id'])
        
        # 构建本地路径: output_dir/room_id/date/filename
        # 文件名保持原始文件名，防止冲突
        filename = Path(image_path).name
        local_dir = self.output_dir / room_id / date_str
        local_path = local_dir / filename
        
        # 如果本地文件已存在，检查是否在清单中
        if local_path.exists():
            # 计算MD5并检查
            md5 = self.calculate_md5(local_path)
            if md5 in self.collected_md5s:
                logger.info(f"Skipping duplicate image (MD5): {image_path}")
                return False
        
        # 下载图片
        image = None
        retries = 0
        while retries < self.max_retries:
            try:
                image = self.data_loader.download_image(image_path)
                if image:
                    break
            except Exception as e:
                logger.warning(f"Download failed for {image_path}: {e}")
            retries += 1
            time.sleep(1)
            
        if image is None:
            logger.error(f"Failed to download image after retries: {image_path}")
            return False
            
        # 保存文件
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            # 临时保存以计算MD5
            temp_path = local_path.with_name(f".tmp_{local_path.name}")
            image.save(temp_path)
            
            md5 = self.calculate_md5(temp_path)
            
            # 去重检查
            if md5 in self.collected_md5s:
                logger.info(f"Duplicate image detected (MD5), removing temp file: {image_path}")
                os.remove(temp_path)
                return False
                
            # 重命名为正式文件
            if local_path.exists():
                os.remove(local_path)
            os.rename(temp_path, local_path)
            
            # 更新记录
            self._append_manifest(room_id, filename, date_str, md5, image_path, str(local_path.relative_to(self.output_dir)))
            self.collected_md5s.add(md5)
            logger.info(f"Successfully collected: {local_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving image {image_path}: {e}")
            if 'temp_path' in locals() and temp_path.exists():
                os.remove(temp_path)
            return False

    def _append_manifest(self, room_id, filename, date, md5, original_path, local_path):
        """追加记录到清单"""
        with open(self.manifest_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([room_id, filename, date, md5, original_path, local_path])

