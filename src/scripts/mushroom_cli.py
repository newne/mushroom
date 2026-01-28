#!/usr/bin/env python3
"""
蘑菇图像处理命令行工具
提供便捷的命令行接口来执行蘑菇图像处理任务
"""

import sys
import argparse
import json
import os
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path, BASE_DIR
ensure_src_path()
os.chdir(str(BASE_DIR))

from vision.mushroom_image_processor import create_mushroom_processor
from utils.minio_service import create_minio_service
from loguru import logger


def setup_logging(verbose: bool = False):
    """设置日志"""
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")


def cmd_list_images(args):
    """列出图像文件"""
    processor = create_mushroom_processor()
    
    images = processor.get_mushroom_images(
        mushroom_id=args.mushroom_id,
        date_filter=args.date
    )
    
    if not images:
        logger.info("未找到匹配的图像文件")
        return
    
    logger.info(f"找到 {len(images)} 个图像文件:")
    
    for i, image_info in enumerate(images):
        print(f"{i+1:3d}. {image_info.file_name}")
        if args.verbose:
            print(f"     路径: {image_info.file_path}")
            print(f"     蘑菇库号: {image_info.mushroom_id}")
            print(f"     采集时间: {image_info.collection_datetime}")
            print(f"     采集IP: {image_info.collection_ip}")
            print()


def cmd_process_images(args):
    """处理图像文件"""
    processor = create_mushroom_processor()
    
    if args.single_file:
        # 处理单个文件
        logger.info(f"处理单个文件: {args.single_file}")
        
        # 解析文件路径
        image_info = processor.parser.parse_path(args.single_file)
        if not image_info:
            logger.error(f"无法解析文件路径: {args.single_file}")
            return
        
        success = processor.process_single_image(image_info, description=args.description)
        if success:
            logger.info("✅ 文件处理成功")
        else:
            logger.error("❌ 文件处理失败")
    else:
        # 批量处理
        logger.info("开始批量处理...")
        
        results = processor.batch_process_images(
            mushroom_id=args.mushroom_id,
            date_filter=args.date,
            batch_size=args.batch_size
        )
        
        logger.info("批量处理完成:")
        logger.info(f"  总计: {results['total']}")
        logger.info(f"  成功: {results['success']}")
        logger.info(f"  失败: {results['failed']}")
        logger.info(f"  跳过: {results['skipped']}")


def cmd_stats(args):
    """显示统计信息"""
    processor = create_mushroom_processor()
    
    # 获取处理统计
    stats = processor.get_processing_statistics()
    
    if not stats:
        logger.warning("无法获取统计信息")
        return
    
    print("蘑菇图像处理统计:")
    print(f"  总处理数量: {stats.get('total_processed', 0)}")
    print(f"  处理时间: {stats.get('processing_time', 'N/A')}")
    
    mushroom_dist = stats.get('mushroom_distribution', {})
    if mushroom_dist:
        print("  蘑菇库号分布:")
        for mushroom_id, count in sorted(mushroom_dist.items()):
            print(f"    库号 {mushroom_id}: {count} 张图片")
    
    # MinIO统计
    minio_service = create_minio_service()
    minio_stats = minio_service.get_image_statistics()
    
    if minio_stats:
        print(f"\nMinIO存储统计:")
        print(f"  总图片数: {minio_stats.get('total_images', 0)}")
        print(f"  总大小: {minio_stats.get('total_size_mb', 0)} MB")
        
        ext_stats = minio_stats.get('extension_stats', {})
        if ext_stats:
            print("  文件类型分布:")
            for ext, info in ext_stats.items():
                print(f"    {ext}: {info['count']} 个文件")


def cmd_search(args):
    """搜索相似图像"""
    processor = create_mushroom_processor()
    
    logger.info(f"搜索与 {args.query_image} 相似的图像...")
    
    results = processor.search_similar_images(args.query_image, top_k=args.top_k)
    
    if not results:
        logger.info("未找到相似图像")
        return
    
    logger.info(f"找到 {len(results)} 张相似图像:")
    
    for i, result in enumerate(results):
        image_info = result['image_info']
        similarity = result.get('similarity', 0)
        
        print(f"{i+1}. {image_info.file_name} (相似度: {similarity:.3f})")
        if args.verbose:
            print(f"   路径: {image_info.file_path}")
            print(f"   描述: {result.get('description', 'N/A')}")
            print(f"   时间: {result.get('created_at', 'N/A')}")
            print()


def cmd_encode_images(args):
    """编码图像并获取环境参数"""
    from vision.mushroom_image_encoder import create_mushroom_encoder
    
    logger.info("开始图像编码和环境参数获取...")
    
    encoder = create_mushroom_encoder()
    
    # 批量处理图像
    stats = encoder.batch_process_images(
        mushroom_id=args.mushroom_id,
        date_filter=args.date,
        batch_size=args.batch_size
    )
    
    logger.info("图像编码完成:")
    logger.info(f"  总计: {stats['total']}")
    logger.info(f"  成功: {stats['success']}")
    logger.info(f"  失败: {stats['failed']}")
    logger.info(f"  跳过: {stats['skipped']}")
    
    # 显示处理统计
    processing_stats = encoder.get_processing_statistics()
    if processing_stats:
        logger.info("处理统计:")
        logger.info(f"  已处理图像: {processing_stats.get('total_processed', 0)}")
        logger.info(f"  含环境数据: {processing_stats.get('with_environmental_data', 0)}")
        
        mushroom_dist = processing_stats.get('mushroom_distribution', {})
        if mushroom_dist:
            logger.info("  蘑菇库号分布:")
            for mushroom_id, count in mushroom_dist.items():
                logger.info(f"    库号 {mushroom_id}: {count} 张图片")


def cmd_encode_single(args):
    """编码单个图像"""
    from vision.mushroom_image_encoder import create_mushroom_encoder
    
    logger.info(f"编码单个图像: {args.image_path}")
    
    encoder = create_mushroom_encoder()
    
    # 解析图像路径
    image_info = encoder.processor.parser.parse_path(args.image_path)
    if not image_info:
        logger.error(f"无法解析图像路径: {args.image_path}")
        return
    
    # 处理单个图像
    result = encoder.process_single_image(image_info, save_to_db=True)
    
    if result:
        logger.info("✅ 图像编码成功")
        logger.info(f"  文件名: {result['image_info'].file_name}")
        logger.info(f"  蘑菇库号: {result['image_info'].mushroom_id}")
        logger.info(f"  采集时间: {result['time_info']['collection_datetime']}")
        logger.info(f"  向量维度: {len(result['embedding'])}")
        
        if result['environmental_data']:
            logger.info("  环境参数:")
            for param, data in result['environmental_data'].items():
                if data:
                    logger.info(f"    {param}: 平均值={data['mean']:.2f}")
        else:
            logger.warning("  未获取到环境参数")
    else:
        logger.error("❌ 图像编码失败")


def cmd_validate(args):
    """验证路径格式"""
    processor = create_mushroom_processor()
    
    if args.path:
        # 验证单个路径
        is_valid = processor.parser.validate_path_structure(args.path)
        image_info = processor.parser.parse_path(args.path)
        
        print(f"路径: {args.path}")
        print(f"格式有效: {'✅' if is_valid else '❌'}")
        
        if image_info:
            print("解析结果:")
            print(f"  蘑菇库号: {image_info.mushroom_id}")
            print(f"  采集IP: {image_info.collection_ip}")
            print(f"  采集日期: {image_info.collection_date}")
            print(f"  详细时间: {image_info.detailed_time}")
            print(f"  采集时间: {image_info.collection_datetime}")
    else:
        # 验证所有图像路径
        logger.info("验证所有图像路径格式...")
        
        minio_service = create_minio_service()
        all_images = minio_service.client.list_images(prefix="mogu/")
        
        valid_count = 0
        invalid_paths = []
        
        for image_path in all_images:
            if processor.parser.validate_path_structure(image_path):
                valid_count += 1
            else:
                invalid_paths.append(image_path)
        
        print(f"验证结果:")
        print(f"  总文件数: {len(all_images)}")
        print(f"  有效路径: {valid_count}")
        print(f"  无效路径: {len(invalid_paths)}")
        
        if invalid_paths and args.verbose:
            print("无效路径列表:")
            for path in invalid_paths:
                print(f"  - {path}")


def cmd_health_check(args):
    """健康检查"""
    logger.info("执行系统健康检查...")
    
    # MinIO健康检查
    minio_service = create_minio_service()
    minio_health = minio_service.health_check()
    
    print("MinIO服务状态:")
    print(f"  连接状态: {'✅' if minio_health['connection'] else '❌'}")
    print(f"  存储桶状态: {'✅' if minio_health['bucket_exists'] else '❌'}")
    print(f"  图片数量: {minio_health['image_count']}")
    print(f"  端点: {minio_health['endpoint']}")
    
    if minio_health['errors']:
        print("  错误信息:")
        for error in minio_health['errors']:
            print(f"    - {error}")
    
    # 数据库连接检查
    try:
        processor = create_mushroom_processor()
        db_stats = processor.get_processing_statistics()
        
        print("数据库状态:")
        print(f"  连接状态: ✅")
        print(f"  已处理图片: {db_stats.get('total_processed', 0)}")
        
    except Exception as e:
        print("数据库状态:")
        print(f"  连接状态: ❌")
        print(f"  错误信息: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="蘑菇图像处理命令行工具")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出图像文件")
    list_parser.add_argument("-m", "--mushroom-id", help="蘑菇库号过滤")
    list_parser.add_argument("-d", "--date", help="日期过滤 (YYYYMMDD)")
    
    # process 命令
    process_parser = subparsers.add_parser("process", help="处理图像文件")
    process_parser.add_argument("-m", "--mushroom-id", help="蘑菇库号过滤")
    process_parser.add_argument("-d", "--date", help="日期过滤 (YYYYMMDD)")
    process_parser.add_argument("-f", "--single-file", help="处理单个文件")
    process_parser.add_argument("--description", help="图像描述")
    process_parser.add_argument("-b", "--batch-size", type=int, default=10, help="批处理大小")
    
    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="显示统计信息")
    
    # encode 命令
    encode_parser = subparsers.add_parser("encode", help="编码图像并获取环境参数")
    encode_parser.add_argument("-m", "--mushroom-id", help="蘑菇库号过滤")
    encode_parser.add_argument("-d", "--date", help="日期过滤 (YYYYMMDD)")
    encode_parser.add_argument("-b", "--batch-size", type=int, default=10, help="批处理大小")
    
    # encode-single 命令
    encode_single_parser = subparsers.add_parser("encode-single", help="编码单个图像")
    encode_single_parser.add_argument("image_path", help="图像路径")
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="搜索相似图像")
    search_parser.add_argument("query_image", help="查询图像路径")
    search_parser.add_argument("-k", "--top-k", type=int, default=5, help="返回前K个结果")
    
    # validate 命令
    validate_parser = subparsers.add_parser("validate", help="验证路径格式")
    validate_parser.add_argument("-p", "--path", help="验证单个路径")
    
    # health 命令
    health_parser = subparsers.add_parser("health", help="健康检查")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # 设置日志
    setup_logging(args.verbose)
    
    # 执行命令
    try:
        if args.command == "list":
            cmd_list_images(args)
        elif args.command == "process":
            cmd_process_images(args)
        elif args.command == "stats":
            cmd_stats(args)
        elif args.command == "encode":
            cmd_encode_images(args)
        elif args.command == "encode-single":
            cmd_encode_single(args)
        elif args.command == "search":
            cmd_search(args)
        elif args.command == "validate":
            cmd_validate(args)
        elif args.command == "health":
            cmd_health_check(args)
        else:
            logger.error(f"未知命令: {args.command}")
            
    except KeyboardInterrupt:
        logger.info("操作被用户中断")
    except Exception as e:
        logger.error(f"命令执行失败: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()