from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from global_const.global_const import pgsql_engine, static_settings
from utils.create_table import (
    DecisionAnalysisBatchStatus,
    DecisionAnalysisDynamicResult,
    DecisionAnalysisStaticConfig,
)
from utils.data_preprocessing import query_realtime_data

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
            "old": result.old,
            "new": result.new,
            "level": result.level or "medium",
            "change": bool(result.change),
        }

    return point_map


def _build_device_remark_map() -> dict[tuple[str, str], str | None]:
    remark_map: dict[tuple[str, str], str | None] = {}
    datapoint_cfg = static_settings.get("mushroom", {}).get("datapoint", {})

    if not isinstance(datapoint_cfg, dict):
        return remark_map

    for device_type, device_cfg in datapoint_cfg.items():
        if not isinstance(device_cfg, dict):
            continue
        device_list = device_cfg.get("device_list", [])
        if not isinstance(device_list, list):
            continue
        for device in device_list:
            if not isinstance(device, dict):
                continue
            device_alias = device.get("device_alias")
            if not device_alias:
                continue
            remark_map[(device_type, device_alias)] = device.get("remark")

    return remark_map


def _build_realtime_point_map(
    static_configs: list[DecisionAnalysisStaticConfig],
) -> dict[tuple[str, str], float]:
    if not static_configs:
        return {}

    query_records = []
    for config in static_configs:
        if not config.device_name or not config.point_name:
            continue
        query_records.append(
            {
                "device_name": config.device_name,
                "point_name": config.point_name,
                "device_alias": config.device_alias,
                "point_alias": config.point_alias,
            }
        )

    if not query_records:
        return {}

    query_df = pd.DataFrame(query_records).drop_duplicates(
        subset=["device_name", "point_name"]
    )

    real_time_df = query_realtime_data(query_df)
    if real_time_df is None or real_time_df.empty:
        return {}

    if "v" not in real_time_df.columns:
        return {}

    realtime_map: dict[tuple[str, str], float] = {}
    for _, row in real_time_df.iterrows():
        device_alias = row.get("device_alias")
        point_alias = row.get("point_alias")
        value = row.get("v")
        if device_alias and point_alias and value is not None:
            realtime_map[(device_alias, point_alias)] = float(value)

    return realtime_map


def _build_monitoring_config(
    room_id: str,
    static_configs: list[DecisionAnalysisStaticConfig],
    dynamic_results: list[DecisionAnalysisDynamicResult],
    instruction_time: datetime | None,
    batch_status: int | None,
) -> dict:
    dynamic_point_map = _build_dynamic_point_map(dynamic_results)
    device_remark_map = _build_device_remark_map()
    realtime_point_map = _build_realtime_point_map(static_configs)

    devices: dict[str, list[dict]] = {}
    device_index: dict[tuple[str, str], dict] = {}

    for config in static_configs:
        device_key = (config.device_type, config.device_alias)
        if device_key not in device_index:
            device_entry = {
                "device_name": config.device_name,
                "device_alias": config.device_alias,
                "remark": device_remark_map.get(device_key),
                "point_list": [],
            }
            devices.setdefault(config.device_type, []).append(device_entry)
            device_index[device_key] = device_entry
        else:
            device_entry = device_index[device_key]

        point_key = (config.device_alias, config.point_alias)
        dynamic_point = dynamic_point_map.get(point_key, {})
        realtime_value = realtime_point_map.get(point_key)
        old_value = dynamic_point.get("old", realtime_value)
        new_value = dynamic_point.get("new", realtime_value)
        change_flag = dynamic_point.get("change", False)

        point_entry = {
            "point_alias": config.point_alias,
            "point_name": config.point_name,
            "remark": config.remark,
            "change_type": config.change_type,
            "threshold": config.threshold,
            "enum_mapping": config.enum_mapping,
            "change": change_flag,
            "old": old_value,
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
        "status": batch_status if batch_status is not None else 0,
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
    description=(
        "基于时间区间与room_id查询动态结果并对齐静态配置。"
        "status 枚举含义：0=pending(未处理), 1=采纳建议, 2=手动调整, 3=忽略建议。"
    ),
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
        batch_status_record = (
            db.query(DecisionAnalysisBatchStatus)
            .filter(DecisionAnalysisBatchStatus.batch_id == latest_dynamic.batch_id)
            .first()
        )
        batch_status = batch_status_record.status if batch_status_record else 0

        config = _build_monitoring_config(
            room_id,
            static_configs,
            dynamic_results,
            instruction_time,
            batch_status,
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
        latest_dynamic = (
            db.query(DecisionAnalysisDynamicResult)
            .filter(DecisionAnalysisDynamicResult.room_id == room_id)
            .order_by(desc(DecisionAnalysisDynamicResult.time))
            .first()
        )
        if latest_dynamic:
            dynamic_results = (
                db.query(DecisionAnalysisDynamicResult)
                .filter(DecisionAnalysisDynamicResult.room_id == room_id)
                .filter(
                    DecisionAnalysisDynamicResult.batch_id == latest_dynamic.batch_id
                )
                .order_by(desc(DecisionAnalysisDynamicResult.time))
                .all()
            )
        else:
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
                return []

            config = _build_monitoring_config(
                room_id,
                static_configs,
                [],
                None,
                0,
            )
            config["metadata"]["batch_id"] = None
            config["confidence"] = None
            config["processed"] = False
            return [config]

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
        batch_status_record = (
            db.query(DecisionAnalysisBatchStatus)
            .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
            .first()
        )
        batch_status = batch_status_record.status if batch_status_record else 0

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
            batch_status,
        )
        config["metadata"]["batch_id"] = batch_id
        response.append(config)

    return response


@router.get(
    "/{room_id}",
    summary="获取监控点配置",
    description=(
        "基于 DecisionAnalysisStaticConfig 与 DecisionAnalysisDynamicResult 生成监控点配置。"
        "status 枚举含义：0=pending(未处理), 1=采纳建议, 2=手动调整, 3=忽略建议。"
    ),
)
def get_monitoring_points(
    room_id: str,
    db: Session = Depends(get_db),
):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    dynamic_results = (
        db.query(DecisionAnalysisDynamicResult)
        .filter(DecisionAnalysisDynamicResult.room_id == room_id)
        .filter(DecisionAnalysisDynamicResult.time >= today_start)
        .filter(DecisionAnalysisDynamicResult.time < tomorrow_start)
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
        batch_status_record = (
            db.query(DecisionAnalysisBatchStatus)
            .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
            .first()
        )
        batch_status = batch_status_record.status if batch_status_record else 0

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
            batch_status,
        )
        config["metadata"]["batch_id"] = batch_id

        confidence_values = [r.confidence for r in results if r.confidence is not None]
        config["confidence"] = max(confidence_values) if confidence_values else None
        config["processed"] = bool(batch_status) and batch_status != 0
        response.append(config)

    return response
