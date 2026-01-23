"""
蘑菇图像处理器
专门处理蘑菇数据结构的图像文件，解析路径信息并进行向量化处理
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from dateutil import parser as date_parser
from loguru import logger
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
# from utils.minio_service import create_minio_service
# from clip.clip_app import get_image_embedding, processor, model
from utils.create_table import MushroomImageEmbedding


@dataclass
class MushroomImageInfo:
    """蘑菇图像信息数据类"""
    mushroom_id: str          # 蘑菇库号
    collection_ip: str        # 采集IP
    collection_date: str      # 采集日期 (YYYYMMDD)
    detailed_time: str        # 详细时间 (YYYYMMDDHHMMSS)
    file_name: str           # 文件名
    file_path: str           # 完整文件路径
    date_folder: str         # 日期文件夹 (YYYYMMDD)
    
    @property
    def collection_datetime(self) -> datetime:
        """获取采集时间的datetime对象"""
        return datetime.strptime(self.detailed_time, "%Y%m%d%H%M%S")
    
    @property
    def collection_date_obj(self) -> datetime:
        """获取采集日期的datetime对象"""
        return datetime.strptime(self.collection_date, "%Y%m%d")


class MushroomImagePathParser:
    """蘑菇图像路径解析器"""
    
    def __init__(self):
        # 文件路径正则表达式 - 支持多种格式变体
        # 格式: 612/20251224/612_1921681235_20251218_20251224160000.jpg
        # 或: 7/20251224/7_1921681233_2025124_20251224160000.jpg (collection_date可能是7位)
        # 或: 8/20260113/8_1921681232_202615_20260113160130.jpg (collection_date可能是6位)
        self.path_pattern = re.compile(
            r'(?P<mushroom_id>\d+)/(?P<date_folder>\d{8})/(?P<mushroom_id_2>\d+)_(?P<collection_ip>\d+)_(?P<collection_date>\d{6,8})_(?P<detailed_time>\d{14})\.jpg'
        )
        
        # 文件名正则表达式 - 支持多种格式变体
        # 格式: 612_1921681235_20251218_20251224160000.jpg
        # 或: 7_1921681233_2025124_20251224160000.jpg (collection_date可能是7位)
        # 或: 8_1921681232_202615_20260113160130.jpg (collection_date可能是6位)
        self.filename_pattern = re.compile(
            r'(?P<mushroom_id>\d+)_(?P<collection_ip>\d+)_(?P<collection_date>\d{6,8})_(?P<detailed_time>\d{14})\.jpg'
        )
    
    def _normalize_collection_date(self, collection_date: str) -> str:
        """
        标准化采集日期格式，使用 dateutil 自动解析各种格式
        
        Args:
            collection_date: 原始采集日期，可能是6位、7位或8位
            
        Returns:
            标准化的8位日期格式 (YYYYMMDD)
            
        Examples:
            202615 -> 20260105 (2026年1月5日)
            2026115 -> 20260115 (2026年1月15日)
            2025124 -> 20251204 (2025年12月4日)
            20251224 -> 20251224 (已是标准格式)
        """
        date_len = len(collection_date)
        
        # 8位标准格式，直接验证并返回
        if date_len == 8:
            try:
                datetime.strptime(collection_date, '%Y%m%d')
                return collection_date
            except ValueError as e:
                logger.error(f"8位日期格式无效: {collection_date}, 错误: {e}")
                return collection_date
        
        # 6位或7位格式，需要智能解析
        if date_len not in [6, 7]:
            logger.error(f"无法标准化日期格式(长度异常): {collection_date} (长度: {date_len})")
            return collection_date
        
        year = collection_date[:4]
        month_day = collection_date[4:]
        
        # 构建可能的日期格式列表
        parse_attempts = []
        
        if date_len == 6:
            # 6位: YYYYMD (月日各1位)
            m, d = month_day[0], month_day[1]
            parse_attempts.extend([
                f"{year}-{m}-{d}",      # 尝试单位数月日
                f"{year}-0{m}-0{d}",    # 补零
            ])
        
        elif date_len == 7:
            # 7位: YYYYMDD 或 YYYYMMD
            first = int(month_day[0])
            
            if first == 1:
                second = int(month_day[1])
                if second in [0, 1, 2]:
                    # 10-12月
                    parse_attempts.extend([
                        f"{year}-{month_day[:2]}-{month_day[2]}",
                        f"{year}-{month_day[:2]}-0{month_day[2]}",
                    ])
                else:
                    # 1月
                    parse_attempts.extend([
                        f"{year}-1-{month_day[1:]}",
                        f"{year}-01-{month_day[1:]}",
                    ])
            else:
                # 2-9月
                parse_attempts.extend([
                    f"{year}-{month_day[0]}-{month_day[1:]}",
                    f"{year}-0{month_day[0]}-{month_day[1:]}",
                ])
        
        # 使用 dateutil.parser 尝试解析
        for attempt in parse_attempts:
            try:
                parsed_date = date_parser.parse(attempt, dayfirst=False)
                normalized = parsed_date.strftime('%Y%m%d')
                logger.debug(f"日期标准化: {collection_date} -> {normalized}")
                return normalized
            except (ValueError, date_parser.ParserError):
                continue
        
        # 所有尝试都失败
        logger.error(f"无法标准化日期格式({date_len}位): {collection_date}, 尝试: {parse_attempts}")
        return collection_date
    
    def parse_path(self, file_path: str) -> Optional[MushroomImageInfo]:
        """
        解析完整文件路径
        
        Args:
            file_path: 完整文件路径，如 "612/20251224/612_1921681235_20251218_20251224160000.jpg"
            
        Returns:
            MushroomImageInfo对象或None
        """
        match = self.path_pattern.match(file_path)
        if not match:
            logger.warning(f"路径格式不匹配: '{file_path}' | 期望格式: <库房号>/<日期文件夹>/<库房号>_<IP>_<采集日期7-8位>_<详细时间14位>.jpg")
            return None
        
        groups = match.groupdict()
        
        # 验证蘑菇库号一致性
        if groups['mushroom_id'] != groups['mushroom_id_2']:
            logger.warning(f"路径中库房号不一致: 文件夹={groups['mushroom_id']}, 文件名={groups['mushroom_id_2']} | 路径: {file_path}")
            return None
        
        # 标准化采集日期
        normalized_collection_date = self._normalize_collection_date(groups['collection_date'])
        
        return MushroomImageInfo(
            mushroom_id=groups['mushroom_id'],
            collection_ip=groups['collection_ip'],
            collection_date=normalized_collection_date,
            detailed_time=groups['detailed_time'],
            file_name=os.path.basename(file_path),
            file_path=file_path,
            date_folder=groups['date_folder']
        )
    
    def parse_filename(self, filename: str, mushroom_id: str = None, date_folder: str = None) -> Optional[MushroomImageInfo]:
        """
        解析文件名
        
        Args:
            filename: 文件名，如 "612_1921681235_20251218_20251224160000.jpg"
            mushroom_id: 蘑菇库号（用于构建完整路径）
            date_folder: 日期文件夹（用于构建完整路径）
            
        Returns:
            MushroomImageInfo对象或None
        """
        match = self.filename_pattern.match(filename)
        if not match:
            logger.warning(f"文件名格式不匹配: '{filename}' | 期望格式: <库房号>_<IP>_<采集日期7-8位>_<详细时间14位>.jpg")
            return None
        
        groups = match.groupdict()
        
        # 如果提供了蘑菇库号，验证一致性
        if mushroom_id and groups['mushroom_id'] != mushroom_id:
            logger.warning(f"文件名中库房号不一致: 参数={mushroom_id}, 文件名={groups['mushroom_id']} | 文件: {filename}")
            return None
        
        # 标准化采集日期
        normalized_collection_date = self._normalize_collection_date(groups['collection_date'])
        
        # 构建完整路径
        if mushroom_id and date_folder:
            file_path = f"{mushroom_id}/{date_folder}/{filename}"
        else:
            file_path = f"{groups['mushroom_id']}/{normalized_collection_date}/{filename}"
        
        return MushroomImageInfo(
            mushroom_id=groups['mushroom_id'],
            collection_ip=groups['collection_ip'],
            collection_date=normalized_collection_date,
            detailed_time=groups['detailed_time'],
            file_name=filename,
            file_path=file_path,
            date_folder=date_folder or normalized_collection_date
        )
    
    def validate_path_structure(self, file_path: str) -> bool:
        """验证路径结构是否符合规范"""
        return bool(self.path_pattern.match(file_path))


class MushroomImageProcessor:
    """蘑菇图像处理器"""
    
    def __init__(self):
        self.parser = MushroomImagePathParser()
        self.session = sessionmaker(bind=pgsql_engine)()
        
        # 初始化MinIO客户端
        from utils.minio_client import create_minio_client
        self.minio_client = create_minio_client()
    
    def __del__(self):
        """析构函数，关闭数据库连接"""
        if hasattr(self, 'session'):
            self.session.close()
    
    def get_mushroom_images(self, mushroom_id: str = None, date_filter: str = None) -> List[MushroomImageInfo]:
        """
        获取蘑菇图像列表
        
        Args:
            mushroom_id: 蘑菇库号过滤
            date_filter: 日期过滤 (YYYYMMDD)
            
        Returns:
            蘑菇图像信息列表
        """
        # 构建前缀过滤 - 图片路径直接以蘑菇库号开头
        prefix = ""
        if mushroom_id:
            prefix = f"{mushroom_id}/"
            if date_filter:
                prefix += f"{date_filter}/"
        
        # 从MinIO获取图像列表
        image_files = self.minio_client.list_images(prefix=prefix)
        
        mushroom_images = []
        for image_path in image_files:
            image_info = self.parser.parse_path(image_path)
            if image_info:
                # 应用过滤条件
                if mushroom_id and image_info.mushroom_id != mushroom_id:
                    continue
                if date_filter and image_info.date_folder != date_filter:
                    continue
                
                mushroom_images.append(image_info)
        
        # 按时间排序
        mushroom_images.sort(key=lambda x: x.collection_datetime)
        
        logger.info(f"找到 {len(mushroom_images)} 个蘑菇图像文件")
        return mushroom_images
    
    def process_single_image(self, image_info: MushroomImageInfo, description: str = None) -> bool:
        """
        处理单个图像文件
        
        Args:
            image_info: 蘑菇图像信息
            description: 图像描述
            
        Returns:
            是否处理成功
        """
        try:
            # 从MinIO获取图像
            image = self.minio_client.get_image(image_info.file_path)
            if not image:
                logger.error(f"无法从MinIO获取图像: {image_info.file_path}")
                return False
            
            # 获取图像向量
            # 临时保存图像到本地进行处理
            temp_path = Path(f"/tmp/{image_info.file_name}")
            image.save(temp_path)
            
            try:
                embedding = get_image_embedding(temp_path)
                if embedding is None:
                    logger.error(f"向量化失败: {image_info.file_path}")
                    return False
            finally:
                # 清理临时文件
                if temp_path.exists():
                    temp_path.unlink()
            
            # 生成描述
            if not description:
                description = self._generate_description(image_info)
            
            # 检查数据库中是否已存在
            existing = self.session.query(MushroomImageEmbedding).filter_by(
                image_path=image_info.file_path
            ).first()
            
            if existing:
                # 更新现有记录
                existing.embedding = embedding
                existing.description = description
                existing.file_name = image_info.file_name
                logger.info(f"更新图像记录: {image_info.file_name}")
            else:
                # 创建新记录
                new_record = MushroomImageEmbedding(
                    image_path=image_info.file_path,
                    file_name=image_info.file_name,
                    description=description,
                    embedding=embedding,
                    growth_day=self._calculate_growth_day(image_info)
                )
                self.session.add(new_record)
                logger.info(f"添加图像记录: {image_info.file_name}")
            
            self.session.commit()
            return True
            
        except Exception as e:
            logger.error(f"处理图像失败 {image_info.file_path}: {e}")
            self.session.rollback()
            return False
    
    def batch_process_images(self, mushroom_id: str = None, date_filter: str = None, 
                           batch_size: int = 10) -> Dict[str, int]:
        """
        批量处理图像
        
        Args:
            mushroom_id: 蘑菇库号过滤
            date_filter: 日期过滤
            batch_size: 批处理大小
            
        Returns:
            处理结果统计
        """
        images = self.get_mushroom_images(mushroom_id, date_filter)
        
        results = {
            'total': len(images),
            'success': 0,
            'failed': 0,
            'skipped': 0
        }
        
        for i, image_info in enumerate(images):
            logger.info(f"处理进度: {i+1}/{len(images)} - {image_info.file_name}")
            
            # 检查是否已处理
            existing = self.session.query(MushroomImageEmbedding).filter_by(
                image_path=image_info.file_path
            ).first()
            
            if existing and existing.embedding:
                logger.info(f"跳过已处理的图像: {image_info.file_name}")
                results['skipped'] += 1
                continue
            
            if self.process_single_image(image_info):
                results['success'] += 1
            else:
                results['failed'] += 1
            
            # 批量提交
            if (i + 1) % batch_size == 0:
                logger.info(f"批量提交进度: {i+1}/{len(images)}")
        
        logger.info(f"批量处理完成: {results}")
        return results
    
    def _generate_description(self, image_info: MushroomImageInfo) -> str:
        """生成图像描述"""
        collection_time = image_info.collection_datetime
        return (
            f"蘑菇库号{image_info.mushroom_id}，"
            f"采集时间{collection_time.strftime('%Y年%m月%d日 %H:%M:%S')}，"
            f"采集IP{image_info.collection_ip}，"
            f"图像文件{image_info.file_name}"
        )
    
    def _calculate_growth_day(self, image_info: MushroomImageInfo) -> Optional[int]:
        """计算生长天数（这里需要根据实际业务逻辑实现）"""
        # 这是一个示例实现，实际需要根据业务逻辑计算
        # 可能需要从数据库中获取种植开始时间等信息
        return None
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        try:
            total_records = self.session.query(MushroomImageEmbedding).count()
            
            # 按蘑菇库号统计
            mushroom_stats = {}
            records = self.session.query(MushroomImageEmbedding).all()
            
            for record in records:
                image_info = self.parser.parse_path(record.image_path)
                if image_info:
                    mushroom_id = image_info.mushroom_id
                    if mushroom_id not in mushroom_stats:
                        mushroom_stats[mushroom_id] = 0
                    mushroom_stats[mushroom_id] += 1
            
            return {
                'total_processed': total_records,
                'mushroom_distribution': mushroom_stats,
                'processing_time': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}
    
    def search_similar_images(self, query_image_path: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        搜索相似图像
        
        Args:
            query_image_path: 查询图像路径
            top_k: 返回前K个相似结果
            
        Returns:
            相似图像列表
        """
        try:
            # 获取查询图像的向量
            query_image = self.minio_client.get_image(query_image_path)
            if not query_image:
                logger.error(f"无法获取查询图像: {query_image_path}")
                return []
            
            # 临时保存并向量化
            temp_path = Path(f"/tmp/query_{os.path.basename(query_image_path)}")
            query_image.save(temp_path)
            
            try:
                query_embedding = get_image_embedding(temp_path)
                if query_embedding is None:
                    return []
            finally:
                if temp_path.exists():
                    temp_path.unlink()
            
            # 在数据库中搜索相似图像
            # 这里需要使用向量相似度搜索，具体实现取决于数据库支持
            # 暂时返回所有记录作为示例
            all_records = self.session.query(MushroomImageEmbedding).limit(top_k).all()
            
            results = []
            for record in all_records:
                image_info = self.parser.parse_path(record.image_path)
                if image_info:
                    results.append({
                        'image_info': image_info,
                        'description': record.description,
                        'similarity': 0.95,  # 示例相似度
                        'created_at': record.created_at
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"搜索相似图像失败: {e}")
            return []


def create_mushroom_processor() -> MushroomImageProcessor:
    """创建蘑菇图像处理器实例"""
    return MushroomImageProcessor()


if __name__ == "__main__":
    # 测试代码
    processor = create_mushroom_processor()
    
    # 测试路径解析
    test_path = "612/20251224/612_1921681235_20251218_20251224160000.jpg"
    image_info = processor.parser.parse_path(test_path)
    
    if image_info:
        print(f"解析成功:")
        print(f"  蘑菇库号: {image_info.mushroom_id}")
        print(f"  采集IP: {image_info.collection_ip}")
        print(f"  采集日期: {image_info.collection_date}")
        print(f"  详细时间: {image_info.detailed_time}")
        print(f"  采集时间: {image_info.collection_datetime}")
    else:
        print("路径解析失败")
    
    # 获取图像列表
    images = processor.get_mushroom_images()
    print(f"找到 {len(images)} 个图像文件")