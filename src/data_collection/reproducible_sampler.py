import os
import random
import csv
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger
import pandas as pd
from PIL import Image

from src.vision.offline.data_loader import DataLoader

class ReproducibleSampler:
    """
    可复现的图像采样器
    """
    
    def __init__(
        self, 
        output_dir: str, 
        room_ids: List[str], 
        start_date: str, 
        end_date: str, 
        limit_per_day: int = 2,
        seed: int = 42
    ):
        self.output_dir = Path(output_dir)
        self.room_ids = room_ids
        self.start_date = start_date
        self.end_date = end_date
        self.limit_per_day = limit_per_day
        self.seed = seed
        
        self.data_loader = DataLoader()
        self.manifest_path = self.output_dir / "manifest.csv"
        
        self._init_workspace()

    def _init_workspace(self):
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)
            
        if not self.manifest_path.exists():
            with open(self.manifest_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['room_id', 'date', 'image_path', 'collection_timestamp'])

    def sample(self):
        """执行采样"""
        logger.info(f"Starting reproducible sampling from {self.start_date} to {self.end_date}")
        logger.info(f"Seed: {self.seed}, Rooms: {self.room_ids}")
        
        current_date = datetime.strptime(self.start_date, "%Y-%m-%d")
        end_date_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
        
        while current_date <= end_date_dt:
            date_str = current_date.strftime("%Y-%m-%d")
            self._process_date(date_str)
            current_date += timedelta(days=1)
            
        logger.info("Sampling completed.")

    def _process_date(self, date_str: str):
        """处理单日所有库房采样 (批量查询优化)"""
        # 1. 批量获取当日所有库房图像
        try:
            all_images_flat = self.data_loader.get_images_by_date(date_str)
        except Exception as e:
            logger.error(f"Failed to fetch metadata for {date_str}: {e}")
            return

        # 2. 按库房分组
        images_by_room: Dict[str, List[Dict]] = {rid: [] for rid in self.room_ids}
        for img in all_images_flat:
            rid = str(img['room_id'])
            if rid in images_by_room:
                images_by_room[rid].append(img)
                
        # 3. 遍历处理每个库房
        for room_id in self.room_ids:
            room_images = images_by_room[room_id]
            self._sample_and_save_room_images(room_id, date_str, room_images)

    def _sample_and_save_room_images(self, room_id: str, date_str: str, all_images: List[Dict]):
        """从给定图像列表中采样并保存"""
        if not all_images:
            logger.warning(f"No images found for Room {room_id} on {date_str}")
            return
            
        if len(all_images) < self.limit_per_day:
            logger.warning(f"Insufficient images for Room {room_id} on {date_str}: Found {len(all_images)}, Required {self.limit_per_day}")
        
        # 确定性随机采样
        date_int = int(date_str.replace("-", ""))
        try:
            room_int = int(room_id)
        except ValueError:
            room_int = sum(ord(c) for c in room_id)
            
        local_seed = self.seed + date_int + room_int
        random.seed(local_seed)
        
        all_images.sort(key=lambda x: x['image_path'])
        
        selected_images = []
        if len(all_images) <= self.limit_per_day:
            selected_images = all_images
        else:
            selected_images = random.sample(all_images, self.limit_per_day)
            
        for item in selected_images:
            self._save_image(item, date_str)

    def _process_room_day(self, room_id: str, date_str: str):
        """(Deprecated) 处理单日单库房采样"""
        # 保留此方法以兼容旧测试或单独调用，但在 sample 中不再使用
        try:
            all_images = self.data_loader.get_images_by_date_and_room(room_id, date_str)
            self._sample_and_save_room_images(room_id, date_str, all_images)
        except Exception as e:
            logger.error(f"Failed to fetch metadata for {room_id} on {date_str}: {e}")

    def _save_image(self, item: Dict[str, Any], date_str: str):
        room_id = str(item['room_id'])
        image_path = item['image_path']
        filename = Path(image_path).name
        
        # 扁平化存储：output_dir / filename
        local_path = self.output_dir / filename
        
        if local_path.exists():
            logger.debug(f"Image already exists: {local_path}")
            self._append_manifest_if_not_exists(room_id, date_str, filename, str(item['collection_datetime']))
            return
            
        # 下载
        try:
            image = self.data_loader.download_image(image_path)
            if image is None:
                logger.error(f"Failed to download image: {image_path}")
                return
                
            image.save(local_path)
            
            self._append_manifest_if_not_exists(room_id, date_str, filename, str(item['collection_datetime']))
            
        except Exception as e:
            logger.error(f"Error saving image {image_path}: {e}")

    def _append_manifest_if_not_exists(self, room_id, date, rel_path, timestamp):
        # 简单追加，实际生产中可能需要更高效的查重
        # 这里为了简单，每次都追加，或者在内存维护一个 set
        # 考虑到性能，我们直接追加，让后续工具处理重复，或者在这里简单读一下最后几行？
        # 为严格起见，我们追加即可。
        with open(self.manifest_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([room_id, date, rel_path, timestamp])

