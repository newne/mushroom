#!/usr/bin/env python3
"""Fix MushroomImageEmbedding in_date based on image path parsing."""

import argparse
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

src_dir = Path(__file__).resolve().parents[2]
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import ensure_src_path, pgsql_engine
from utils.loguru_setting import logger
from vision.mushroom_image_processor import MushroomImagePathParser

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
    clauses = ["image_path IS NOT NULL"]
    params: dict = {}
    if room_ids:
        clauses.append("room_id = ANY(:room_ids)")
        params["room_ids"] = list(room_ids)
    if start_date:
        clauses.append("collection_datetime >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("collection_datetime <= :end_date")
        params["end_date"] = end_date
    return " AND ".join(clauses), params


def fix_embedding_in_date(
    room_ids: Iterable[str] | None,
    start_date: date | None,
    end_date: date | None,
    dry_run: bool,
) -> dict:
    parser = MushroomImagePathParser()
    report = {
        "dry_run": dry_run,
        "checked": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }

    where_sql, params = _build_filters(room_ids, start_date, end_date)

    with pgsql_engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, image_path, room_id, in_date
                FROM mushroom_embedding
                WHERE {where_sql}
                ORDER BY id
                """
            ),
            params,
        ).fetchall()

        for row in rows:
            report["checked"] += 1
            image_path = row[1]
            info = parser.parse_path(image_path)
            if not info:
                report["errors"] += 1
                continue

            parsed_in_date = info.collection_date_obj.date()
            if row[3] == parsed_in_date:
                report["skipped"] += 1
                continue

            if dry_run:
                report["updated"] += 1
                continue

            conn.execute(
                text(
                    """
                    UPDATE mushroom_embedding
                    SET in_date = :in_date
                    WHERE id = :record_id
                    """
                ),
                {"in_date": parsed_in_date, "record_id": row[0]},
            )
            report["updated"] += 1

        if not dry_run:
            conn.commit()

    logger.info("[EMBEDDING_FIX] Report: {}", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix MushroomImageEmbedding.in_date using filename collection_date."
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
        help="Filter by collection_datetime start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="Filter by collection_datetime end date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report mismatches; do not update records.",
    )
    args = parser.parse_args()

    fix_embedding_in_date(
        room_ids=args.room_ids,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
