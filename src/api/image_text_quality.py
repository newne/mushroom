from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import ImageTextQuality
from utils.time_utils import DATETIME_FORMAT, format_datetime, parse_datetime

router = APIRouter(
    prefix="/image_text_quality",
    tags=["image_text_quality"],
    responses={404: {"description": "Record not found"}},
)

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ImageTextQualityBase(BaseModel):
    mushroom_embedding_id: int | None = Field(
        None, description="关联mushroom_embedding.id"
    )
    image_path: str = Field(..., description="图片存储路径")
    room_id: str | None = Field(None, description="库房编号")
    in_date: date | None = Field(None, description="进库日期 (YYYY-MM-DD)")
    collection_datetime: datetime | None = Field(None, description="采集时间")
    llama_description: str | None = Field(
        None, description="LLaMA生成的蘑菇生长情况描述"
    )
    image_quality_score: float | None = Field(None, description="图像质量评分 (0-100)")
    human_evaluation: str | None = Field(None, description="人工评估结果或备注")
    chinese_description: str | None = Field(None, description="中文描述文本")


class ImageTextQualityCreate(ImageTextQualityBase):
    image_path: str = Field(..., min_length=1, description="图片存储路径")


class ImageTextQualityUpdate(BaseModel):
    mushroom_embedding_id: int | None = None
    image_path: str | None = None
    room_id: str | None = None
    in_date: date | None = None
    collection_datetime: datetime | None = None
    llama_description: str | None = None
    image_quality_score: float | None = None
    human_evaluation: str | None = None
    chinese_description: str | None = None


class HumanEvaluationUpdate(BaseModel):
    human_evaluation: str = Field(..., min_length=1, description="人工评估结果或备注")


class HumanEvaluationByKeyUpdate(BaseModel):
    record_id: int | None = Field(None, description="记录ID")
    image_path: str | None = Field(None, description="图片存储路径")
    human_evaluation: str = Field(..., min_length=1, description="人工评估结果或备注")

    @model_validator(mode="after")
    def validate_key(self):
        if bool(self.record_id) == bool(self.image_path):
            raise ValueError("record_id 或 image_path 必须且只能提供一个")
        return self


class ImageTextQualityResponse(ImageTextQualityBase):
    id: int
    is_evaluation: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def set_is_evaluation(cls, data):
        if isinstance(data, dict):
            human_eval = data.get("human_evaluation")
            data["is_evaluation"] = bool(human_eval)
            return data
        if hasattr(data, "human_evaluation"):
            setattr(data, "is_evaluation", bool(getattr(data, "human_evaluation")))
        return data

    @field_serializer("collection_datetime", "created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None):
        return format_datetime(value)


@router.post(
    "",
    response_model=ImageTextQualityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建图文质量记录",
    description="创建一条 ImageTextQuality 记录，包含图像路径、文本描述与质量评分。",
    response_model_exclude_none=True,
)
def create_image_text_quality(
    payload: ImageTextQualityCreate, db: Session = Depends(get_db)
):
    record = ImageTextQuality(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get(
    "",
    response_model=list[ImageTextQualityResponse],
    summary="查询图文质量记录",
    description="按库房/日期/评分范围筛选，支持分页。",
    response_model_exclude_none=True,
)
def list_image_text_quality(
    db: Session = Depends(get_db),
    room_id: str | None = Query(None, description="库房编号"),
    in_date: date | None = Query(None, description="进库日期 (YYYY-MM-DD)"),
    min_score: float | None = Query(None, ge=0, le=100, description="最小图像质量评分"),
    max_score: float | None = Query(None, ge=0, le=100, description="最大图像质量评分"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    if min_score is not None and max_score is not None and min_score > max_score:
        raise HTTPException(
            status_code=400, detail="min_score cannot be greater than max_score"
        )

    query = db.query(ImageTextQuality)

    if room_id:
        query = query.filter(ImageTextQuality.room_id == room_id)
    if in_date:
        query = query.filter(ImageTextQuality.in_date == in_date)
    if min_score is not None:
        query = query.filter(ImageTextQuality.image_quality_score >= min_score)
    if max_score is not None:
        query = query.filter(ImageTextQuality.image_quality_score <= max_score)

    query = query.order_by(ImageTextQuality.created_at.desc())
    return query.offset(offset).limit(limit).all()


@router.get(
    "/best_by_room_time",
    response_model=ImageTextQualityResponse,
    summary="按库房与结束时间获取当天最高评分记录",
    description="返回指定库房在当天0点至给定结束时间之前的最高评分记录。",
    response_model_exclude_none=False,
)
def get_best_by_room_and_time(
    room_id: str = Query(..., description="库房编号"),
    end_time: str = Query(..., description="结束时间 (YYYY-MM-DD HH:MM:SS)"),
    db: Session = Depends(get_db),
):
    try:
        parsed_end_time = parse_datetime(end_time)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"time format must be {DATETIME_FORMAT}"
        )

    start_time = datetime.combine(parsed_end_time.date(), datetime.min.time())

    time_filter = or_(
        and_(
            ImageTextQuality.collection_datetime.isnot(None),
            ImageTextQuality.collection_datetime >= start_time,
            ImageTextQuality.collection_datetime <= parsed_end_time,
        ),
        and_(
            ImageTextQuality.collection_datetime.is_(None),
            ImageTextQuality.created_at >= start_time,
            ImageTextQuality.created_at <= parsed_end_time,
        ),
    )

    record = (
        db.query(ImageTextQuality)
        .filter(ImageTextQuality.room_id == room_id)
        .filter(time_filter)
        .order_by(
            ImageTextQuality.image_quality_score.desc().nullslast(),
            ImageTextQuality.collection_datetime.desc().nullslast(),
            ImageTextQuality.created_at.desc(),
        )
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.get(
    "/{room_id}",
    response_model=ImageTextQualityResponse,
    summary="获取库房最高评分图文质量记录",
    description="根据库房编号返回当前图像中质量评分最高的一条记录。",
    response_model_exclude_none=False,
)
def get_image_text_quality(room_id: str, db: Session = Depends(get_db)):
    record = (
        db.query(ImageTextQuality)
        .filter(ImageTextQuality.room_id == room_id)
        .order_by(
            ImageTextQuality.image_quality_score.desc().nullslast(),
            ImageTextQuality.created_at.desc(),
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.put(
    "/{record_id}",
    response_model=ImageTextQualityResponse,
    summary="更新图文质量记录",
    description="根据记录ID更新图文质量记录（支持部分字段更新）。",
    response_model_exclude_none=True,
)
def update_image_text_quality(
    record_id: int,
    payload: ImageTextQualityUpdate,
    db: Session = Depends(get_db),
):
    record = db.query(ImageTextQuality).filter(ImageTextQuality.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


@router.patch(
    "/human_evaluation",
    response_model=ImageTextQualityResponse,
    summary="按ID或图片路径更新人工评估",
    description="根据 record_id 或 image_path 更新 human_evaluation 字段。",
    response_model_exclude_none=True,
)
def update_human_evaluation_by_key(
    payload: HumanEvaluationByKeyUpdate,
    db: Session = Depends(get_db),
):
    if payload.record_id:
        record = (
            db.query(ImageTextQuality)
            .filter(ImageTextQuality.id == payload.record_id)
            .first()
        )
    else:
        record = (
            db.query(ImageTextQuality)
            .filter(ImageTextQuality.image_path == payload.image_path)
            .order_by(ImageTextQuality.created_at.desc())
            .first()
        )

    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    record.human_evaluation = payload.human_evaluation
    db.commit()
    db.refresh(record)
    return record


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_200_OK,
    summary="删除图文质量记录",
    description="根据记录ID删除图文质量记录。",
)
def delete_image_text_quality(record_id: int, db: Session = Depends(get_db)):
    record = db.query(ImageTextQuality).filter(ImageTextQuality.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    db.delete(record)
    db.commit()
    return {"deleted": True, "id": record_id}
