import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy.orm import sessionmaker


def store_decision_analysis_results(
    json_data: dict,
    room_id: str,
    analysis_time: datetime,
    batch_id: str = None,
) -> dict:
    from utils.create_table import (
        DecisionAnalysisBatchStatus,
        extract_dynamic_results_from_json,
        extract_skill_feedback_from_json,
        extract_static_config_from_json,
        pgsql_engine,
        store_decision_analysis_dynamic_results,
        store_decision_analysis_skill_audit,
        store_decision_analysis_static_configs,
    )

    try:
        if not batch_id:
            batch_id = f"{room_id}_{analysis_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"[IoT Storage] Storing IoT analysis results for room {room_id}, batch {batch_id}"
        )

        static_configs = extract_static_config_from_json(json_data)
        static_count = (
            store_decision_analysis_static_configs(static_configs)
            if static_configs
            else 0
        )

        dynamic_results = extract_dynamic_results_from_json(
            json_data, batch_id, analysis_time
        )
        dynamic_count = (
            store_decision_analysis_dynamic_results(dynamic_results)
            if dynamic_results
            else 0
        )

        skill_feedback = extract_skill_feedback_from_json(json_data)
        skill_audit_count = store_decision_analysis_skill_audit(
            skill_feedback=skill_feedback,
            batch_id=batch_id,
            room_id=room_id,
            analysis_time=analysis_time,
        )

        if batch_id:
            Session = sessionmaker(bind=pgsql_engine)
            session = Session()
            try:
                existing = (
                    session.query(DecisionAnalysisBatchStatus)
                    .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
                    .first()
                )
                if not existing:
                    session.add(
                        DecisionAnalysisBatchStatus(
                            batch_id=batch_id,
                            room_id=room_id,
                            status=0,
                            operator="system",
                            comment="auto-created on batch storage",
                        )
                    )
                    session.commit()
            finally:
                session.close()

        result_stats = {
            "batch_id": batch_id,
            "room_id": room_id,
            "analysis_time": analysis_time.isoformat(),
            "static_configs_stored": static_count,
            "dynamic_results_stored": dynamic_count,
            "dynamic_results_count": dynamic_count,
            "change_count": len([r for r in dynamic_results if r.get("change", False)])
            if dynamic_results
            else 0,
            "total_points_processed": len(static_configs) if static_configs else 0,
            "processing_time": 0.0,
            "skill_audit_count": skill_audit_count,
        }

        logger.info("[IoT Storage] IoT analysis results stored successfully:")
        logger.info(f"  - Batch ID: {batch_id}")
        logger.info(f"  - Static configs: {static_count}")
        logger.info(f"  - Dynamic results: {dynamic_count}")
        logger.info(f"  - Skill audit: {skill_audit_count}")

        return result_stats

    except Exception as exc:
        logger.error(f"[IoT Storage] Failed to store IoT analysis results: {exc}")
        raise


def query_decision_analysis_dynamic_results(
    room_id: str = None,
    batch_id: str = None,
    device_alias: str = None,
    point_alias: str = None,
    change_only: bool = False,
    start_time: datetime = None,
    end_time: datetime = None,
    limit: int = 1000,
) -> list:
    from utils.create_table import DecisionAnalysisDynamicResult, pgsql_engine

    try:
        logger.info("[Dynamic Results Query] Querying dynamic point results")

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            query = session.query(DecisionAnalysisDynamicResult)

            if room_id:
                query = query.filter(DecisionAnalysisDynamicResult.room_id == room_id)
            if batch_id:
                query = query.filter(DecisionAnalysisDynamicResult.batch_id == batch_id)
            if device_alias:
                query = query.filter(
                    DecisionAnalysisDynamicResult.device_alias == device_alias
                )
            if point_alias:
                query = query.filter(
                    DecisionAnalysisDynamicResult.point_alias == point_alias
                )
            if change_only:
                query = query.filter(DecisionAnalysisDynamicResult.change == True)
            if start_time:
                query = query.filter(DecisionAnalysisDynamicResult.time >= start_time)
            if end_time:
                query = query.filter(DecisionAnalysisDynamicResult.time <= end_time)

            query = query.order_by(DecisionAnalysisDynamicResult.time.desc())

            if limit:
                query = query.limit(limit)

            results = query.all()
            logger.info(
                f"[Dynamic Results Query] Found {len(results)} dynamic point results"
            )

            return results

        finally:
            session.close()

    except Exception as exc:
        logger.error(
            f"[Dynamic Results Query] Failed to query dynamic point results: {exc}"
        )
        raise
