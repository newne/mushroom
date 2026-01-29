#!/usr/bin/env python3
"""
处理最近图片的命令行工具 - 优化版本
整合摘要和处理过程，避免重复查询和初始化
"""

import sys
import argparse
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from vision.recent_image_processor import create_recent_image_processor
from vision.mushroom_image_encoder import create_mushroom_encoder
from utils.minio_client import create_minio_client
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description='处理最近时间段内的蘑菇图片 - 优化版本')
    
    parser.add_argument(
        '--hours', 
        type=int, 
        default=1, 
        help='查询最近多少小时的图片 (默认: 1)'
    )
    
    parser.add_argument(
        '--room-id',

        type=str, 
        help='指定库房号，如果不指定则处理所有库房'
    )
    
    parser.add_argument(
        '--max-per-room', 
        type=int, 
        help='每个库房最多处理多少张图片'
    )
    
    parser.add_argument(
        '--no-save', 
        action='store_true', 
        help='不保存到数据库，仅测试处理'
    )
    
    parser.add_argument(
        '--summary-only', 
        action='store_true', 
        help='仅显示摘要信息，不进行处理'
    )
    
    parser.add_argument(
        '--room-ids', 
        nargs='+', 
        help='指定多个库房号，用空格分隔'
    )
    
    parser.add_argument(
        '--batch-size', 
        type=int, 
        default=10, 
        help='批处理大小，每批处理多少张图片 (默认: 10)'
    )
    
    parser.add_argument(
        '--enable-batch', 
        action='store_true', 
        help='启用批处理模式，提升处理效率'
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("蘑菇图片处理工具 - 优化版本")
    print("=" * 60)
    
    try:
        # 创建共享实例，避免重复初始化
        print("🔧 初始化共享组件...")
        shared_encoder = create_mushroom_encoder()
        shared_minio_client = create_minio_client()
        
        # 创建处理器，使用共享实例
        processor = create_recent_image_processor(
            shared_encoder=shared_encoder,
            shared_minio_client=shared_minio_client
        )
        
        # 确定要处理的库房
        room_ids = None
        if args.room_id:
            room_ids = [args.room_id]
        elif args.room_ids:
            room_ids = args.room_ids
        
        # 处理参数
        save_to_db = not args.no_save
        
        if args.summary_only:
            # 仅显示摘要
            print(f"\n📊 获取最近 {args.hours} 小时的图片摘要...")
            summary = processor.get_recent_image_summary(hours=args.hours)
            
            print(f"   总图片数: {summary['total_images']}")
            
            if summary['total_images'] > 0:
                print(f"   时间范围: {summary['time_range']['start']} ~ {summary['time_range']['end']}")
                print("   各库房统计:")
                for room_id, stats in sorted(summary['room_stats'].items()):
                    print(f"     库房{room_id}: {stats['count']}张 (最新: {stats['latest_time']})")
            else:
                print(f"   未找到最近 {args.hours} 小时的图片")
            
            print("\n✅ 摘要信息显示完成")
            return
        
        # 使用整合的方法：一次调用完成摘要和处理
        print(f"\n🚀 整合处理最近 {args.hours} 小时的图片...")
        
        # 批处理配置
        batch_config = {
            'enabled': args.enable_batch,
            'batch_size': args.batch_size
        }
        
        result = processor.get_recent_image_summary_and_process(
            hours=args.hours,
            room_ids=room_ids,
            max_images_per_room=args.max_per_room,
            save_to_db=save_to_db,
            show_summary=True,
            batch_config=batch_config
        )
        
        # 显示处理结果
        processing = result['processing']
        
        print(f"\n📈 处理结果:")
        print(f"   找到: {processing['total_found']}张")
        print(f"   处理: {processing['total_processed']}张")
        print(f"   成功: {processing['total_success']}张")
        print(f"   失败: {processing['total_failed']}张")
        print(f"   跳过: {processing['total_skipped']}张")
        
        if processing['room_stats']:
            print(f"\n📋 各库房详情:")
            for room_id, stats in sorted(processing['room_stats'].items()):
                print(f"   库房{room_id}: 找到={stats['found']}, 处理={stats['processed']}, "
                      f"成功={stats['success']}, 失败={stats['failed']}, 跳过={stats['skipped']}")
        
        if args.no_save:
            print("\n⚠️ 注意: 使用了 --no-save 参数，结果未保存到数据库")
        
        if args.enable_batch:
            print(f"\n🚀 批处理模式: 启用 (批大小: {args.batch_size})")
            if 'batch_stats' in result:
                batch_stats = result['batch_stats']
                print(f"   批处理统计: 总批数={batch_stats.get('total_batches', 0)}, "
                      f"平均批大小={batch_stats.get('avg_batch_size', 0):.1f}")
        
        print(f"\n✅ 整合处理完成! 时间: {datetime.now()}")
        optimization_msg = "🎯 优化效果: 避免了重复初始化和重复查询"
        if args.enable_batch:
            optimization_msg += f"，启用批处理 (批大小: {args.batch_size}) 提升了处理效率"
        else:
            optimization_msg += "，提升了处理效率"
        print(optimization_msg)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()