"""
蘑菇图像处理示例
演示如何使用蘑菇图像处理器进行图像向量化和管理
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from clip.mushroom_image_processor import create_mushroom_processor, MushroomImagePathParser
from utils.minio_service import create_minio_service
from loguru import logger


def demonstrate_path_parsing():
    """演示路径解析功能"""
    logger.info("=" * 60)
    logger.info("蘑菇图像路径解析演示")
    logger.info("=" * 60)
    
    parser = MushroomImagePathParser()
    
    # 测试路径示例
    test_paths = [
        "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg",
        "mogu/613/20251225/613_1921681236_20251219_20251225090000.jpg",
        "mogu/614/20251226/614_1921681237_20251220_20251226120000.jpg",
    ]
    
    for path in test_paths:
        logger.info(f"解析路径: {path}")
        image_info = parser.parse_path(path)
        
        if image_info:
            logger.info(f"  ✅ 解析成功:")
            logger.info(f"     蘑菇库号: {image_info.mushroom_id}")
            logger.info(f"     采集IP: {image_info.collection_ip}")
            logger.info(f"     采集日期: {image_info.collection_date}")
            logger.info(f"     详细时间: {image_info.detailed_time}")
            logger.info(f"     采集时间: {image_info.collection_datetime}")
            logger.info(f"     日期文件夹: {image_info.date_folder}")
        else:
            logger.error(f"  ❌ 解析失败")
        
        logger.info("-" * 40)
    
    # 测试文件名解析
    logger.info("文件名解析测试:")
    filename = "612_1921681235_20251218_20251224160000.jpg"
    image_info = parser.parse_filename(filename, mushroom_id="612", date_folder="20251224")
    
    if image_info:
        logger.info(f"  ✅ 文件名解析成功: {image_info.file_path}")
    else:
        logger.error(f"  ❌ 文件名解析失败")


def demonstrate_image_discovery():
    """演示图像发现功能"""
    logger.info("=" * 60)
    logger.info("蘑菇图像发现演示")
    logger.info("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 获取所有蘑菇图像
        logger.info("获取所有蘑菇图像...")
        all_images = processor.get_mushroom_images()
        
        if all_images:
            logger.info(f"发现 {len(all_images)} 个蘑菇图像文件")
            
            # 显示前5个图像信息
            for i, image_info in enumerate(all_images[:5]):
                logger.info(f"图像 {i+1}:")
                logger.info(f"  文件名: {image_info.file_name}")
                logger.info(f"  蘑菇库号: {image_info.mushroom_id}")
                logger.info(f"  采集时间: {image_info.collection_datetime}")
                logger.info(f"  完整路径: {image_info.file_path}")
            
            # 按蘑菇库号分组统计
            mushroom_groups = {}
            for image_info in all_images:
                mushroom_id = image_info.mushroom_id
                if mushroom_id not in mushroom_groups:
                    mushroom_groups[mushroom_id] = []
                mushroom_groups[mushroom_id].append(image_info)
            
            logger.info(f"蘑菇库号分布:")
            for mushroom_id, images in mushroom_groups.items():
                logger.info(f"  库号 {mushroom_id}: {len(images)} 张图片")
        else:
            logger.warning("未发现蘑菇图像文件")
            
            # 尝试上传测试图像
            logger.info("尝试上传测试图像...")
            upload_test_image(processor)
        
    except Exception as e:
        logger.error(f"图像发现失败: {e}")


def upload_test_image(processor):
    """上传测试图像"""
    try:
        # 检查本地是否有测试图像
        test_image_path = project_root / "data" / "m1.jpg"
        
        if test_image_path.exists():
            logger.info(f"发现本地测试图像: {test_image_path}")
            
            # 生成符合蘑菇命名规范的文件名
            current_time = datetime.now()
            mushroom_id = "612"
            collection_ip = "1921681235"
            collection_date = current_time.strftime("%Y%m%d")
            detailed_time = current_time.strftime("%Y%m%d%H%M%S")
            date_folder = collection_date
            
            # 构建MinIO路径
            minio_filename = f"{mushroom_id}_{collection_ip}_{collection_date}_{detailed_time}.jpg"
            minio_path = f"mogu/{mushroom_id}/{date_folder}/{minio_filename}"
            
            # 上传到MinIO
            minio_service = create_minio_service()
            if minio_service.client.upload_image(str(test_image_path), minio_path):
                logger.info(f"测试图像上传成功: {minio_path}")
                
                # 重新获取图像列表
                images = processor.get_mushroom_images()
                logger.info(f"上传后发现 {len(images)} 个图像文件")
            else:
                logger.error("测试图像上传失败")
        else:
            logger.warning(f"本地测试图像不存在: {test_image_path}")
            
    except Exception as e:
        logger.error(f"上传测试图像失败: {e}")


def demonstrate_image_processing():
    """演示图像处理功能"""
    logger.info("=" * 60)
    logger.info("蘑菇图像处理演示")
    logger.info("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 获取图像列表
        images = processor.get_mushroom_images()
        
        if not images:
            logger.warning("没有找到蘑菇图像文件，跳过处理演示")
            return
        
        # 处理单个图像
        logger.info("处理单个图像...")
        first_image = images[0]
        logger.info(f"处理图像: {first_image.file_name}")
        
        success = processor.process_single_image(
            first_image, 
            description=f"蘑菇库号{first_image.mushroom_id}的测试图像，采集时间{first_image.collection_datetime}"
        )
        
        if success:
            logger.info("✅ 单个图像处理成功")
        else:
            logger.error("❌ 单个图像处理失败")
        
        # 批量处理（限制数量避免过长时间）
        if len(images) > 1:
            logger.info("批量处理图像...")
            
            # 只处理前3张图像作为演示
            sample_images = images[:3]
            results = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
            
            for image_info in sample_images:
                logger.info(f"处理: {image_info.file_name}")
                if processor.process_single_image(image_info):
                    results['success'] += 1
                else:
                    results['failed'] += 1
                results['total'] += 1
            
            logger.info(f"批量处理结果: {results}")
        
        # 获取处理统计
        logger.info("获取处理统计...")
        stats = processor.get_processing_statistics()
        logger.info(f"处理统计: {stats}")
        
    except Exception as e:
        logger.error(f"图像处理演示失败: {e}")


def demonstrate_filtering():
    """演示过滤功能"""
    logger.info("=" * 60)
    logger.info("蘑菇图像过滤演示")
    logger.info("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 按蘑菇库号过滤
        logger.info("按蘑菇库号过滤...")
        mushroom_612_images = processor.get_mushroom_images(mushroom_id="612")
        logger.info(f"蘑菇库号612的图像: {len(mushroom_612_images)} 张")
        
        # 按日期过滤
        logger.info("按日期过滤...")
        today = datetime.now().strftime("%Y%m%d")
        today_images = processor.get_mushroom_images(date_filter=today)
        logger.info(f"今天的图像: {len(today_images)} 张")
        
        # 组合过滤
        logger.info("组合过滤...")
        combined_images = processor.get_mushroom_images(mushroom_id="612", date_filter=today)
        logger.info(f"蘑菇库号612今天的图像: {len(combined_images)} 张")
        
        # 显示过滤结果
        if combined_images:
            for image_info in combined_images:
                logger.info(f"  - {image_info.file_name} ({image_info.collection_datetime})")
        
    except Exception as e:
        logger.error(f"过滤演示失败: {e}")


def demonstrate_search():
    """演示相似图像搜索"""
    logger.info("=" * 60)
    logger.info("相似图像搜索演示")
    logger.info("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 获取图像列表
        images = processor.get_mushroom_images()
        
        if not images:
            logger.warning("没有找到图像文件，跳过搜索演示")
            return
        
        # 使用第一张图像作为查询
        query_image = images[0]
        logger.info(f"使用查询图像: {query_image.file_name}")
        
        # 搜索相似图像
        similar_images = processor.search_similar_images(query_image.file_path, top_k=3)
        
        if similar_images:
            logger.info(f"找到 {len(similar_images)} 张相似图像:")
            for i, result in enumerate(similar_images):
                image_info = result['image_info']
                similarity = result['similarity']
                logger.info(f"  {i+1}. {image_info.file_name} (相似度: {similarity:.3f})")
        else:
            logger.warning("未找到相似图像")
        
    except Exception as e:
        logger.error(f"搜索演示失败: {e}")


def main():
    """主函数"""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    logger.info("蘑菇图像处理系统演示")
    logger.info(f"项目路径: {project_root}")
    
    try:
        # 1. 路径解析演示
        demonstrate_path_parsing()
        
        # 2. 图像发现演示
        demonstrate_image_discovery()
        
        # 3. 图像处理演示
        demonstrate_image_processing()
        
        # 4. 过滤功能演示
        demonstrate_filtering()
        
        # 5. 搜索功能演示
        demonstrate_search()
        
        logger.info("=" * 60)
        logger.info("所有演示完成")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"演示执行失败: {e}")


if __name__ == "__main__":
    main()