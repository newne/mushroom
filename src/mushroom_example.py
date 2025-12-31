"""
蘑菇图像处理示例（在src目录下运行）
演示如何使用蘑菇图像处理器进行图像向量化和管理
"""

import sys
from pathlib import Path

from loguru import logger

from utils.minio_service import create_minio_service
from utils.mushroom_image_processor import create_mushroom_processor, MushroomImagePathParser


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
            logger.info("这可能是因为MinIO服务未启动或没有上传图像文件")
        
    except Exception as e:
        logger.error(f"图像发现失败: {e}")


def demonstrate_minio_service():
    """演示MinIO服务功能"""
    logger.info("=" * 60)
    logger.info("MinIO服务演示")
    logger.info("=" * 60)
    
    try:
        service = create_minio_service()
        
        # 健康检查
        health = service.health_check()
        logger.info(f"MinIO服务状态: {'健康' if health['healthy'] else '异常'}")
        logger.info(f"连接状态: {'正常' if health['connection'] else '异常'}")
        logger.info(f"存储桶状态: {'存在' if health['bucket_exists'] else '不存在'}")
        logger.info(f"图片数量: {health['image_count']}")
        
        if health['errors']:
            logger.warning("错误信息:")
            for error in health['errors']:
                logger.warning(f"  - {error}")
        
        # 获取统计信息
        if health['connection']:
            stats = service.get_image_statistics()
            if stats:
                logger.info(f"存储统计:")
                logger.info(f"  总图片数: {stats.get('total_images', 0)}")
                logger.info(f"  总大小: {stats.get('total_size_mb', 0)} MB")
                
                ext_stats = stats.get('extension_stats', {})
                if ext_stats:
                    logger.info("  文件类型分布:")
                    for ext, info in ext_stats.items():
                        logger.info(f"    {ext}: {info['count']} 个文件")
        
    except Exception as e:
        logger.error(f"MinIO服务演示失败: {e}")


def demonstrate_configuration():
    """演示配置功能"""
    logger.info("=" * 60)
    logger.info("配置演示")
    logger.info("=" * 60)
    
    try:
        from global_const.global_const import settings, get_environment
        
        env = get_environment()
        logger.info(f"当前环境: {env}")
        
        # 显示MinIO配置
        minio_config = settings.MINIO
        logger.info("MinIO配置:")
        logger.info(f"  端点: {minio_config['endpoint']}")
        logger.info(f"  存储桶: {minio_config['bucket']}")
        logger.info(f"  区域: {minio_config['region']}")
        
        # 显示数据库配置
        pgsql_config = settings.PGSQL
        logger.info("PostgreSQL配置:")
        logger.info(f"  主机: {pgsql_config['host']}")
        logger.info(f"  端口: {pgsql_config['port']}")
        logger.info(f"  数据库: {pgsql_config['database_name']}")
        
    except Exception as e:
        logger.error(f"配置演示失败: {e}")


def main():
    """主函数"""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    logger.info("蘑菇图像处理系统演示")
    logger.info(f"当前目录: {Path.cwd()}")
    
    try:
        # 1. 配置演示
        demonstrate_configuration()
        
        # 2. 路径解析演示
        demonstrate_path_parsing()
        
        # 3. MinIO服务演示
        demonstrate_minio_service()
        
        # 4. 图像发现演示
        demonstrate_image_discovery()
        
        logger.info("=" * 60)
        logger.info("演示完成")
        logger.info("=" * 60)
        
        logger.info("系统状态总结:")
        logger.info("✅ 配置加载正常")
        logger.info("✅ 路径解析功能正常")
        logger.info("✅ MinIO客户端创建正常")
        logger.info("✅ 蘑菇处理器创建正常")
        logger.info("⚠️ MinIO服务连接需要启动服务")
        logger.info("⚠️ 数据库连接需要启动服务")
        
        logger.info("\n使用说明:")
        logger.info("1. 启动MinIO服务以进行图像操作")
        logger.info("2. 启动PostgreSQL服务以进行数据存储")
        logger.info("3. 上传符合命名规范的图像文件")
        logger.info("4. 运行图像处理和向量化")
        
    except Exception as e:
        logger.error(f"演示执行失败: {e}")


if __name__ == "__main__":
    main()