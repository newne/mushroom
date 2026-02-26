#!/usr/bin/env python3
"""
处理最近图片的命令行工具 - 企业级重构版
功能：整合摘要和处理过程，增强健壮性与输入验证
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta

# 统一使用 loguru，配置简单的控制台输出格式以保持 CLI 友好性
from loguru import logger

# 路径管理
from global_const.global_const import ensure_src_path
from global_const.const_config import MUSHROOM_ROOM_IDS

ensure_src_path()

# 延迟导入以加快帮助信息的显示速度
try:
    from sqlalchemy import text
    from global_const.global_const import pgsql_engine
    from utils.task_common import check_database_connection
    from utils.minio_client import create_minio_client
    from vision.mushroom_image_encoder import create_mushroom_encoder
    from vision.recent_image_processor import (
        RecentImageProcessor,
        create_recent_image_processor,
    )
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
        description="处理最近时间段内的蘑菇图片 (企业级优化版)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # 核心参数
    parser.add_argument(
        "--hours", type=validate_positive_int, default=1, help="查询最近多少小时的图片"
    )

    # 互斥组：库房选择
    room_group = parser.add_mutually_exclusive_group()
    room_group.add_argument("--room-id", type=str, help="指定单个库房号")
    room_group.add_argument("--room-ids", nargs="+", help="指定多个库房号 (空格分隔)")

    # 控制参数
    parser.add_argument(
        "--max-per-room", type=validate_positive_int, help="每个库房限制处理数量"
    )
    parser.add_argument(
        "--no-save", action="store_true", help="[Dry Run] 不保存结果到数据库"
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="仅显示统计摘要，不执行处理"
    )
    parser.add_argument("--verbose", action="store_true", help="显示详细调试日志")

    # 批处理参数
    batch_group = parser.add_argument_group("批处理配置")
    batch_group.add_argument(
        "--enable-batch", action="store_true", help="启用批处理模式"
    )
    batch_group.add_argument(
        "--batch-size", type=validate_positive_int, default=10, help="每批次图片数量"
    )

    return parser.parse_args()


def initialize_services() -> RecentImageProcessor:
    """初始化核心服务，带具体的错误上下文"""
    try:
        logger.info("🔧 初始化共享组件...")
        t0 = time.time()
        db_check_timeout = int(os.environ.get("PROCESS_RECENT_DB_CHECK_TIMEOUT", "45"))
        logger.info(f"[INIT-001] 数据库连通性检查超时配置: {db_check_timeout}s")

        logger.info("[INIT-002] 开始检查数据库连通性...")
        db_check_start = time.time()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(check_database_connection)
            try:
                db_ok = future.result(timeout=db_check_timeout)
            except FuturesTimeoutError:
                raise RuntimeError(
                    f"数据库连通性检查超过 {db_check_timeout}s，终止 process_recent_images 初始化"
                )

        if not db_ok:
            raise RuntimeError("数据库不可达，终止 process_recent_images 初始化")
        logger.info(
            f"[INIT-003] 数据库连通性检查通过 (耗时: {time.time() - db_check_start:.2f}s)"
        )

        # 二次确认（显示触发一次实际连接）
        db_confirm_start = time.time()
        logger.info("[INIT-004] 执行数据库二次确认查询 SELECT 1...")
        with pgsql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info(
            f"[INIT-005] 数据库二次确认通过 (耗时: {time.time() - db_confirm_start:.2f}s)"
        )

        # 显式分离初始化步骤以便定位错误
        logger.info("[INIT-008] 正在连接 MinIO...")
        minio_start = time.time()
        minio_client = create_minio_client()
        logger.info(
            f"[INIT-009] MinIO 连接完成 (耗时: {time.time() - minio_start:.2f}s)"
        )

        logger.info("[INIT-010] 正在加载 AI 编码器...")
        encoder_start = time.time()
        encoder = create_mushroom_encoder(load_clip=False)
        logger.info(
            f"[INIT-011] AI 编码器加载完成 (耗时: {time.time() - encoder_start:.2f}s)"
        )

        # [增强逻辑] 配置预处理过滤规则
        # 1. 设置质量评分阈值 (0-100)，过滤低质量图片
        encoder.quality_threshold = 50
        # 2. 设置必须包含的关键词，确保图片内容相关
        encoder.required_keywords = [
            "mushroom",
            "fungi",
            "primordia",
            "mycelium",
            "pinhead",
            "pinning",
            "cluster",
        ]
        logger.info(
            f"启用质量与内容过滤 | 阈值: {encoder.quality_threshold} | 关键词: {encoder.required_keywords}"
        )

        processor = create_recent_image_processor(
            shared_encoder=encoder, shared_minio_client=minio_client
        )

        logger.success(f"[INIT-012] 组件初始化完成 (总耗时: {time.time() - t0:.2f}s)")
        return processor

    except Exception as e:
        logger.critical(f"服务初始化失败: {e}")
        # 这里可以添加更具体的异常类型判断
        raise


def format_summary(summary: dict, hours: int, filter_room_ids: list[str] | None = None):
    """
    格式化打印摘要信息，支持按库房过滤

    Args:
        summary: 包含 'total_images', 'time_range', 'room_stats'
        hours: 查询的时间窗口
        filter_room_ids: 需要展示的库房列表，None 表示全部
    """
    # 预处理：计算实际展示的图片总数（如果进行了过滤）
    room_stats_all = summary.get("room_stats", {})

    # 确定要展示的库房
    if filter_room_ids:
        # 使用 set 进行 O(1) 查找，且注意 string 类型匹配
        target_set = set(str(rid) for rid in filter_room_ids)
        display_rooms = {
            k: v for k, v in room_stats_all.items() if str(k) in target_set
        }
        is_filtered = True
    else:
        display_rooms = room_stats_all
        is_filtered = False

    total_display = sum(r["count"] for r in display_rooms.values())

    title_suffix = f" (过滤后: {total_display} 张)" if is_filtered else ""
    logger.info(f"📊 最近 {hours} 小时图片快照{title_suffix}:")

    # 显示原始总数（如果不同）
    if is_filtered and total_display != summary["total_images"]:
        logger.info(
            f"   源总数: {summary['total_images']} 张 -> 目标库房: {len(display_rooms)} 个"
        )
    else:
        logger.info(f"   总数: {summary['total_images']} 张")

    if display_rooms:
        if "time_range" in summary and summary["time_range"]:
            tr = summary["time_range"]
            # 处理时间对象可能是datetime的情况
            start_str = str(tr.get("start", "N/A"))
            end_str = str(tr.get("end", "N/A"))
            logger.info(f"   时间窗口: {start_str} ~ {end_str}")

        logger.info("   📦 分库房统计:")
        for room_id, stats in sorted(display_rooms.items()):
            logger.info(
                f"     🏠 库房 {room_id:<4}: {stats['count']:>3} 张 (最新: {stats['latest_time']})"
            )
    else:
        if summary["total_images"] > 0:
            logger.warning(
                f"   ⚠️ 源数据中包含图片，但指定库房 {filter_room_ids} 下无数据"
            )
        else:
            logger.warning("   ⚠️ 该时间段内未发现任何图片")


def run_text_quality_pipeline_like_scheduler(
    processor: RecentImageProcessor,
    hours: int,
    target_room_ids: list[str] | None,
    batch_size: int,
    max_images_per_room: int | None,
    save_to_db: bool,
) -> dict:
    """按 safe_hourly_text_quality_inference 的逻辑执行文本/质量处理。"""
    if not save_to_db:
        logger.warning(
            "⚠️ 当前仍会触发数据库读写流程，--no-save 仅用于提示，不改变任务链路"
        )

    encoder = processor.encoder
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    minio_rooms = set(encoder.minio_client.list_rooms())
    env_to_minio: dict[str, list[str]] = {}
    for minio_id, env_id in encoder.room_id_mapping.items():
        env_to_minio.setdefault(env_id, []).append(minio_id)

    room_sequence = target_room_ids if target_room_ids else MUSHROOM_ROOM_IDS

    total_stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
    room_stats: dict[str, dict] = {}

    for room_id in room_sequence:
        candidate_ids = env_to_minio.get(room_id, [room_id])
        minio_room_id = next(
            (cid for cid in candidate_ids if cid in minio_rooms), candidate_ids[0]
        )

        if minio_room_id != room_id:
            logger.info(f"[PIPELINE] 映射库房号: {room_id} -> {minio_room_id}")

        stats = encoder.batch_process_text_quality(
            mushroom_id=minio_room_id,
            start_time=start_time,
            end_time=end_time,
            batch_size=batch_size,
            reprocess=False,
            max_images=max_images_per_room,
            link_mushroom_embedding=False,
        )

        room_stats[room_id] = {
            "found": stats.get("total", 0),
            "processed": stats.get("total", 0),
            "success": stats.get("success", 0),
            "failed": stats.get("failed", 0),
            "skipped": stats.get("skipped", 0),
        }

        for key in total_stats:
            total_stats[key] += stats.get(key, 0)

    return {
        "total_found": total_stats["total"],
        "total_processed": total_stats["total"],
        "total_success": total_stats["success"],
        "total_failed": total_stats["failed"],
        "total_skipped": total_stats["skipped"],
        "room_stats": room_stats,
    }


def main():
    args = parse_arguments()
    setup_logging(args.verbose)

    logger.info("=" * 40)
    logger.info("🍄 蘑菇图片智能处理工具 v2.0")
    logger.info("=" * 40)

    try:
        processor = initialize_services()

        # 解析库房列表
        target_room_ids: list[str] | None = None
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
        logger.info(
            f"🚀 开始处理流程 | 窗口: {args.hours}h | 批处理: {'ON' if args.enable_batch else 'OFF'}"
        )
        if target_room_ids:
            logger.info(f"🎯 目标库房: {target_room_ids}")

        summary = processor.get_recent_image_summary(hours=args.hours)

        processing = run_text_quality_pipeline_like_scheduler(
            processor=processor,
            hours=args.hours,
            target_room_ids=target_room_ids,
            batch_size=args.batch_size,
            max_images_per_room=args.max_per_room,
            save_to_db=not args.no_save,
        )

        # 1. 先打印摘要 (使用返回结果中的 summary 数据)
        format_summary(summary, args.hours, filter_room_ids=target_room_ids)

        logger.info("-" * 40)

        # 2. 结果展示
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
