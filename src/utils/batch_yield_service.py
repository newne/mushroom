from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from utils.create_table import MushroomBatchYield


def get_active_rooms(db: Session) -> List[str]:
    rooms = (
        db.query(MushroomBatchYield.room_id)
        .filter(MushroomBatchYield.room_id.isnot(None))
        .distinct()
        .order_by(MushroomBatchYield.room_id)
        .all()
    )
    return [room_id for (room_id,) in rooms]


def get_batch_ranges(
    db: Session,
    room_ids: Optional[Iterable[str]] = None,
    in_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    query = db.query(
        MushroomBatchYield.room_id,
        MushroomBatchYield.in_date,
        func.min(MushroomBatchYield.stat_date).label("min_date"),
        func.max(MushroomBatchYield.stat_date).label("max_date"),
    ).filter(MushroomBatchYield.room_id.isnot(None))

    if room_ids:
        query = query.filter(MushroomBatchYield.room_id.in_(list(room_ids)))
    if in_date:
        query = query.filter(MushroomBatchYield.in_date == in_date)

    query = query.group_by(MushroomBatchYield.room_id, MushroomBatchYield.in_date)

    rows = query.all()
    results = []
    for room_id, batch_date, min_date, max_date in rows:
        min_date = min_date or batch_date
        max_date = max_date or batch_date
        results.append(
            {
                "room_id": room_id,
                "in_date": batch_date,
                "min_date": min_date,
                "max_date": max_date,
            }
        )
    return results


def build_stat_dates(
    min_date: date,
    max_date: date,
    stat_date: Optional[date] = None,
) -> List[date]:
    if stat_date:
        return [stat_date]

    days = (max_date - min_date).days
    return [min_date + timedelta(days=i) for i in range(days + 1)]


def build_template_records(
    batch_ranges: List[Dict[str, Any]],
    stat_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for batch in batch_ranges:
        min_date = batch["min_date"]
        max_date = batch["max_date"]
        for stat in build_stat_dates(min_date, max_date, stat_date=stat_date):
            records.append(
                {
                    "room_id": batch["room_id"],
                    "in_date": batch["in_date"],
                    "stat_date": stat,
                    "fresh_weight": None,
                    "dried_weight": None,
                    "human_evaluation": None,
                    "version": 1,
                }
            )
    return records


def init_batch_yield_records(
    db: Session,
    room_ids: Optional[Iterable[str]] = None,
    in_date: Optional[date] = None,
    stat_date: Optional[date] = None,
) -> int:
    batch_ranges = get_batch_ranges(db, room_ids=room_ids, in_date=in_date)
    if not batch_ranges:
        return 0

    template_records = build_template_records(batch_ranges, stat_date=stat_date)
    if not template_records:
        return 0

    insert_stmt = insert(MushroomBatchYield).values(template_records)
    insert_stmt = insert_stmt.on_conflict_do_nothing(
        index_elements=["room_id", "in_date", "stat_date"]
    )
    result = db.execute(insert_stmt)
    logger.info("[BATCH_YIELD] Initialized %s records", result.rowcount or 0)
    return result.rowcount or 0


def find_batch_date_range(
    batch_ranges: List[Dict[str, Any]], room_id: str, in_date: date
) -> Optional[Dict[str, date]]:
    for item in batch_ranges:
        if item["room_id"] == room_id and item["in_date"] == in_date:
            return {"min_date": item["min_date"], "max_date": item["max_date"]}
    return None
