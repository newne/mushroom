from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import static_settings
from utils.create_table import ImageTextQuality, MushroomBatchYield


def _resolve_room_ids(room_ids: Optional[Iterable[str]]) -> List[str]:
    if room_ids:
        return list(room_ids)
    rooms_config = getattr(static_settings, "mushroom", None)
    if rooms_config and hasattr(rooms_config, "rooms"):
        return list(rooms_config.rooms.keys())
    return []


def _get_iot_batch_ranges(
    stat_date: Optional[date],
    room_ids: Optional[Iterable[str]] = None,
    in_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    if not stat_date:
        return []

    try:
        from environment.processor import get_room_mushroom_info
    except Exception as exc:
        logger.warning(f"[BATCH_YIELD] IoT batch source unavailable: {exc}")
        return []

    results: List[Dict[str, Any]] = []
    for room_id in _resolve_room_ids(room_ids):
        info = get_room_mushroom_info(room_id, stat_date)
        batch_date = info.get("in_date")
        if batch_date is None:
            continue
        if in_date and batch_date != in_date:
            continue
        results.append(
            {
                "room_id": room_id,
                "in_date": batch_date,
                "min_date": batch_date,
                "max_date": batch_date,
            }
        )
    return results


def _get_text_quality_batch_ranges(
    db: Session,
    room_ids: Optional[Iterable[str]] = None,
    in_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    query = db.query(ImageTextQuality.room_id, ImageTextQuality.in_date).filter(
        ImageTextQuality.room_id.isnot(None),
        ImageTextQuality.in_date.isnot(None),
    )

    if room_ids:
        query = query.filter(ImageTextQuality.room_id.in_(list(room_ids)))
    if in_date:
        query = query.filter(ImageTextQuality.in_date == in_date)

    rows = (
        query.distinct()
        .order_by(ImageTextQuality.room_id, ImageTextQuality.in_date)
        .all()
    )
    results = []
    for room_id, batch_date in rows:
        results.append(
            {
                "room_id": room_id,
                "in_date": batch_date,
                "min_date": batch_date,
                "max_date": batch_date,
            }
        )
    return results


def get_active_rooms(db: Session, stat_date: Optional[date] = None) -> List[str]:
    rooms = set()
    text_quality_rooms = (
        db.query(ImageTextQuality.room_id)
        .filter(ImageTextQuality.room_id.isnot(None))
        .distinct()
        .order_by(ImageTextQuality.room_id)
        .all()
    )
    rooms.update(room_id for (room_id,) in text_quality_rooms)

    if stat_date:
        for item in _get_iot_batch_ranges(stat_date=stat_date):
            rooms.add(item["room_id"])

    return sorted(rooms)


def get_batch_ranges(
    db: Session,
    room_ids: Optional[Iterable[str]] = None,
    in_date: Optional[date] = None,
    stat_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen = set()

    for item in _get_iot_batch_ranges(
        stat_date=stat_date, room_ids=room_ids, in_date=in_date
    ):
        key = (item["room_id"], item["in_date"])
        if key in seen:
            continue
        results.append(item)
        seen.add(key)

    for item in _get_text_quality_batch_ranges(db, room_ids=room_ids, in_date=in_date):
        key = (item["room_id"], item["in_date"])
        if key in seen:
            continue
        results.append(item)
        seen.add(key)

    return results


def build_template_records(
    batch_ranges: List[Dict[str, Any]],
    stat_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for batch in batch_ranges:
        record_stat_date = batch["in_date"]
        if stat_date and stat_date != record_stat_date:
            logger.debug(
                "[BATCH_YIELD] Override stat_date=%s to in_date=%s for room=%s",
                stat_date,
                record_stat_date,
                batch["room_id"],
            )
        records.append(
            {
                "room_id": batch["room_id"],
                "in_date": batch["in_date"],
                "stat_date": record_stat_date,
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
    batch_ranges = get_batch_ranges(
        db, room_ids=room_ids, in_date=in_date, stat_date=stat_date
    )
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


def resolve_setpoint_batch_info(
    room_id: str,
    change_time: datetime,
    db: Optional[Session] = None,
    use_iot: bool = True,
) -> Dict[str, Any]:
    """
    解析设定点变更对应的批次信息（IoT 优先，ImageTextQuality 兜底）
    """
    result = {"in_date": None, "growth_day": None, "in_num": None, "batch_id": None}
    if not room_id or not change_time:
        return result

    change_date = change_time.date()
    in_date = None
    in_num = None
    growth_day = None

    if use_iot:
        try:
            from environment.processor import get_room_mushroom_info

            info = get_room_mushroom_info(room_id, change_date)
            if info:
                in_date = info.get("in_date")
                in_num = info.get("in_num")
                growth_day = info.get("in_day_num")
        except Exception as exc:
            logger.debug(f"[SETPOINT_BATCH] IoT batch lookup failed: {exc}")

    if isinstance(in_date, datetime):
        in_date = in_date.date()

    if in_date is None:
        own_session = False
        if db is None:
            from global_const.global_const import pgsql_engine

            SessionLocal = sessionmaker(bind=pgsql_engine)
            db = SessionLocal()
            own_session = True

        try:
            query = db.query(
                ImageTextQuality.in_date,
                ImageTextQuality.collection_datetime,
            ).filter(
                ImageTextQuality.room_id == room_id,
                ImageTextQuality.in_date.isnot(None),
            )
            query = query.filter(
                (ImageTextQuality.collection_datetime.is_(None))
                | (ImageTextQuality.collection_datetime <= change_time)
            )
            query = query.order_by(
                ImageTextQuality.collection_datetime.desc(),
                ImageTextQuality.in_date.desc(),
            )
            row = query.first()
            if row and row[0]:
                in_date = row[0]
        finally:
            if own_session and db is not None:
                db.close()

    if isinstance(in_date, datetime):
        in_date = in_date.date()

    if in_date:
        if growth_day is None:
            growth_day = (change_date - in_date).days + 1
        batch_id = f"{room_id}_{in_date.strftime('%Y%m%d')}"
        return {
            "in_date": in_date,
            "growth_day": growth_day,
            "in_num": in_num,
            "batch_id": batch_id,
        }

    return result
