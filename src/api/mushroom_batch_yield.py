from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import ImageTextQuality, MushroomBatchYield
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
    stat_date: date = Field(..., description="统计日期 (YYYY-MM-DD)")
    harvest_time: datetime | None = Field(None, description="采收时间")
    fresh_weight: float | None = Field(None, ge=0, description="鲜菇重量 (斤)")
    dried_weight: float | None = Field(None, ge=0, description="干菇重量 (斤)")
    human_evaluation: str | None = Field(None, description="人工评价/备注")


class MushroomBatchYieldCreate(MushroomBatchYieldBase):
    pass


class MushroomBatchYieldUpdate(BaseModel):
    room_id: str | None = None
    in_date: date | None = None
    stat_date: date | None = None
    harvest_time: datetime | None = None
    fresh_weight: float | None = Field(None, ge=0)
    dried_weight: float | None = Field(None, ge=0)
    human_evaluation: str | None = None


class MushroomBatchYieldBatchUpdate(BaseModel):
    harvest_time: datetime | None = None
    fresh_weight: float | None = Field(None, ge=0)
    dried_weight: float | None = Field(None, ge=0)
    human_evaluation: str | None = None


class MushroomBatchYieldResponse(MushroomBatchYieldBase):
    id: int
    create_time: datetime | None = None
    update_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("harvest_time", "create_time", "update_time")
    def serialize_datetime(self, value: datetime | None):
        return format_datetime(value)


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
    existing = (
        db.query(MushroomBatchYield)
        .filter(MushroomBatchYield.room_id == payload.room_id)
        .filter(MushroomBatchYield.in_date == payload.in_date)
        .filter(MushroomBatchYield.stat_date == payload.stat_date)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Batch yield already exists")

    record = MushroomBatchYield(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


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
):
    if (
        start_harvest_time
        and end_harvest_time
        and start_harvest_time > end_harvest_time
    ):
        raise HTTPException(
            status_code=400,
            detail="start_harvest_time cannot be later than end_harvest_time",
        )

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
    return query.offset(offset).limit(limit).all()


@router.get(
    "/rooms",
    response_model=list[str],
    summary="查询库房列表",
    description="从图文质量表中查询并返回已有记录的库房编号列表。",
)
def list_rooms(db: Session = Depends(get_db)):
    rooms = (
        db.query(ImageTextQuality.room_id)
        .filter(ImageTextQuality.room_id.isnot(None))
        .distinct()
        .order_by(ImageTextQuality.room_id)
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
        db.query(ImageTextQuality.in_date)
        .filter(ImageTextQuality.room_id == room_id)
        .filter(ImageTextQuality.in_date.isnot(None))
        .distinct()
        .order_by(ImageTextQuality.in_date.desc())
        .all()
    )
    return [in_date for (in_date,) in batches]


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
        raise HTTPException(status_code=404, detail="Record not found")
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
        raise HTTPException(status_code=404, detail="Record not found")

    update_data = payload.model_dump(exclude_unset=True)
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
        raise HTTPException(status_code=404, detail="Record not found")
    if len(records) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple records found for batch, please specify stat_date",
        )

    record = records[0]
    update_data = payload.model_dump(exclude_unset=True)
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
        raise HTTPException(status_code=404, detail="Record not found")

    db.delete(record)
    db.commit()
    return {"deleted": True, "id": record_id}
