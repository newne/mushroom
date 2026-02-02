from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import ImageTextQuality

router = APIRouter(
    prefix="/hourly_text_quality_inference",
    tags=["hourly_text_quality_inference"],
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


class ImageTextQualityResponse(ImageTextQualityBase):
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


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
    "/{record_id}",
    response_model=ImageTextQualityResponse,
    summary="获取单条图文质量记录",
    description="根据记录ID获取 ImageTextQuality 详情。",
    response_model_exclude_none=True,
)
def get_image_text_quality(record_id: int, db: Session = Depends(get_db)):
    record = db.query(ImageTextQuality).filter(ImageTextQuality.id == record_id).first()
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
