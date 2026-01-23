#!/usr/bin/env python3
"""
CLIP推理调度器
蘑菇图像处理系统的CLIP推理功能模块
支持处理最近图片、批量处理所有图片、系统验证等功能
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加src目录到路径
current_dir = Path(__file__).parent
src_dir = current_dir.parent
sys.path.insert(0, str(src_dir))

from clip.mushroom_image_encoder import create_mushroom_encoder
from clip.recent_image_processor import create_recent_image_processor
from utils.minio_client import create_minio_client
from utils.loguru_setting import logger


def process_recent_images(args):
    """处理最近时间段的图片"""
    print("=" * 60)
    print("处理最近时间段的图片")
    print("=" * 60)
    
    try:
        # 创建共享实例
        print("🔧 初始化共享组件...")
        shared_encoder = create_mushroom_encoder()
        shared_minio_client = create_minio_client()
        
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
        
        # 使用整合的方法处理
        result = processor.get_recent_image_summary_and_process(
            hours=args.hours,
            room_ids=room_ids,
            max_images_per_room=args.max_per_room,
            save_to_db=not args.no_save,
            show_summary=True
        )
        
        # 显示结果
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
        
        return True
        
    except Exception as e:
        print(f"❌ 处理最近图片失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def process_all_images(args):
    """批量处理所有图片数据"""
    print("=" * 60)
    print("批量处理所有图片数据")
    print("=" * 60)
    
    try:
        # 创建编码器
        print("🔧 初始化蘑菇图像编码器...")
        encoder = create_mushroom_encoder()
        
        # 确定要处理的库房
        mushroom_id = args.room_id if args.room_id else None
        date_filter = args.date_filter if hasattr(args, 'date_filter') else None
        
        print(f"📊 开始批量处理图片...")
        if mushroom_id:
            print(f"   指定库房: {mushroom_id}")
        else:
            print(f"   处理所有库房")
        
        if date_filter:
            print(f"   日期过滤: {date_filter}")
        else:
            print(f"   处理所有日期")
        
        print(f"   批处理大小: {args.batch_size}")
        print(f"   保存到数据库: {'是' if not args.no_save else '否'}")
        
        # 执行批量处理
        stats = encoder.batch_process_images(
            mushroom_id=mushroom_id,
            date_filter=date_filter,
            batch_size=args.batch_size
        )
        
        # 显示结果
        print(f"\n📈 批量处理结果:")
        print(f"   总计: {stats['total']}张")
        print(f"   成功: {stats['success']}张")
        print(f"   失败: {stats['failed']}张")
        print(f"   跳过: {stats['skipped']}张")
        
        if stats['total'] > 0:
            success_rate = (stats['success'] / stats['total']) * 100
            print(f"   成功率: {success_rate:.1f}%")
        
        # 获取处理统计
        print(f"\n📊 获取处理统计信息...")
        processing_stats = encoder.get_processing_statistics()
        
        if processing_stats:
            print(f"   数据库总记录: {processing_stats.get('total_processed', 0)}")
            print(f"   有环境控制的记录: {processing_stats.get('with_environmental_control', 0)}")
            
            room_dist = processing_stats.get('room_distribution', {})
            if room_dist:
                print(f"   库房分布:")
                for room_id, count in sorted(room_dist.items()):
                    print(f"     库房{room_id}: {count}张")
        
        return True
        
    except Exception as e:
        print(f"❌ 批量处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def validate_system(args):
    """系统验证"""
    print("=" * 60)
    print("系统功能验证")
    print("=" * 60)
    
    try:
        # 创建编码器
        print("🔧 初始化蘑菇图像编码器...")
        encoder = create_mushroom_encoder()
        
        max_per_mushroom = getattr(args, 'max_per_room', 3)
        
        print(f"🔍 开始系统验证（每个库房最多处理 {max_per_mushroom} 张图片）...")
        
        # 执行系统验证
        validation_results = encoder.validate_system_with_limited_samples(
            max_per_mushroom=max_per_mushroom
        )
        
        # 显示结果
        print(f"\n📊 验证结果:")
        print(f"   总库房数: {validation_results['total_mushrooms']}")
        print(f"   库房列表: {validation_results['mushroom_ids']}")
        print(f"   总处理: {validation_results['total_processed']}")
        print(f"   总成功: {validation_results['total_success']}")
        print(f"   总失败: {validation_results['total_failed']}")
        print(f"   总跳过: {validation_results['total_skipped']}")
        print(f"   无环境数据: {validation_results['total_no_env_data']}")
        
        print(f"\n📋 各库房详情:")
        for mushroom_id, stats in validation_results['processed_per_mushroom'].items():
            print(f"   库房{mushroom_id}: 处理={stats['processed']}, 成功={stats['success']}, "
                  f"失败={stats['failed']}, 无环境数据={stats['no_env_data']}")
        
        return True
        
    except Exception as e:
        print(f"❌ 系统验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='CLIP推理调度器 - 蘑菇图像处理系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 处理最近1小时的图片
  python src/clip/clip_inference_scheduler.py recent --hours 1
  
  # 处理指定库房最近2小时的图片
  python src/clip/clip_inference_scheduler.py recent --hours 2 --room-id 7
  
  # 批量处理所有图片
  python src/clip/clip_inference_scheduler.py batch-all
  
  # 批量处理指定库房的图片
  python src/clip/clip_inference_scheduler.py batch-all --room-id 7
  
  # 批量处理指定日期的图片
  python src/clip/clip_inference_scheduler.py batch-all --date-filter 20251231
  
  # 系统验证（每个库房处理3张图片）
  python src/clip/clip_inference_scheduler.py validate --max-per-room 3
  
  # 测试模式（不保存到数据库）
  python src/clip/clip_inference_scheduler.py recent --hours 1 --no-save
        """
    )
    
    # 添加子命令
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # 最近图片处理命令
    recent_parser = subparsers.add_parser('recent', help='处理最近时间段的图片')
    recent_parser.add_argument('--hours', type=int, default=1, help='查询最近多少小时的图片 (默认: 1)')
    recent_parser.add_argument('--room-id', type=str, help='指定库房号')
    recent_parser.add_argument('--room-ids', nargs='+', help='指定多个库房号，用空格分隔')
    recent_parser.add_argument('--max-per-room', type=int, help='每个库房最多处理多少张图片')
    recent_parser.add_argument('--no-save', action='store_true', help='不保存到数据库，仅测试处理')
    
    # 批量处理所有图片命令
    batch_parser = subparsers.add_parser('batch-all', help='批量处理所有图片数据')
    batch_parser.add_argument('--room-id', type=str, help='指定库房号，如果不指定则处理所有库房')
    batch_parser.add_argument('--date-filter', type=str, help='日期过滤 (YYYYMMDD格式)')
    batch_parser.add_argument('--batch-size', type=int, default=10, help='批处理大小 (默认: 10)')
    batch_parser.add_argument('--no-save', action='store_true', help='不保存到数据库，仅测试处理')
    
    # 系统验证命令
    validate_parser = subparsers.add_parser('validate', help='系统功能验证')
    validate_parser.add_argument('--max-per-room', type=int, default=3, help='每个库房最多处理多少张图片 (默认: 3)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    print("🍄 CLIP推理调度器 - 蘑菇图像处理系统")
    print(f"⏰ 开始时间: {datetime.now()}")
    
    try:
        success = False
        
        if args.command == 'recent':
            success = process_recent_images(args)
        elif args.command == 'batch-all':
            success = process_all_images(args)
        elif args.command == 'validate':
            success = validate_system(args)
        else:
            print(f"❌ 未知命令: {args.command}")
            parser.print_help()
            return
        
        print(f"\n⏰ 结束时间: {datetime.now()}")
        
        if success:
            print("✅ 处理完成！")
        else:
            print("❌ 处理失败！")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
