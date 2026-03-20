#!/usr/bin/env python3
"""
CLIP推理调度器
蘑菇图像处理系统的CLIP推理功能模块
支持处理最近图片、批量处理所有图片、系统验证等功能
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path

ensure_src_path()

from utils import log_task_event
from utils.minio_client import create_minio_client
from vision.mushroom_image_encoder import create_mushroom_encoder
from vision.recent_image_processor import create_recent_image_processor


def _scheduler_log(event: str, message: str, level: str = "INFO", **context):
    """输出 CLIP 调度器统一事件日志。"""
    log_task_event(
        "VISION_CLIP_SCHEDULER",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


def _summary_level(failed_items: int) -> str:
    """根据失败数量返回汇总日志级别。"""
    return "INFO" if failed_items == 0 else "WARNING"


def _build_run_result(
    ok: bool,
    status: str,
    level: str,
    summary: str,
):
    """构建统一的命令执行结果。"""
    return {
        "ok": ok,
        "status": status,
        "level": level,
        "summary": summary,
    }


def process_recent_images(args):
    """处理最近时间段的图片"""
    room_scope = "all"
    if getattr(args, "room_id", None):
        room_scope = str(args.room_id)
    elif getattr(args, "room_ids", None):
        room_scope = ",".join(args.room_ids)

    _scheduler_log(
        "VISION_CLIP_SCHEDULER_RECENT_START",
        "recent 开始",
        hours=args.hours,
        room_scope=room_scope,
        max_per_room=getattr(args, "max_per_room", None),
        save_to_db=not args.no_save,
        room_id=getattr(args, "room_id", None),
        room_ids=",".join(args.room_ids) if getattr(args, "room_ids", None) else None,
        status="running",
    )

    try:
        # 创建共享实例
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_RECENT_INIT",
            "recent 初始化共享组件",
            level="DEBUG",
            status="running",
        )
        shared_encoder = create_mushroom_encoder()
        shared_minio_client = create_minio_client()

        processor = create_recent_image_processor(
            shared_encoder=shared_encoder, shared_minio_client=shared_minio_client
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
            show_summary=True,
        )

        # 显示结果
        processing = result["processing"]
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_RECENT_FINISH",
            (
                "recent 完成"
                f" | found={processing['total_found']}"
                f" processed={processing['total_processed']}"
                f" success={processing['total_success']}"
                f" failed={processing['total_failed']}"
                f" skipped={processing['total_skipped']}"
            ),
            level=_summary_level(processing["total_failed"]),
            total_found=processing["total_found"],
            total_items=processing["total_processed"],
            successful_items=processing["total_success"],
            failed_items=processing["total_failed"],
            skipped_items=processing["total_skipped"],
            status="success" if processing["total_failed"] == 0 else "partial",
        )

        if processing["room_stats"]:
            for room_id, stats in sorted(processing["room_stats"].items()):
                _scheduler_log(
                    "VISION_CLIP_SCHEDULER_RECENT_ROOM_DETAIL",
                    "recent 图片处理库房明细",
                    level="DEBUG",
                    room_id=room_id,
                    found_items=stats["found"],
                    total_items=stats["processed"],
                    successful_items=stats["success"],
                    failed_items=stats["failed"],
                    skipped_items=stats["skipped"],
                    status="success" if stats["failed"] == 0 else "partial",
                )

        run_status = "success" if processing["total_failed"] == 0 else "partial"
        run_level = _summary_level(processing["total_failed"])
        return _build_run_result(
            ok=True,
            status=run_status,
            level=run_level,
            summary=(
                f"recent({run_status}) found={processing['total_found']}"
                f" processed={processing['total_processed']}"
                f" success={processing['total_success']}"
                f" failed={processing['total_failed']}"
                f" skipped={processing['total_skipped']}"
            ),
        )

    except Exception as e:
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_RECENT_FAILED",
            "recent 图片处理失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
        )
        return _build_run_result(
            ok=False,
            status="failed",
            level="ERROR",
            summary=f"recent(failed) error={type(e).__name__}",
        )


def process_all_images(args):
    """批量处理所有图片数据"""
    _scheduler_log(
        "VISION_CLIP_SCHEDULER_BATCH_START",
        "batch-all 开始",
        room_id=getattr(args, "room_id", None),
        date_filter=getattr(args, "date_filter", None),
        batch_size=args.batch_size,
        save_to_db=not args.no_save,
        status="running",
    )

    try:
        # 创建编码器
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_BATCH_INIT",
            "batch-all 初始化编码器",
            level="DEBUG",
            status="running",
        )
        encoder = create_mushroom_encoder()

        # 确定要处理的库房
        mushroom_id = args.room_id if args.room_id else None
        date_filter = args.date_filter if hasattr(args, "date_filter") else None

        # 执行批量处理
        stats = encoder.batch_process_images(
            mushroom_id=mushroom_id, date_filter=date_filter, batch_size=args.batch_size
        )

        # 显示结果
        success_rate = (
            (stats["success"] / stats["total"]) * 100 if stats["total"] > 0 else 0.0
        )
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_BATCH_FINISH",
            (
                "batch-all 完成"
                f" | total={stats['total']}"
                f" success={stats['success']}"
                f" failed={stats['failed']}"
                f" skipped={stats['skipped']}"
                f" success_rate={round(success_rate, 2)}%"
            ),
            level=_summary_level(stats["failed"]),
            total_items=stats["total"],
            successful_items=stats["success"],
            failed_items=stats["failed"],
            skipped_items=stats["skipped"],
            success_rate=round(success_rate, 2),
            status="success" if stats["failed"] == 0 else "partial",
        )

        # 获取处理统计
        processing_stats = encoder.get_processing_statistics()

        if processing_stats:
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_BATCH_STATS",
                "batch-all 统计",
                level="DEBUG",
                total_items=processing_stats.get("total_processed", 0),
                with_environmental_control=processing_stats.get(
                    "with_environmental_control", 0
                ),
                status="success",
            )

            room_dist = processing_stats.get("room_distribution", {})
            if room_dist:
                for room_id, count in sorted(room_dist.items()):
                    _scheduler_log(
                        "VISION_CLIP_SCHEDULER_BATCH_ROOM_DISTRIBUTION",
                        "batch-all 库房分布统计",
                        level="DEBUG",
                        room_id=room_id,
                        total_items=count,
                        status="success",
                    )

        run_status = "success" if stats["failed"] == 0 else "partial"
        return _build_run_result(
            ok=True,
            status=run_status,
            level=_summary_level(stats["failed"]),
            summary=(
                f"batch-all({run_status}) total={stats['total']}"
                f" success={stats['success']}"
                f" failed={stats['failed']}"
                f" skipped={stats['skipped']}"
                f" success_rate={round(success_rate, 2)}%"
            ),
        )

    except Exception as e:
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_BATCH_FAILED",
            "batch-all 处理失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
        )
        return _build_run_result(
            ok=False,
            status="failed",
            level="ERROR",
            summary=f"batch-all(failed) error={type(e).__name__}",
        )


def validate_system(args):
    """系统验证"""
    _scheduler_log(
        "VISION_CLIP_SCHEDULER_VALIDATE_START",
        "validate 开始",
        max_per_room=getattr(args, "max_per_room", 3),
        status="running",
    )

    try:
        # 创建编码器
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_VALIDATE_INIT",
            "validate 初始化编码器",
            level="DEBUG",
            status="running",
        )
        encoder = create_mushroom_encoder()

        max_per_mushroom = getattr(args, "max_per_room", 3)

        # 执行系统验证
        validation_results = encoder.validate_system_with_limited_samples(
            max_per_mushroom=max_per_mushroom
        )

        # 显示结果
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_VALIDATE_FINISH",
            (
                "validate 完成"
                f" | rooms={validation_results['total_mushrooms']}"
                f" processed={validation_results['total_processed']}"
                f" success={validation_results['total_success']}"
                f" failed={validation_results['total_failed']}"
                f" skipped={validation_results['total_skipped']}"
                f" no_env={validation_results['total_no_env_data']}"
            ),
            level=_summary_level(validation_results["total_failed"]),
            total_mushrooms=validation_results["total_mushrooms"],
            mushroom_ids=",".join(
                str(item) for item in validation_results["mushroom_ids"]
            ),
            total_items=validation_results["total_processed"],
            successful_items=validation_results["total_success"],
            failed_items=validation_results["total_failed"],
            skipped_items=validation_results["total_skipped"],
            no_env_data=validation_results["total_no_env_data"],
            status="success" if validation_results["total_failed"] == 0 else "partial",
        )
        for mushroom_id, stats in validation_results["processed_per_mushroom"].items():
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_VALIDATE_ROOM_DETAIL",
                "validate 库房明细",
                level="DEBUG",
                room_id=mushroom_id,
                total_items=stats["processed"],
                successful_items=stats["success"],
                failed_items=stats["failed"],
                no_env_data=stats["no_env_data"],
                status="success" if stats["failed"] == 0 else "partial",
            )

        run_status = "success" if validation_results["total_failed"] == 0 else "partial"
        return _build_run_result(
            ok=True,
            status=run_status,
            level=_summary_level(validation_results["total_failed"]),
            summary=(
                f"validate({run_status}) rooms={validation_results['total_mushrooms']}"
                f" processed={validation_results['total_processed']}"
                f" success={validation_results['total_success']}"
                f" failed={validation_results['total_failed']}"
                f" skipped={validation_results['total_skipped']}"
                f" no_env={validation_results['total_no_env_data']}"
            ),
        )

    except Exception as e:
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_VALIDATE_FAILED",
            "validate 执行失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
        )
        return _build_run_result(
            ok=False,
            status="failed",
            level="ERROR",
            summary=f"validate(failed) error={type(e).__name__}",
        )


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="CLIP推理调度器 - 蘑菇图像处理系统",
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
        """,
    )

    # 添加子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 最近图片处理命令
    recent_parser = subparsers.add_parser("recent", help="处理最近时间段的图片")
    recent_parser.add_argument(
        "--hours", type=int, default=1, help="查询最近多少小时的图片 (默认: 1)"
    )
    recent_parser.add_argument("--room-id", type=str, help="指定库房号")
    recent_parser.add_argument(
        "--room-ids", nargs="+", help="指定多个库房号，用空格分隔"
    )
    recent_parser.add_argument(
        "--max-per-room", type=int, help="每个库房最多处理多少张图片"
    )
    recent_parser.add_argument(
        "--no-save", action="store_true", help="不保存到数据库，仅测试处理"
    )

    # 批量处理所有图片命令
    batch_parser = subparsers.add_parser("batch-all", help="批量处理所有图片数据")
    batch_parser.add_argument(
        "--room-id", type=str, help="指定库房号，如果不指定则处理所有库房"
    )
    batch_parser.add_argument("--date-filter", type=str, help="日期过滤 (YYYYMMDD格式)")
    batch_parser.add_argument(
        "--batch-size", type=int, default=10, help="批处理大小 (默认: 10)"
    )
    batch_parser.add_argument(
        "--no-save", action="store_true", help="不保存到数据库，仅测试处理"
    )

    # 系统验证命令
    validate_parser = subparsers.add_parser("validate", help="系统功能验证")
    validate_parser.add_argument(
        "--max-per-room",
        type=int,
        default=3,
        help="每个库房最多处理多少张图片 (默认: 3)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 抑制第三方模型加载进度条，避免调度日志被刷屏。
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    run_started_at = datetime.now()

    _scheduler_log(
        "VISION_CLIP_SCHEDULER_MAIN_START",
        "调度器启动",
        command=args.command,
        started_at=run_started_at.isoformat(),
        status="running",
    )

    try:
        run_result = _build_run_result(
            ok=False,
            status="failed",
            level="ERROR",
            summary="command not executed",
        )

        if args.command == "recent":
            run_result = process_recent_images(args)
        elif args.command == "batch-all":
            run_result = process_all_images(args)
        elif args.command == "validate":
            run_result = validate_system(args)
        else:
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_UNKNOWN_COMMAND",
                "收到未知调度命令",
                level="ERROR",
                command=args.command,
                status="failed",
            )
            parser.print_help()
            return

        _scheduler_log(
            "VISION_CLIP_SCHEDULER_MAIN_FINISH",
            f"调度器结束 | {run_result['summary']}",
            level=run_result["level"],
            command=args.command,
            finished_at=datetime.now().isoformat(),
            duration_seconds=round(
                (datetime.now() - run_started_at).total_seconds(), 3
            ),
            status=run_result["status"],
        )

        if run_result["ok"] and run_result["status"] == "success":
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_MAIN_SUCCESS",
                "命令成功",
                command=args.command,
                status="success",
            )
        elif run_result["ok"] and run_result["status"] == "partial":
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_MAIN_PARTIAL",
                "命令完成（部分失败）",
                level="WARNING",
                command=args.command,
                status="partial",
                summary=run_result["summary"],
            )
        else:
            _scheduler_log(
                "VISION_CLIP_SCHEDULER_MAIN_FAILED",
                "命令失败",
                level="ERROR",
                command=args.command,
                status="failed",
            )
            sys.exit(1)

    except KeyboardInterrupt:
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_INTERRUPTED",
            "用户中断调度器执行",
            level="WARNING",
            status="failed",
        )
        sys.exit(1)
    except Exception as e:
        _scheduler_log(
            "VISION_CLIP_SCHEDULER_MAIN_EXCEPTION",
            "调度器主流程执行异常",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
