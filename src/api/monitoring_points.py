from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import (
    DecisionAnalysisDynamicResult,
    DecisionAnalysisStaticConfig,
)

router = APIRouter(
    prefix="/decision_analysis",
    tags=["decision_analysis"],
    responses={404: {"description": "Record not found"}},
)

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _build_dynamic_point_map(
    dynamic_results: list[DecisionAnalysisDynamicResult],
) -> dict[tuple[str, str], dict]:
    point_map: dict[tuple[str, str], dict] = {}

    for result in dynamic_results:
        point_map[(result.device_alias, result.point_alias)] = {
            "new": result.new,
            "level": result.level or "medium",
            "change": result.new is not None,
        }

    return point_map


def _build_monitoring_config(
    room_id: str,
    static_configs: list[DecisionAnalysisStaticConfig],
    dynamic_results: list[DecisionAnalysisDynamicResult],
    instruction_time: datetime | None,
) -> dict:
    dynamic_point_map = _build_dynamic_point_map(dynamic_results)

    devices: dict[str, list[dict]] = {}
    device_index: dict[tuple[str, str], dict] = {}

    for config in static_configs:
        device_key = (config.device_type, config.device_alias)
        if device_key not in device_index:
            device_entry = {
                "device_name": config.device_name,
                "device_alias": config.device_alias,
                "point_list": [],
            }
            devices.setdefault(config.device_type, []).append(device_entry)
            device_index[device_key] = device_entry
        else:
            device_entry = device_index[device_key]

        point_key = (config.device_alias, config.point_alias)
        dynamic_point = dynamic_point_map.get(point_key, {})
        static_old = getattr(config, "old", None)
        new_value = dynamic_point.get("new", static_old)

        point_entry = {
            "point_alias": config.point_alias,
            "point_name": config.point_name,
            "remark": config.remark,
            "change_type": config.change_type,
            "threshold": config.threshold,
            "enum_mapping": config.enum_mapping,
            "change": dynamic_point.get("change", False),
            "old": static_old,
            "new": new_value,
            "level": dynamic_point.get("level", "medium"),
        }
        device_entry["point_list"].append(point_entry)

    total_points = sum(
        len(device.get("point_list", []))
        for device_list in devices.values()
        for device in device_list
        if isinstance(device_list, list)
    )

    return {
        "room_id": room_id,
        "time": instruction_time.isoformat() if instruction_time else None,
        "devices": devices,
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "enhanced_decision_analysis",
            "total_points": total_points,
        },
    }


@router.get(
    "/query",
    summary="按时间区间查询监控点配置",
    description="基于时间区间与room_id查询动态结果并对齐静态配置",
)
def list_monitoring_points_by_time_range(
    room_id: str = Query(..., description="库房编号"),
    end_time: datetime = Query(..., description="结束时间 (ISO 8601)"),
    start_time: datetime | None = Query(None, description="起始时间 (ISO 8601)"),
    db: Session = Depends(get_db),
):
    if start_time and start_time > end_time:
        raise HTTPException(
            status_code=400, detail="start_time cannot be greater than end_time"
        )

    if start_time is None:
        latest_dynamic = (
            db.query(DecisionAnalysisDynamicResult)
            .filter(DecisionAnalysisDynamicResult.room_id == room_id)
            .filter(DecisionAnalysisDynamicResult.time <= end_time)
            .order_by(desc(DecisionAnalysisDynamicResult.time))
            .first()
        )

        if not latest_dynamic:
            return []

        instruction_time = latest_dynamic.time
        static_query = (
            db.query(DecisionAnalysisStaticConfig)
            .filter(DecisionAnalysisStaticConfig.room_id == room_id)
            .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
            .filter(DecisionAnalysisStaticConfig.effective_time <= instruction_time)
        )

        static_configs = static_query.order_by(
            DecisionAnalysisStaticConfig.device_type,
            DecisionAnalysisStaticConfig.device_alias,
            DecisionAnalysisStaticConfig.point_alias,
        ).all()

        if not static_configs:
            static_configs = (
                db.query(DecisionAnalysisStaticConfig)
                .filter(DecisionAnalysisStaticConfig.room_id == room_id)
                .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
                .order_by(
                    DecisionAnalysisStaticConfig.device_type,
                    DecisionAnalysisStaticConfig.device_alias,
                    DecisionAnalysisStaticConfig.point_alias,
                )
                .all()
            )

        if not static_configs:
            raise HTTPException(status_code=404, detail="Static config not found")

        dynamic_results = (
            db.query(DecisionAnalysisDynamicResult)
            .filter(DecisionAnalysisDynamicResult.room_id == room_id)
            .filter(DecisionAnalysisDynamicResult.batch_id == latest_dynamic.batch_id)
            .all()
        )

        config = _build_monitoring_config(
            room_id,
            static_configs,
            dynamic_results,
            instruction_time,
        )
        config["metadata"]["batch_id"] = latest_dynamic.batch_id
        return config

    dynamic_results = (
        db.query(DecisionAnalysisDynamicResult)
        .filter(DecisionAnalysisDynamicResult.room_id == room_id)
        .filter(DecisionAnalysisDynamicResult.time >= start_time)
        .filter(DecisionAnalysisDynamicResult.time <= end_time)
        .order_by(desc(DecisionAnalysisDynamicResult.time))
        .all()
    )

    if not dynamic_results:
        return []

    batch_groups: dict[str, list[DecisionAnalysisDynamicResult]] = defaultdict(list)
    batch_time: dict[str, datetime] = {}
    for result in dynamic_results:
        batch_groups[result.batch_id].append(result)
        if (
            result.batch_id not in batch_time
            or result.time > batch_time[result.batch_id]
        ):
            batch_time[result.batch_id] = result.time

    response: list[dict] = []
    for batch_id, results in batch_groups.items():
        instruction_time = batch_time.get(batch_id)

        static_query = (
            db.query(DecisionAnalysisStaticConfig)
            .filter(DecisionAnalysisStaticConfig.room_id == room_id)
            .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
        )
        if instruction_time:
            static_query = static_query.filter(
                DecisionAnalysisStaticConfig.effective_time <= instruction_time
            )

        static_configs = static_query.order_by(
            DecisionAnalysisStaticConfig.device_type,
            DecisionAnalysisStaticConfig.device_alias,
            DecisionAnalysisStaticConfig.point_alias,
        ).all()

        if not static_configs and instruction_time:
            static_configs = (
                db.query(DecisionAnalysisStaticConfig)
                .filter(DecisionAnalysisStaticConfig.room_id == room_id)
                .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
                .order_by(
                    DecisionAnalysisStaticConfig.device_type,
                    DecisionAnalysisStaticConfig.device_alias,
                    DecisionAnalysisStaticConfig.point_alias,
                )
                .all()
            )

        if not static_configs:
            continue

        config = _build_monitoring_config(
            room_id,
            static_configs,
            results,
            instruction_time,
        )
        config["metadata"]["batch_id"] = batch_id
        response.append(config)

    return response


@router.get(
    "/{room_id}",
    summary="获取监控点配置",
    description="基于 DecisionAnalysisStaticConfig 与 DecisionAnalysisDynamicResult 生成监控点配置",
)
def get_monitoring_points(
    room_id: str,
    db: Session = Depends(get_db),
):
    latest_dynamic = (
        db.query(DecisionAnalysisDynamicResult)
        .filter(DecisionAnalysisDynamicResult.room_id == room_id)
        .order_by(desc(DecisionAnalysisDynamicResult.time))
        .first()
    )

    instruction_time = latest_dynamic.time if latest_dynamic else None

    static_query = (
        db.query(DecisionAnalysisStaticConfig)
        .filter(DecisionAnalysisStaticConfig.room_id == room_id)
        .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
    )

    if instruction_time:
        static_query = static_query.filter(
            DecisionAnalysisStaticConfig.effective_time <= instruction_time
        )

    static_configs = static_query.order_by(
        DecisionAnalysisStaticConfig.device_type,
        DecisionAnalysisStaticConfig.device_alias,
        DecisionAnalysisStaticConfig.point_alias,
    ).all()

    if not static_configs and instruction_time:
        static_configs = (
            db.query(DecisionAnalysisStaticConfig)
            .filter(DecisionAnalysisStaticConfig.room_id == room_id)
            .filter(DecisionAnalysisStaticConfig.is_active.is_(True))
            .order_by(
                DecisionAnalysisStaticConfig.device_type,
                DecisionAnalysisStaticConfig.device_alias,
                DecisionAnalysisStaticConfig.point_alias,
            )
            .all()
        )

    if not static_configs:
        raise HTTPException(status_code=404, detail="Static config not found")

    dynamic_results: list[DecisionAnalysisDynamicResult] = []
    if latest_dynamic:
        dynamic_results = (
            db.query(DecisionAnalysisDynamicResult)
            .filter(DecisionAnalysisDynamicResult.room_id == room_id)
            .filter(DecisionAnalysisDynamicResult.batch_id == latest_dynamic.batch_id)
            .all()
        )

    return _build_monitoring_config(
        room_id,
        static_configs,
        dynamic_results,
        instruction_time,
    )
