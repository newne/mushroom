#!/usr/bin/env python3
"""
处理最近图片的命令行工具 - 企业级重构版
功能：整合摘要和处理过程，增强健壮性与输入验证
"""

import sys
import argparse
import time
from typing import List, Optional, Set

# 统一使用 loguru，配置简单的控制台输出格式以保持 CLI 友好性
from loguru import logger

# 路径管理
from global_const.global_const import ensure_src_path
ensure_src_path()

# 延迟导入以加快帮助信息的显示速度
try:
    from vision.recent_image_processor import RecentImageProcessor, create_recent_image_processor
    from vision.mushroom_image_encoder import create_mushroom_encoder
    from utils.minio_client import create_minio_client
except ImportError as e:
    sys.stderr.write(f"❌ 关键模块导入失败: {e}\n检查 PYTHONPATH 或运行环境。\n")
    sys.exit(1)


def setup_logging(verbose: bool = False):
    """配置日志输出 (兼顾 CLI 美观性与详细调试)"""
    logger.remove()
    # 增加 name 字段以便在 verbose 模式下追踪日志来源
    if verbose:
        log_format = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        level = "DEBUG"
    else:
        log_format = "<green>{time:HH:mm:ss}</green> | <level>{message}</level>"
        level = "INFO"
        
    logger.add(sys.stdout, format=log_format, level=level)


def validate_positive_int(value: str) -> int:
    """Argparse 辅助验证函数：正整数"""
    try:
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"{value} 必须是正整数")
        return ivalue
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value} 必须是整数")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='处理最近时间段内的蘑菇图片 (企业级优化版)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # 核心参数
    parser.add_argument('--hours', type=validate_positive_int, default=1, help='查询最近多少小时的图片')
    
    # 互斥组：库房选择
    room_group = parser.add_mutually_exclusive_group()
    room_group.add_argument('--room-id', type=str, help='指定单个库房号')
    room_group.add_argument('--room-ids', nargs='+', help='指定多个库房号 (空格分隔)')
    
    # 控制参数
    parser.add_argument('--max-per-room', type=validate_positive_int, help='每个库房限制处理数量')
    parser.add_argument('--no-save', action='store_true', help='[Dry Run] 不保存结果到数据库')
    parser.add_argument('--summary-only', action='store_true', help='仅显示统计摘要，不执行处理')
    parser.add_argument('--verbose', action='store_true', help='显示详细调试日志')
    
    # 批处理参数
    batch_group = parser.add_argument_group('批处理配置')
    batch_group.add_argument('--enable-batch', action='store_true', help='启用批处理模式')
    batch_group.add_argument('--batch-size', type=validate_positive_int, default=10, help='每批次图片数量')
    
    return parser.parse_args()


def initialize_services() -> RecentImageProcessor:
    """初始化核心服务，带具体的错误上下文"""
    try:
        logger.info("🔧 初始化共享组件...")
        t0 = time.time()
        
        # 显式分离初始化步骤以便定位错误
        logger.debug("正在连接 MinIO...")
        minio_client = create_minio_client()
        
        logger.debug("正在加载 AI 编码器 (CLIP)...")
        encoder = create_mushroom_encoder()
        
        # [增强逻辑] 配置预处理过滤规则
        # 1. 设置质量评分阈值 (0-100)，过滤低质量图片
        encoder.quality_threshold = 50 
        # 2. 设置必须包含的关键词，确保图片内容相关
        encoder.required_keywords = ['mushroom', 'fungi', 'primordia', 'mycelium', 'pinhead', 'pinning', 'cluster']
        logger.info(f"启用质量与内容过滤 | 阈值: {encoder.quality_threshold} | 关键词: {encoder.required_keywords}")
        
        processor = create_recent_image_processor(
            shared_encoder=encoder,
            shared_minio_client=minio_client
        )
        
        logger.success(f"组件初始化完成 (耗时: {time.time() - t0:.2f}s)")
        return processor
        
    except Exception as e:
        logger.critical(f"服务初始化失败: {e}")
        # 这里可以添加更具体的异常类型判断
        raise


def format_summary(summary: dict, hours: int, filter_room_ids: Optional[List[str]] = None):
    """
    格式化打印摘要信息，支持按库房过滤
    
    Args:
        summary: 包含 'total_images', 'time_range', 'room_stats'
        hours: 查询的时间窗口
        filter_room_ids: 需要展示的库房列表，None 表示全部
    """
    # 预处理：计算实际展示的图片总数（如果进行了过滤）
    room_stats_all = summary.get('room_stats', {})
    
    # 确定要展示的库房
    if filter_room_ids:
        # 使用 set 进行 O(1) 查找，且注意 string 类型匹配
        target_set = set(str(rid) for rid in filter_room_ids)
        display_rooms = {k: v for k, v in room_stats_all.items() if str(k) in target_set}
        is_filtered = True
    else:
        display_rooms = room_stats_all
        is_filtered = False
        
    total_display = sum(r['count'] for r in display_rooms.values())
    
    title_suffix = f" (过滤后: {total_display} 张)" if is_filtered else ""
    logger.info(f"📊 最近 {hours} 小时图片快照{title_suffix}:")
    
    # 显示原始总数（如果不同）
    if is_filtered and total_display != summary['total_images']:
         logger.info(f"   源总数: {summary['total_images']} 张 -> 目标库房: {len(display_rooms)} 个")
    else:
         logger.info(f"   总数: {summary['total_images']} 张")

    if display_rooms:
        if 'time_range' in summary and summary['time_range']:
            tr = summary['time_range']
            # 处理时间对象可能是datetime的情况
            start_str = str(tr.get('start', 'N/A'))
            end_str = str(tr.get('end', 'N/A'))
            logger.info(f"   时间窗口: {start_str} ~ {end_str}")
            
        logger.info("   📦 分库房统计:")
        for room_id, stats in sorted(display_rooms.items()):
            logger.info(f"     🏠 库房 {room_id:<4}: {stats['count']:>3} 张 (最新: {stats['latest_time']})")
    else:
        if summary['total_images'] > 0:
             logger.warning(f"   ⚠️ 源数据中包含图片，但指定库房 {filter_room_ids} 下无数据")
        else:
             logger.warning("   ⚠️ 该时间段内未发现任何图片")


def main():
    args = parse_arguments()
    setup_logging(args.verbose)
    
    logger.info("=" * 40)
    logger.info("🍄 蘑菇图片智能处理工具 v2.0")
    logger.info("=" * 40)
    
    try:
        processor = initialize_services()
        
        # 解析库房列表
        target_room_ids: Optional[List[str]] = None
        if args.room_id:
            target_room_ids = [args.room_id]
        elif args.room_ids:
            target_room_ids = args.room_ids
            
        # 场景1: 仅摘要
        if args.summary_only:
            # 获取全量摘要
            summary = processor.get_recent_image_summary(hours=args.hours)
            # 在 CLI 层进行过滤展示，确保所见即所得
            format_summary(summary, args.hours, filter_room_ids=target_room_ids)
            return

        # 场景2: 完整处理
        logger.info(f"🚀 开始处理流程 | 窗口: {args.hours}h | 批处理: {'ON' if args.enable_batch else 'OFF'}")
        if target_room_ids:
            logger.info(f"🎯 目标库房: {target_room_ids}")
        
        batch_config = {
            'enabled': args.enable_batch,
            'batch_size': args.batch_size
        }
        
        # 关键修改：show_summary=False，完全由 CLI 接管摘要输出，避免混乱
        result = processor.get_recent_image_summary_and_process(
            hours=args.hours,
            room_ids=target_room_ids,
            max_images_per_room=args.max_per_room,
            save_to_db=not args.no_save,
            show_summary=False, 
            batch_config=batch_config
        )
        
        # 1. 先打印摘要 (使用返回结果中的 summary 数据)
        if 'summary' in result:
             format_summary(result['summary'], args.hours, filter_room_ids=target_room_ids)

        logger.info("-" * 40)

        # 2. 结果展示
        processing = result['processing']
        logger.info("📈 此轮运行结果统计:")
        logger.info(f"   🔍 扫描: {processing['total_found']}")
        logger.info(f"   ⚙️  执行: {processing['total_processed']}")
        logger.info(f"   ✅ 成功: {processing['total_success']}")
        logger.info(f"   ❌ 失败: {processing['total_failed']}")
        logger.info(f"   ⏭️  跳过: {processing['total_skipped']}")
        
        if args.no_save:
             logger.warning("⚠️ dry-run 模式: 结果未写入数据库")

        logger.success("处理任务圆满结束")

    except KeyboardInterrupt:
        logger.warning("\n⛔ 用户终止了操作")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"运行时发生未预期的错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
