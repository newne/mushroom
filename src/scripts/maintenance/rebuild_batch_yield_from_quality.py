#!/usr/bin/env python3
"""Rebuild mushroom_batch_yield from ImageTextQuality only."""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

src_dir = Path(__file__).resolve().parents[2]
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import ensure_src_path, pgsql_engine
from utils.create_table import ImageTextQuality, MushroomBatchYield
from utils.loguru_setting import logger

ensure_src_path()

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _query_batch_ranges(
    db,
    room_ids: Optional[Iterable[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    query = db.query(ImageTextQuality.room_id, ImageTextQuality.in_date).filter(
        ImageTextQuality.room_id.isnot(None),
        ImageTextQuality.in_date.isnot(None),
    )
    if room_ids:
        query = query.filter(ImageTextQuality.room_id.in_(list(room_ids)))
    if start_date:
        query = query.filter(ImageTextQuality.in_date >= start_date)
    if end_date:
        query = query.filter(ImageTextQuality.in_date <= end_date)

    rows = (
        query.distinct()
        .order_by(ImageTextQuality.room_id, ImageTextQuality.in_date)
        .all()
    )
    return [
        {
            "room_id": room_id,
            "in_date": in_date,
            "min_date": in_date,
            "max_date": in_date,
        }
        for room_id, in_date in rows
    ]


def _build_template_records(batch_ranges: list[dict]) -> list[dict]:
    records = []
    for batch in batch_ranges:
        records.append(
            {
                "room_id": batch["room_id"],
                "in_date": batch["in_date"],
                "stat_date": batch["in_date"],
                "fresh_weight": None,
                "dried_weight": None,
                "human_evaluation": None,
                "version": 1,
            }
        )
    return records


def rebuild_batch_yield(
    dry_run: bool = True,
    room_ids: Optional[Iterable[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    report = {
        "dry_run": dry_run,
        "room_ids": list(room_ids) if room_ids else None,
        "start_date": start_date,
        "end_date": end_date,
        "batch_count": 0,
        "deleted_rows": 0,
        "inserted_rows": 0,
    }

    db = SessionLocal()
    try:
        batch_ranges = _query_batch_ranges(
            db, room_ids=room_ids, start_date=start_date, end_date=end_date
        )
        report["batch_count"] = len(batch_ranges)
        if not batch_ranges:
            return report

        if dry_run:
            return report

        room_set = sorted({item["room_id"] for item in batch_ranges})
        in_dates = sorted({item["in_date"] for item in batch_ranges})
        delete_query = db.query(MushroomBatchYield).filter(
            MushroomBatchYield.room_id.in_(room_set),
            MushroomBatchYield.in_date.in_(in_dates),
        )
        report["deleted_rows"] = delete_query.delete(synchronize_session=False) or 0

        template_records = _build_template_records(batch_ranges)
        insert_stmt = insert(MushroomBatchYield).values(template_records)
        result = db.execute(insert_stmt)
        if result.rowcount is None or result.rowcount < 0:
            report["inserted_rows"] = len(template_records)
        else:
            report["inserted_rows"] = result.rowcount
        db.commit()
        return report
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild mushroom_batch_yield from ImageTextQuality (no IoT)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run).",
    )
    parser.add_argument(
        "--room-id",
        action="append",
        dest="room_ids",
        help="Room id to include (can be specified multiple times).",
    )
    parser.add_argument("--start-date", help="Start in_date (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End in_date (YYYY-MM-DD).")
    args = parser.parse_args()

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)

    report = rebuild_batch_yield(
        dry_run=not args.apply,
        room_ids=args.room_ids,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info("[BATCH_YIELD_REBUILD] Report: {}", report)


if __name__ == "__main__":
    main()
