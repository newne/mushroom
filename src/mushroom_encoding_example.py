"""
蘑菇图像编码示例
演示如何使用CLIP模型对蘑菇图像进行编码并获取环境参数
"""

import sys
from pathlib import Path

from loguru import logger

from utils.create_table import create_tables
from utils.mushroom_image_encoder import create_mushroom_encoder


def demonstrate_single_image_encoding():
    """演示单个图像编码"""
    logger.info("=" * 60)
    logger.info("单个图像编码演示")
    logger.info("=" * 60)
    
    try:
        encoder = create_mushroom_encoder()
        
        # 获取第一张图像进行演示
        all_images = encoder.processor.get_mushroom_images()
        
        if not all_images:
            logger.warning("未找到蘑菇图像文件")
            return
        
        # 选择第一张图像
        image_info = all_images[0]
        logger.info(f"选择图像: {image_info.file_name}")
        
        # 处理图像
        result = encoder.process_single_image(image_info, save_to_db=True)
        
        if result:
            logger.info("✅ 图像编码成功")
            logger.info(f"  文件名: {result['image_info'].file_name}")
            logger.info(f"  蘑菇库号: {result['image_info'].mushroom_id}")
            logger.info(f"  采集IP: {result['image_info'].collection_ip}")
            logger.info(f"  采集时间: {result['time_info']['collection_datetime']}")
            logger.info(f"  向量维度: {len(result['embedding'])}")
            logger.info(f"  保存到数据库: {result.get('saved_to_db', False)}")
            
            # 显示时间信息
            logger.info("时间信息:")
            for key, value in result['time_info'].items():
                logger.info(f"  {key}: {value}")
            
            # 显示环境参数
            if result['environmental_data']:
                logger.info("环境参数:")
                for param, data in result['environmental_data'].items():
                    if data:
                        logger.info(f"  {param}:")
                        logger.info(f"    平均值: {data['mean']:.2f}")
                        logger.info(f"    最小值: {data['min']:.2f}")
                        logger.info(f"    最大值: {data['max']:.2f}")
                        logger.info(f"    数据点数: {data['count']}")
                    else:
                        logger.info(f"  {param}: 无数据")
            else:
                logger.warning("  未获取到环境参数")
        else:
            logger.error("❌ 图像编码失败")
            
    except Exception as e:
        logger.error(f"单个图像编码演示失败: {e}")


def demonstrate_batch_encoding():
    """演示批量图像编码"""
    logger.info("=" * 60)
    logger.info("批量图像编码演示")
    logger.info("=" * 60)
    
    try:
        encoder = create_mushroom_encoder()
        
        # 批量处理前5张图像
        logger.info("开始批量编码前5张图像...")
        
        stats = encoder.batch_process_images(batch_size=5)
        
        logger.info("批量编码完成:")
        logger.info(f"  总计: {stats['total']}")
        logger.info(f"  成功: {stats['success']}")
        logger.info(f"  失败: {stats['failed']}")
        logger.info(f"  跳过: {stats['skipped']}")
        
    except Exception as e:
        logger.error(f"批量图像编码演示失败: {e}")


def demonstrate_processing_statistics():
    """演示处理统计信息"""
    logger.info("=" * 60)
    logger.info("处理统计信息演示")
    logger.info("=" * 60)
    
    try:
        encoder = create_mushroom_encoder()
        
        stats = encoder.get_processing_statistics()
        
        if stats:
            logger.info("处理统计:")
            logger.info(f"  已处理图像总数: {stats.get('total_processed', 0)}")
            logger.info(f"  含环境数据的图像: {stats.get('with_environmental_data', 0)}")
            
            mushroom_dist = stats.get('mushroom_distribution', {})
            if mushroom_dist:
                logger.info("  蘑菇库号分布:")
                for mushroom_id, count in mushroom_dist.items():
                    logger.info(f"    库号 {mushroom_id}: {count} 张图片")
            
            date_dist = stats.get('date_distribution', {})
            if date_dist:
                logger.info("  日期分布:")
                for date, count in sorted(date_dist.items()):
                    logger.info(f"    {date}: {count} 张图片")
        else:
            logger.warning("无法获取处理统计信息")
            
    except Exception as e:
        logger.error(f"获取处理统计信息失败: {e}")


def demonstrate_mushroom_filter():
    """演示按蘑菇库号过滤编码"""
    logger.info("=" * 60)
    logger.info("按蘑菇库号过滤编码演示")
    logger.info("=" * 60)
    
    try:
        encoder = create_mushroom_encoder()
        
        # 获取所有图像，找到第一个蘑菇库号
        all_images = encoder.processor.get_mushroom_images()
        if not all_images:
            logger.warning("未找到蘑菇图像文件")
            return
        
        # 选择第一个蘑菇库号
        target_mushroom_id = all_images[0].mushroom_id
        logger.info(f"选择蘑菇库号: {target_mushroom_id}")
        
        # 按库号过滤处理
        stats = encoder.batch_process_images(
            mushroom_id=target_mushroom_id,
            batch_size=3
        )
        
        logger.info(f"库号 {target_mushroom_id} 编码完成:")
        logger.info(f"  总计: {stats['total']}")
        logger.info(f"  成功: {stats['success']}")
        logger.info(f"  失败: {stats['failed']}")
        logger.info(f"  跳过: {stats['skipped']}")
        
    except Exception as e:
        logger.error(f"按蘑菇库号过滤编码演示失败: {e}")


def demonstrate_database_operations():
    """演示数据库操作"""
    logger.info("=" * 60)
    logger.info("数据库操作演示")
    logger.info("=" * 60)
    
    try:
        # 确保数据库表存在
        logger.info("检查并创建数据库表...")
        create_tables()
        
        encoder = create_mushroom_encoder()
        
        # 检查数据库中的记录
        from sqlalchemy.orm import sessionmaker
        from utils.create_table import MushroomImageEmbedding
        from global_const.global_const import pgsql_engine
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 查询记录数
            total_count = session.query(MushroomImageEmbedding).count()
            logger.info(f"数据库中已有 {total_count} 条记录")
            
            # 查询最近的几条记录
            recent_records = session.query(MushroomImageEmbedding).order_by(
                MushroomImageEmbedding.created_at.desc()
            ).limit(3).all()
            
            if recent_records:
                logger.info("最近的记录:")
                for record in recent_records:
                    logger.info(f"  {record.file_name} - 库号: {record.mushroom_id} - "
                               f"时间: {record.collection_datetime}")
                    
                    # 检查是否有环境数据
                    if record.environmental_data:
                        logger.info(f"    含环境数据: 是")
                    else:
                        logger.info(f"    含环境数据: 否")
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"数据库操作演示失败: {e}")


def main():
    """主函数"""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    logger.info("蘑菇图像编码系统演示")
    logger.info(f"当前目录: {Path.cwd()}")
    
    try:
        # 1. 数据库操作演示
        demonstrate_database_operations()
        
        # 2. 单个图像编码演示
        demonstrate_single_image_encoding()
        
        # 3. 批量编码演示
        demonstrate_batch_encoding()
        
        # 4. 处理统计演示
        demonstrate_processing_statistics()
        
        # 5. 按库号过滤演示
        demonstrate_mushroom_filter()
        
        logger.info("=" * 60)
        logger.info("所有演示完成")
        logger.info("=" * 60)
        
        logger.info("系统功能总结:")
        logger.info("✅ CLIP模型图像编码")
        logger.info("✅ 蘑菇图像路径解析")
        logger.info("✅ 时间信息提取")
        logger.info("✅ 环境参数获取")
        logger.info("✅ 数据库存储")
        logger.info("✅ 批量处理")
        logger.info("✅ 统计分析")
        
        logger.info("\n使用说明:")
        logger.info("1. 使用CLI工具进行批量编码: python scripts/mushroom_cli.py encode")
        logger.info("2. 编码单个图像: python scripts/mushroom_cli.py encode-single <image_path>")
        logger.info("3. 查看统计信息: python scripts/mushroom_cli.py stats")
        logger.info("4. 健康检查: python scripts/mushroom_cli.py health")
        
    except Exception as e:
        logger.error(f"演示执行失败: {e}")


if __name__ == "__main__":
    main()