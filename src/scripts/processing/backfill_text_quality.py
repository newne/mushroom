#!/usr/bin/env python3
"""
批量回填文本描述与图像质量评分
- 支持指定库房与日期范围
- 支持批量大小控制
- 支持仅验证落库结果
"""

import sys
import argparse
from datetime import datetime, date
from typing import List, Optional
from pathlib import Path

from loguru import logger

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from global_const.global_const import ensure_src_path
ensure_src_path()

try:
    from sqlalchemy import func
    from sqlalchemy.orm import sessionmaker

    from global_const.const_config import MUSHROOM_ROOM_IDS
    from global_const.global_const import pgsql_engine
    from utils.create_table import (
        ImageTextQuality,
        create_tables,
    )
    from vision.mushroom_image_encoder import create_mushroom_encoder
except ImportError as e:
    sys.stderr.write(f"❌ 关键模块导入失败: {e}\n检查 PYTHONPATH 或运行环境。\n")
    sys.exit(1)


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效日期格式: {value}，应为 YYYY-MM-DD")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量回填文本描述与图像质量评分",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--start-date", type=parse_date, required=True, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=parse_date, default=date.today(), help="结束日期 (YYYY-MM-DD)")

    room_group = parser.add_mutually_exclusive_group()
    room_group.add_argument("--room-id", type=str, help="指定单个库房号")
    room_group.add_argument("--room-ids", nargs="+", help="指定多个库房号 (空格分隔)")

    parser.add_argument("--batch-size", type=int, default=10, help="每批处理数量")
    parser.add_argument("--verify-only", action="store_true", help="仅验证落库结果，不执行回填")
    parser.add_argument("--skip-create-tables", action="store_true", help="跳过建表/检查")
    parser.add_argument("--reprocess", action="store_true", help="强制重新处理并新增记录")

    return parser.parse_args()


def resolve_room_ids(args: argparse.Namespace) -> List[str]:
    if args.room_id:
        return [args.room_id]
    if args.room_ids:
        return args.room_ids
    return list(MUSHROOM_ROOM_IDS)


def run_backfill(start_date: date, end_date: date, room_ids: List[str], batch_size: int, reprocess: bool) -> None:
    encoder = create_mushroom_encoder()
    start_time = datetime.combine(start_date, datetime.min.time())
    end_time = datetime.combine(end_date, datetime.max.time())

    logger.info(f"🚀 开始回填: {start_date} ~ {end_date}, rooms={room_ids}, batch={batch_size}")

    for room_id in room_ids:
        stats = encoder.batch_process_text_quality(
            mushroom_id=room_id,
            start_time=start_time,
            end_time=end_time,
            batch_size=batch_size,
            reprocess=reprocess,
        )
        logger.info(
            f"✅ 房间 {room_id} 完成: total={stats['total']} success={stats['success']} "
            f"failed={stats['failed']} skipped={stats['skipped']}"
        )


def verify_results(start_date: date, end_date: date, room_ids: List[str]) -> None:
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()
    try:
        logger.info("🔍 验证落库结果...")

        text_quality_query = session.query(func.count(ImageTextQuality.id))
        text_quality_null_desc = session.query(func.count(ImageTextQuality.id)).filter(
            ImageTextQuality.llama_description.is_(None)
        )
        text_quality_null_score = session.query(func.count(ImageTextQuality.id)).filter(
            ImageTextQuality.image_quality_score.is_(None)
        )

        if room_ids:
            text_quality_query = text_quality_query.filter(ImageTextQuality.room_id.in_(room_ids))
            text_quality_null_desc = text_quality_null_desc.filter(ImageTextQuality.room_id.in_(room_ids))
            text_quality_null_score = text_quality_null_score.filter(ImageTextQuality.room_id.in_(room_ids))

        text_quality_query = text_quality_query.filter(ImageTextQuality.in_date.between(start_date, end_date))
        text_quality_null_desc = text_quality_null_desc.filter(ImageTextQuality.in_date.between(start_date, end_date))
        text_quality_null_score = text_quality_null_score.filter(ImageTextQuality.in_date.between(start_date, end_date))

        total = text_quality_query.scalar() or 0
        null_desc = text_quality_null_desc.scalar() or 0
        null_score = text_quality_null_score.scalar() or 0

        logger.info(f"📄 文本/质量表总数: {total}")
        logger.info(f"⚠️ 文本缺失描述: {null_desc}")
        logger.info(f"⚠️ 质量缺失评分: {null_score}")

    finally:
        session.close()


def main() -> None:
    args = parse_arguments()
    room_ids = resolve_room_ids(args)

    if not args.skip_create_tables:
        create_tables()

    try:
        if not args.verify_only:
            run_backfill(args.start_date, args.end_date, room_ids, args.batch_size, args.reprocess)
    except KeyboardInterrupt:
        logger.warning("⚠️ 回填被中断，将继续进行落库验证")
    except Exception as exc:
        logger.error(f"❌ 回填过程异常: {exc}")

    verify_results(args.start_date, args.end_date, room_ids)


if __name__ == "__main__":
    main()
