import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import conn, pgsql_engine
from utils.batch_yield_service import (
    find_batch_date_range,
    get_active_rooms,
    get_batch_ranges,
    init_batch_yield_records,
)
from utils.create_table import MushroomBatchYield, MushroomBatchYieldAudit
from utils.loguru_setting import logger
from utils.time_utils import format_datetime

router = APIRouter(
    prefix="/mushroom_batch_yield",
    tags=["mushroom_batch_yield"],
    responses={404: {"description": "Record not found"}},
)

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class MushroomBatchYieldBase(BaseModel):
    room_id: str = Field(..., description="库房编号")
    in_date: date = Field(..., description="进库日期 (YYYY-MM-DD)")
    stat_date: date = Field(
        default_factory=date.today, description="统计日期 (YYYY-MM-DD)"
    )
    harvest_time: datetime | None = Field(None, description="采收时间")
    fresh_weight: int | None = Field(None, ge=0, description="鲜菇重量 (斤)")
    dried_weight: int | None = Field(None, ge=0, description="干菇重量 (斤)")
    human_evaluation: str | None = Field(
        None, max_length=500, description="人工评价/备注"
    )


class MushroomBatchYieldCreate(MushroomBatchYieldBase):
    pass


class MushroomBatchYieldUpdate(BaseModel):
    room_id: str | None = None
    in_date: date | None = None
    stat_date: date | None = None
    harvest_time: datetime | None = None
    fresh_weight: int | None = Field(None, ge=0)
    dried_weight: int | None = Field(None, ge=0)
    human_evaluation: str | None = Field(None, max_length=500)


class MushroomBatchYieldBatchUpdate(BaseModel):
    harvest_time: datetime | None = None
    fresh_weight: int | None = Field(None, ge=0)
    dried_weight: int | None = Field(None, ge=0)
    human_evaluation: str | None = Field(None, max_length=500)


class MushroomBatchYieldPutItem(BaseModel):
    id: int
    version: int = Field(..., ge=1)
    harvest_time: datetime | None = None
    fresh_weight: int | None = Field(None, ge=0)
    dried_weight: int | None = Field(None, ge=0)
    human_evaluation: str | None = Field(None, max_length=500)
    operator: str | None = None
    request_id: str | None = None


class MushroomBatchYieldPutRequest(BaseModel):
    items: list[MushroomBatchYieldPutItem]


class MushroomBatchYieldResponse(MushroomBatchYieldBase):
    id: int
    version: int
    create_time: datetime | None = None
    update_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("harvest_time", "create_time", "update_time")
    def serialize_datetime(self, value: datetime | None):
        return format_datetime(value)


def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": details or {}},
    )


def _serialize_records(records: list[MushroomBatchYield]) -> list[dict[str, Any]]:
    return [
        MushroomBatchYieldResponse.model_validate(record).model_dump()
        for record in records
    ]


def _cache_key(params: dict[str, Any]) -> str:
    return "mushroom:batch_yield:" + json.dumps(params, sort_keys=True, default=str)


def _is_cacheable(
    in_date: date | None,
    stat_date: date | None,
    start_harvest_time: datetime | None,
    end_harvest_time: datetime | None,
) -> bool:
    cutoff = date.today() - timedelta(days=7)
    if stat_date and stat_date >= cutoff:
        return True
    if in_date and in_date >= cutoff:
        return True
    if start_harvest_time and start_harvest_time.date() >= cutoff:
        return True
    if end_harvest_time and end_harvest_time.date() >= cutoff:
        return True
    return False


def _get_cache(params: dict[str, Any]) -> list[dict[str, Any]] | None:
    if conn is None:
        return None
    try:
        cached = conn.get(_cache_key(params))
        if not cached:
            return None
        return json.loads(cached)
    except Exception as exc:
        logger.warning(f"[BATCH_YIELD] cache read failed: {exc}")
        return None


def _set_cache(params: dict[str, Any], payload: list[dict[str, Any]]) -> None:
    if conn is None:
        return
    try:
        conn.setex(_cache_key(params), 86400, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"[BATCH_YIELD] cache write failed: {exc}")


@router.post(
    "",
    response_model=MushroomBatchYieldResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建批次产量记录",
    description="创建一条蘑菇批次产量记录。",
    response_model_exclude_none=True,
)
def create_batch_yield(
    payload: MushroomBatchYieldCreate, db: Session = Depends(get_db)
):
    active_rooms = get_active_rooms(db)
    if payload.room_id not in active_rooms:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "room_id not active",
            {"room_id": payload.room_id},
        )

    batch_ranges = get_batch_ranges(
        db, room_ids=[payload.room_id], in_date=payload.in_date
    )
    if not batch_ranges:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "batch not found",
            {"room_id": payload.room_id, "in_date": payload.in_date},
        )
    range_info = find_batch_date_range(batch_ranges, payload.room_id, payload.in_date)
    if range_info and not (
        range_info["min_date"] <= payload.stat_date <= range_info["max_date"]
    ):
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "BUSINESS_ERROR",
            "stat_date out of range",
            {
                "stat_date": payload.stat_date,
                "min_date": range_info["min_date"],
                "max_date": range_info["max_date"],
            },
        )

    existing = (
        db.query(MushroomBatchYield)
        .filter(MushroomBatchYield.room_id == payload.room_id)
        .filter(MushroomBatchYield.in_date == payload.in_date)
        .filter(MushroomBatchYield.stat_date == payload.stat_date)
        .first()
    )
    if existing:
        raise_api_error(
            status.HTTP_409_CONFLICT,
            "BUSINESS_ERROR",
            "Batch yield already exists",
            {"room_id": payload.room_id, "in_date": payload.in_date},
        )

    payload_data = payload.model_dump()
    if payload_data.get("fresh_weight") is not None:
        payload_data["fresh_weight"] = float(payload_data["fresh_weight"])
    if payload_data.get("dried_weight") is not None:
        payload_data["dried_weight"] = float(payload_data["dried_weight"])
    record = MushroomBatchYield(**payload_data)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.post(
    "/init",
    response_model=list[MushroomBatchYieldResponse],
    status_code=status.HTTP_201_CREATED,
    summary="初始化批次产量模板",
    description="初始化批次产量空数据模板（用于前端编辑）。",
    response_model_exclude_none=True,
)
def init_batch_yields(
    db: Session = Depends(get_db),
    room_id: str | None = Query(None, description="库房编号"),
    in_date: date | None = Query(None, description="进库日期 (YYYY-MM-DD)"),
    stat_date: date | None = Query(None, description="统计日期 (YYYY-MM-DD)"),
):
    try:
        if room_id:
            active_rooms = get_active_rooms(db)
            if room_id not in active_rooms:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "BUSINESS_ERROR",
                    "room_id not active",
                    {"room_id": room_id},
                )

        init_batch_yield_records(
            db,
            room_ids=[room_id] if room_id else None,
            in_date=in_date,
            stat_date=stat_date,
        )
        db.commit()

        query = db.query(MushroomBatchYield)
        if room_id:
            query = query.filter(MushroomBatchYield.room_id == room_id)
        if in_date:
            query = query.filter(MushroomBatchYield.in_date == in_date)
        if stat_date:
            query = query.filter(MushroomBatchYield.stat_date == stat_date)

        return query.order_by(MushroomBatchYield.create_time.desc()).all()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[BATCH_YIELD] init failed: {exc}", exc_info=True)
        raise_api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "SYSTEM_ERROR",
            "Init failed",
        )


@router.get(
    "",
    response_model=list[MushroomBatchYieldResponse],
    summary="查询批次产量记录",
    description="按库房/入库日期/统计日期筛选，支持分页。",
    response_model_exclude_none=True,
)
def list_batch_yields(
    db: Session = Depends(get_db),
    room_id: str | None = Query(None, description="库房编号"),
    in_date: date | None = Query(None, description="进库日期 (YYYY-MM-DD)"),
    stat_date: date | None = Query(None, description="统计日期 (YYYY-MM-DD)"),
    start_harvest_time: datetime | None = Query(None, description="采收开始时间"),
    end_harvest_time: datetime | None = Query(None, description="采收结束时间"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    auto_init: bool = Query(True, description="空数据自动初始化"),
):
    if (
        start_harvest_time
        and end_harvest_time
        and start_harvest_time > end_harvest_time
    ):
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "BUSINESS_ERROR",
            "start_harvest_time cannot be later than end_harvest_time",
        )

    cache_params = {
        "room_id": room_id,
        "in_date": in_date,
        "stat_date": stat_date,
        "start_harvest_time": start_harvest_time,
        "end_harvest_time": end_harvest_time,
        "limit": limit,
        "offset": offset,
    }
    if _is_cacheable(in_date, stat_date, start_harvest_time, end_harvest_time):
        cached = _get_cache(cache_params)
        if cached is not None:
            return cached

    query = db.query(MushroomBatchYield)

    if room_id:
        query = query.filter(MushroomBatchYield.room_id == room_id)
    if in_date:
        query = query.filter(MushroomBatchYield.in_date == in_date)
    if stat_date:
        query = query.filter(MushroomBatchYield.stat_date == stat_date)
    if start_harvest_time:
        query = query.filter(MushroomBatchYield.harvest_time >= start_harvest_time)
    if end_harvest_time:
        query = query.filter(MushroomBatchYield.harvest_time <= end_harvest_time)

    query = query.order_by(MushroomBatchYield.create_time.desc())
    results = query.offset(offset).limit(limit).all()

    if not results and auto_init:
        init_batch_yield_records(
            db,
            room_ids=[room_id] if room_id else None,
            in_date=in_date,
            stat_date=stat_date,
        )
        db.commit()

        query = db.query(MushroomBatchYield)
        if room_id:
            query = query.filter(MushroomBatchYield.room_id == room_id)
        if in_date:
            query = query.filter(MushroomBatchYield.in_date == in_date)
        if stat_date:
            query = query.filter(MushroomBatchYield.stat_date == stat_date)

        results = query.order_by(MushroomBatchYield.create_time.desc()).all()

    payload = _serialize_records(results)
    if _is_cacheable(in_date, stat_date, start_harvest_time, end_harvest_time):
        _set_cache(cache_params, payload)
    return payload


@router.get(
    "/rooms",
    response_model=list[str],
    summary="查询库房列表",
    description="从批次产量表中查询并返回已有记录的库房编号列表。",
)
def list_rooms(db: Session = Depends(get_db)):
    rooms = (
        db.query(MushroomBatchYield.room_id)
        .filter(MushroomBatchYield.room_id.isnot(None))
        .distinct()
        .order_by(MushroomBatchYield.room_id)
        .all()
    )
    return [room_id for (room_id,) in rooms]


@router.get(
    "/rooms/{room_id}/batches",
    response_model=list[date],
    summary="按库房查询批次信息",
    description="根据库房编号查询批次列表（返回 in_date 作为批次信息）。",
)
def list_room_batches(room_id: str, db: Session = Depends(get_db)):
    batches = (
        db.query(MushroomBatchYield.in_date)
        .filter(MushroomBatchYield.room_id == room_id)
        .filter(MushroomBatchYield.in_date.isnot(None))
        .distinct()
        .order_by(MushroomBatchYield.in_date.desc())
        .all()
    )
    return [in_date for (in_date,) in batches]


@router.put(
    "",
    response_model=list[MushroomBatchYieldResponse],
    summary="批量更新批次产量",
    description="批量更新产量与人工评价，支持乐观锁与事务。",
    response_model_exclude_none=True,
)
def update_batch_yields(
    payload: MushroomBatchYieldPutRequest,
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "BUSINESS_ERROR",
            "items cannot be empty",
        )

    ids = [item.id for item in payload.items]
    records = db.query(MushroomBatchYield).filter(MushroomBatchYield.id.in_(ids)).all()
    record_map = {record.id: record for record in records}

    if len(record_map) != len(ids):
        missing = [item_id for item_id in ids if item_id not in record_map]
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "Record not found",
            {"missing_ids": missing},
        )

    active_rooms = set(get_active_rooms(db))
    batch_ranges = get_batch_ranges(db)

    update_mappings = []
    audit_rows = []

    try:
        with db.begin():
            for item in payload.items:
                record = record_map[item.id]
                current_version = record.version or 1

                if record.room_id not in active_rooms:
                    raise_api_error(
                        status.HTTP_400_BAD_REQUEST,
                        "BUSINESS_ERROR",
                        "room_id not active",
                        {"room_id": record.room_id},
                    )

                range_info = find_batch_date_range(
                    batch_ranges, record.room_id, record.in_date
                )
                if range_info:
                    if not (
                        range_info["min_date"]
                        <= record.stat_date
                        <= range_info["max_date"]
                    ):
                        raise_api_error(
                            status.HTTP_400_BAD_REQUEST,
                            "BUSINESS_ERROR",
                            "stat_date out of range",
                            {
                                "stat_date": record.stat_date,
                                "min_date": range_info["min_date"],
                                "max_date": range_info["max_date"],
                            },
                        )

                if current_version != item.version:
                    raise_api_error(
                        status.HTTP_409_CONFLICT,
                        "BUSINESS_ERROR",
                        "Version conflict",
                        {"id": record.id, "expected": current_version},
                    )

                update_data = {
                    "id": record.id,
                    "version": current_version + 1,
                }
                if item.harvest_time is not None:
                    update_data["harvest_time"] = item.harvest_time
                if item.fresh_weight is not None:
                    update_data["fresh_weight"] = float(item.fresh_weight)
                if item.dried_weight is not None:
                    update_data["dried_weight"] = float(item.dried_weight)
                if item.human_evaluation is not None:
                    update_data["human_evaluation"] = item.human_evaluation

                before_snapshot = {
                    "id": record.id,
                    "room_id": record.room_id,
                    "in_date": record.in_date.isoformat(),
                    "stat_date": record.stat_date.isoformat(),
                    "harvest_time": format_datetime(record.harvest_time),
                    "fresh_weight": record.fresh_weight,
                    "dried_weight": record.dried_weight,
                    "human_evaluation": record.human_evaluation,
                    "version": current_version,
                }
                after_snapshot = before_snapshot.copy()
                after_snapshot.update(
                    {
                        "harvest_time": format_datetime(
                            update_data.get("harvest_time", record.harvest_time)
                        ),
                        "fresh_weight": update_data.get(
                            "fresh_weight", record.fresh_weight
                        ),
                        "dried_weight": update_data.get(
                            "dried_weight", record.dried_weight
                        ),
                        "human_evaluation": update_data.get(
                            "human_evaluation", record.human_evaluation
                        ),
                        "version": update_data["version"],
                    }
                )

                update_mappings.append(update_data)
                audit_rows.append(
                    MushroomBatchYieldAudit(
                        record_id=record.id,
                        room_id=record.room_id,
                        in_date=record.in_date,
                        stat_date=record.stat_date,
                        before_snapshot=before_snapshot,
                        after_snapshot=after_snapshot,
                        operator=item.operator,
                        request_id=item.request_id,
                    )
                )

            db.bulk_update_mappings(MushroomBatchYield, update_mappings)
            if audit_rows:
                db.add_all(audit_rows)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[BATCH_YIELD] batch update failed: {exc}", exc_info=True)
        raise_api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "SYSTEM_ERROR",
            "Batch update failed",
        )

    updated_records = (
        db.query(MushroomBatchYield)
        .filter(MushroomBatchYield.id.in_(ids))
        .order_by(MushroomBatchYield.update_time.desc())
        .all()
    )
    return updated_records


@router.get(
    "/{record_id}",
    response_model=MushroomBatchYieldResponse,
    summary="获取批次产量记录",
    description="按记录ID获取批次产量记录。",
)
def get_batch_yield(record_id: int, db: Session = Depends(get_db)):
    record = (
        db.query(MushroomBatchYield).filter(MushroomBatchYield.id == record_id).first()
    )
    if not record:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "Record not found",
            {"id": record_id},
        )
    return record


@router.put(
    "/{record_id}",
    response_model=MushroomBatchYieldResponse,
    summary="更新批次产量记录",
    description="根据记录ID更新批次产量记录（支持部分字段更新）。",
    response_model_exclude_none=True,
)
def update_batch_yield(
    record_id: int,
    payload: MushroomBatchYieldUpdate,
    db: Session = Depends(get_db),
):
    record = (
        db.query(MushroomBatchYield).filter(MushroomBatchYield.id == record_id).first()
    )
    if not record:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "Record not found",
            {"id": record_id},
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "fresh_weight" in update_data and update_data["fresh_weight"] is not None:
        update_data["fresh_weight"] = float(update_data["fresh_weight"])
    if "dried_weight" in update_data and update_data["dried_weight"] is not None:
        update_data["dried_weight"] = float(update_data["dried_weight"])
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


@router.put(
    "/rooms/{room_id}/batches/{in_date}",
    response_model=MushroomBatchYieldResponse,
    summary="按库房与批次更新产量记录",
    description="根据库房编号与批次（in_date）更新产量记录部分字段。",
    response_model_exclude_none=True,
)
def update_batch_yield_by_batch(
    room_id: str,
    in_date: date,
    payload: MushroomBatchYieldBatchUpdate,
    db: Session = Depends(get_db),
    stat_date: date | None = Query(None, description="统计日期 (YYYY-MM-DD)"),
):
    query = db.query(MushroomBatchYield).filter(
        MushroomBatchYield.room_id == room_id,
        MushroomBatchYield.in_date == in_date,
    )
    if stat_date:
        query = query.filter(MushroomBatchYield.stat_date == stat_date)

    records = query.all()
    if not records:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "Record not found",
            {"room_id": room_id, "in_date": in_date},
        )
    if len(records) > 1:
        raise_api_error(
            status.HTTP_409_CONFLICT,
            "BUSINESS_ERROR",
            "Multiple records found for batch, please specify stat_date",
            {"room_id": room_id, "in_date": in_date},
        )

    record = records[0]
    update_data = payload.model_dump(exclude_unset=True)
    if "fresh_weight" in update_data and update_data["fresh_weight"] is not None:
        update_data["fresh_weight"] = float(update_data["fresh_weight"])
    if "dried_weight" in update_data and update_data["dried_weight"] is not None:
        update_data["dried_weight"] = float(update_data["dried_weight"])
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_200_OK,
    summary="删除批次产量记录",
    description="根据记录ID删除批次产量记录。",
)
def delete_batch_yield(record_id: int, db: Session = Depends(get_db)):
    record = (
        db.query(MushroomBatchYield).filter(MushroomBatchYield.id == record_id).first()
    )
    if not record:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            "BUSINESS_ERROR",
            "Record not found",
            {"id": record_id},
        )

    db.delete(record)
    db.commit()
    return {"deleted": True, "id": record_id}
