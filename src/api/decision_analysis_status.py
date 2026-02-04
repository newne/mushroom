from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import DecisionAnalysisBatchStatus

router = APIRouter(
    prefix="/decision_analysis_status",
    tags=["decision_analysis_status"],
    responses={404: {"description": "Record not found"}},
)

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BatchStatusBase(BaseModel):
    batch_id: str = Field(..., description="批次ID")
    room_id: str = Field(..., description="库房编号")
    status: int = Field(
        0,
        description="状态：0=pending(未处理), 1=采纳建议, 2=手动调整, 3=忽略建议",
    )
    operator: str | None = Field(None, description="操作者：人/系统")
    comment: str | None = Field(None, description="备注说明")


class BatchStatusCreate(BatchStatusBase):
    pass


class BatchStatusUpdate(BaseModel):
    status: int | None = Field(
        None,
        description="状态：0=pending(未处理), 1=采纳建议, 2=手动调整, 3=忽略建议",
    )
    operator: str | None = Field(None, description="操作者：人/系统")
    comment: str | None = Field(None, description="备注说明")


class BatchStatusResponse(BatchStatusBase):
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


@router.post(
    "",
    response_model=BatchStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建批次状态",
    description="创建决策分析批次状态记录。",
)
def create_batch_status(payload: BatchStatusCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(DecisionAnalysisBatchStatus)
        .filter(DecisionAnalysisBatchStatus.batch_id == payload.batch_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Batch status already exists")

    record = DecisionAnalysisBatchStatus(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get(
    "",
    response_model=list[BatchStatusResponse],
    summary="查询批次状态",
    description="按库房或批次ID筛选批次状态记录。",
)
def list_batch_status(
    db: Session = Depends(get_db),
    room_id: str | None = Query(None, description="库房编号"),
    batch_id: str | None = Query(None, description="批次ID"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    query = db.query(DecisionAnalysisBatchStatus)
    if room_id:
        query = query.filter(DecisionAnalysisBatchStatus.room_id == room_id)
    if batch_id:
        query = query.filter(DecisionAnalysisBatchStatus.batch_id == batch_id)

    query = query.order_by(DecisionAnalysisBatchStatus.updated_at.desc())
    return query.offset(offset).limit(limit).all()


@router.get(
    "/{batch_id}",
    response_model=BatchStatusResponse,
    summary="获取批次状态",
    description="按批次ID获取单条批次状态记录。",
)
def get_batch_status(batch_id: str, db: Session = Depends(get_db)):
    record = (
        db.query(DecisionAnalysisBatchStatus)
        .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.put(
    "/{batch_id}",
    response_model=BatchStatusResponse,
    summary="更新批次状态",
    description="按批次ID更新状态、操作者或备注。",
)
def update_batch_status(
    batch_id: str,
    payload: BatchStatusUpdate,
    db: Session = Depends(get_db),
):
    record = (
        db.query(DecisionAnalysisBatchStatus)
        .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_200_OK,
    summary="删除批次状态",
    description="按批次ID删除批次状态记录。",
)
def delete_batch_status(batch_id: str, db: Session = Depends(get_db)):
    record = (
        db.query(DecisionAnalysisBatchStatus)
        .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    db.delete(record)
    db.commit()
    return {"deleted": True, "batch_id": batch_id}
