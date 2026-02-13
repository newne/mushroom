#!/usr/bin/env python3
"""Backfill device_setpoint_changes batch fields and validate data quality."""

import argparse
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

src_dir = Path(__file__).resolve().parents[2]
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import ensure_src_path, pgsql_engine
from utils.batch_yield_service import resolve_setpoint_batch_info
from utils.loguru_setting import logger

ensure_src_path()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _build_filters(
    room_ids: Iterable[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, dict]:
    clauses = ["change_time IS NOT NULL"]
    params: dict = {}
    if room_ids:
        clauses.append("room_id = ANY(:room_ids)")
        params["room_ids"] = list(room_ids)
    if start_date:
        clauses.append("change_time >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("change_time <= :end_date")
        params["end_date"] = end_date
    return " AND ".join(clauses), params


def validate_setpoint_batch_fields(
    room_ids: Iterable[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> dict:
    where_sql, params = _build_filters(room_ids, start_date, end_date)

    sql = f"""
    SELECT
        COUNT(*) AS total,
        COUNT(CASE WHEN in_date IS NULL THEN 1 END) AS missing_in_date,
        COUNT(CASE WHEN growth_day IS NULL THEN 1 END) AS missing_growth_day,
        COUNT(CASE WHEN batch_id IS NULL THEN 1 END) AS missing_batch_id,
        COUNT(CASE WHEN in_num IS NULL THEN 1 END) AS missing_in_num
    FROM device_setpoint_changes
    WHERE {where_sql}
    """

    with pgsql_engine.connect() as conn:
        row = conn.execute(text(sql), params).fetchone()

    report = {
        "total": int(row.total or 0),
        "missing_in_date": int(row.missing_in_date or 0),
        "missing_growth_day": int(row.missing_growth_day or 0),
        "missing_batch_id": int(row.missing_batch_id or 0),
        "missing_in_num": int(row.missing_in_num or 0),
    }
    logger.info("[SETPOINT_BACKFILL] Validation report: {}", report)
    return report


def backfill_setpoint_batch_fields(
    room_ids: Iterable[str] | None,
    start_date: date | None,
    end_date: date | None,
    dry_run: bool,
    limit: int | None,
    use_iot: bool,
) -> dict:
    where_sql, params = _build_filters(room_ids, start_date, end_date)
    limit_sql = ""
    if limit and limit > 0:
        limit_sql = " LIMIT :limit"
        params["limit"] = limit

    report = {
        "dry_run": dry_run,
        "checked": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }

    Session = sessionmaker(bind=pgsql_engine)
    iot_cache: dict[tuple[str, date], dict] = {}
    with Session() as session:
        rows = session.execute(
            text(
                f"""
                SELECT id, room_id, change_time, in_date, growth_day, in_num, batch_id
                FROM device_setpoint_changes
                WHERE {where_sql}
                ORDER BY change_time DESC
                {limit_sql}
                """
            ),
            params,
        ).fetchall()

        for row in rows:
            report["checked"] += 1
            record_id = row[0]
            room_id = row[1]
            change_time = row[2]
            if not room_id or not change_time:
                report["errors"] += 1
                continue

            info = None
            cache_key = (room_id, change_time.date())
            if use_iot:
                info = iot_cache.get(cache_key)

            if info is None:
                info = resolve_setpoint_batch_info(
                    room_id, change_time, db=session, use_iot=use_iot
                )
                if use_iot:
                    iot_cache[cache_key] = info
            if not info.get("in_date"):
                report["skipped"] += 1
                continue

            updated_fields: dict = {}
            if row[3] != info.get("in_date"):
                updated_fields["in_date"] = info.get("in_date")
            if row[4] != info.get("growth_day"):
                updated_fields["growth_day"] = info.get("growth_day")
            if row[5] != info.get("in_num"):
                updated_fields["in_num"] = info.get("in_num")
            if row[6] != info.get("batch_id"):
                updated_fields["batch_id"] = info.get("batch_id")

            if not updated_fields:
                report["skipped"] += 1
                continue

            if dry_run:
                report["updated"] += 1
                continue

            session.execute(
                text(
                    """
                    UPDATE device_setpoint_changes
                    SET in_date = :in_date,
                        growth_day = :growth_day,
                        in_num = :in_num,
                        batch_id = :batch_id
                    WHERE id = :record_id
                    """
                ),
                {
                    "record_id": record_id,
                    "in_date": updated_fields.get("in_date", row[3]),
                    "growth_day": updated_fields.get("growth_day", row[4]),
                    "in_num": updated_fields.get("in_num", row[5]),
                    "batch_id": updated_fields.get("batch_id", row[6]),
                },
            )
            report["updated"] += 1

        if not dry_run:
            session.commit()

    logger.info("[SETPOINT_BACKFILL] Backfill report: {}", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill device_setpoint_changes batch fields and validate data."
    )
    parser.add_argument(
        "--room-id",
        action="append",
        dest="room_ids",
        help="Filter by room id (repeatable).",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Filter by change_time start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="Filter by change_time end date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit records processed.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates (default is dry run).",
    )
    parser.add_argument(
        "--no-iot",
        action="store_true",
        help="Skip IoT lookup and use ImageTextQuality only.",
    )
    args = parser.parse_args()

    logger.info("[SETPOINT_BACKFILL] Pre-validation start")
    validate_setpoint_batch_fields(
        room_ids=args.room_ids,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
    )

    backfill_setpoint_batch_fields(
        room_ids=args.room_ids,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
        dry_run=not args.apply,
        limit=args.limit,
        use_iot=not args.no_iot,
    )

    logger.info("[SETPOINT_BACKFILL] Post-validation start")
    validate_setpoint_batch_fields(
        room_ids=args.room_ids,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
    )


if __name__ == "__main__":
    main()
