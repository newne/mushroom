#!/usr/bin/env python3
"""
Enhanced Decision Analysis Script

Provides a programmatic API for running enhanced decision analysis and
optionally outputting monitoring-points formatted JSON for dynamic storage.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from loguru import logger

from global_const.const_config import (
    DECISION_ANALYSIS_ENABLE_SKILL_KB_PRIOR,
    OUTPUT_DIR_NAME,
)
from global_const.global_const import (
    BASE_DIR,
    ensure_src_path,
    pgsql_engine,
    settings,
    static_settings,
)
from utils.time_utils import format_datetime

ensure_src_path()

from decision_analysis.decision_analyzer import DecisionAnalyzer
from decision_analysis.prompt_manager import resolve_decision_prompt_template
from decision_analysis.skills.cultivation_skill import CultivationSkillEngine


@dataclass
class EnhancedDecisionAnalysisResult:
    """
    Enhanced decision analysis result

    Attributes:
        success: Execution success flag
        status: Result status
        room_id: Room number
        analysis_time: Analysis timestamp
        output_file: Output JSON path (if any)
        data: Output JSON data (if any)
        error_message: Error message on failure
        warnings: Warning list
        processing_time: Processing time (seconds)
        metadata: Additional metadata
    """

    success: bool
    status: str
    room_id: str
    analysis_time: datetime
    output_file: Optional[Path] = None
    data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    processing_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_monitoring_points_output(
    enhanced_output, room_id: str, analysis_time: datetime
) -> Dict[str, Any]:
    monitoring_config_path = BASE_DIR / "configs" / "monitoring_points_config.json"
    static_config_path = BASE_DIR / "configs" / "static_config.json"

    monitoring_config = _load_json(monitoring_config_path)
    static_config = _load_json(static_config_path)

    monitoring_devices = monitoring_config.get("devices", {})
    static_datapoint = static_config.get("mushroom", {}).get("datapoint", {})

    device_recs = enhanced_output.device_recommendations.devices
    confidence_score = None
    metadata = getattr(enhanced_output, "metadata", None)
    if metadata and getattr(metadata, "device_config_metadata", None):
        confidence_score = metadata.device_config_metadata.get("decision_confidence")
    if confidence_score is None and getattr(
        enhanced_output, "multi_image_analysis", None
    ):
        confidence_score = enhanced_output.multi_image_analysis.confidence_score

    devices_output: Dict[str, list] = {}

    def _base_alias(alias: str) -> str:
        return re.sub(r"_\d+$", "", alias)

    for device_type, template_devices in monitoring_devices.items():
        point_template = []
        if template_devices:
            point_template = template_devices[0].get("point_list", [])

        device_list = static_datapoint.get(device_type, {}).get("device_list", [])
        room_devices = [
            d for d in device_list if d.get("device_alias", "").endswith(f"_{room_id}")
        ]

        if not room_devices:
            continue

        devices_output[device_type] = []

        for device in room_devices:
            device_alias = device.get("device_alias")
            device_name = device.get("device_name")
            device_rec = device_recs.get(device_alias) or device_recs.get(device_type)
            if not device_rec and device_alias:
                alias_base = _base_alias(device_alias)
                for key, rec in device_recs.items():
                    if _base_alias(key) == alias_base:
                        device_rec = rec
                        break

            point_list = []
            for point in point_template:
                point_alias = point.get("point_alias")
                param = None
                if device_rec and point_alias:
                    param = device_rec.parameters.get(point_alias)

                old_value = float(point.get("old", 0))
                new_value = float(point.get("new", 0))
                level = "low"
                action = None

                if param:
                    old_value = float(param.current_value)
                    new_value = float(param.recommended_value)
                    level = str(param.priority or "low")
                    action = param.action
                    reason = param.change_reason
                else:
                    reason = None

                threshold = point.get("threshold")
                if action == "adjust":
                    change = True
                elif action in {"maintain", "monitor"}:
                    change = False
                elif threshold is not None:
                    change = abs(new_value - old_value) >= float(threshold)
                else:
                    change = new_value != old_value

                point_list.append(
                    {
                        "point_alias": point_alias,
                        "point_name": point.get("point_name"),
                        "remark": point.get("remark"),
                        "change_type": point.get("change_type"),
                        "threshold": threshold,
                        "enum_mapping": point.get("enum_mapping"),
                        "change": bool(change),
                        "old": old_value,
                        "new": new_value,
                        "level": level,
                        "reason": reason,
                        "confidence": confidence_score,
                    }
                )

            devices_output[device_type].append(
                {
                    "device_name": device_name,
                    "device_alias": device_alias,
                    "point_list": point_list,
                }
            )

    return {
        "monitoring_points": {
            "room_id": room_id,
            "time": format_datetime(analysis_time),
            "devices": devices_output,
        }
    }


def execute_enhanced_decision_analysis(
    room_id: str,
    analysis_datetime: Optional[datetime] = None,
    output_file: Optional[Union[str, Path]] = None,
    verbose: bool = False,
    output_format: str = "both",
    similar_case_top_k: int = 3,
    embedding_similarity_weight: float = 0.7,
    env_similarity_weight: float = 0.3,
    enable_kb_human_prior: bool = False,
    multi_image_boost: bool = True,
    enable_skill_engine: bool = True,
    persist_output_file: bool = True,
) -> EnhancedDecisionAnalysisResult:
    """
    Execute enhanced decision analysis

    Args:
        room_id: Room ID
        analysis_datetime: Analysis time (defaults to now)
        output_file: Optional output file path
        verbose: Enable debug logging
        output_format: "enhanced", "monitoring", or "both"
        similar_case_top_k: 相似案例数量
        embedding_similarity_weight: 图像向量相似度权重
        env_similarity_weight: 环境相似度权重
        enable_kb_human_prior: 是否注入知识库人工调控偏好
        multi_image_boost: 是否启用多图像相似度增强
        enable_skill_engine: 是否启用Skill能力模块约束修正
        persist_output_file: 是否写入输出JSON文件（调度任务可关闭以减少I/O）
    """

    start_time = datetime.now()
    if analysis_datetime is None:
        analysis_datetime = start_time

    result = EnhancedDecisionAnalysisResult(
        success=False,
        status="pending",
        room_id=room_id,
        analysis_time=analysis_datetime,
    )

    try:
        if verbose:
            logger.enable(__name__)

        template_path = BASE_DIR / "configs" / "decision_prompt.jinja"
        template_content, prompt_meta = resolve_decision_prompt_template(
            settings=settings,
            urls=settings.data_source_url,
            fallback_template_path=template_path,
        )

        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path),
            template_content=template_content,
        )

        enhanced_output = analyzer.analyze_enhanced(
            room_id=room_id,
            analysis_datetime=analysis_datetime,
            similar_case_top_k=int(similar_case_top_k),
            embedding_similarity_weight=float(embedding_similarity_weight),
            env_similarity_weight=float(env_similarity_weight),
            enable_kb_human_prior=bool(enable_kb_human_prior),
            multi_image_boost=bool(multi_image_boost),
        )

        enhanced_dict = _serialize(asdict(enhanced_output))
        monitoring_dict = _build_monitoring_points_output(
            enhanced_output, room_id, analysis_datetime
        )
        skill_feedback: Dict[str, Any] = {
            "enabled": bool(enable_skill_engine),
            "matched_count": 0,
            "constraint_corrections": 0,
        }

        if enable_skill_engine and output_format in {"monitoring", "both"}:
            try:
                skill_library_path = (
                    BASE_DIR / "configs" / "cultivation_skill_library.json"
                )
                skill_engine = CultivationSkillEngine(
                    config_path=skill_library_path,
                    enable_kb_prior=DECISION_ANALYSIS_ENABLE_SKILL_KB_PRIOR,
                )
                skill_context = skill_engine.build_context_from_db(
                    room_id=room_id,
                    analysis_time=analysis_datetime,
                )
                monitoring_dict, skill_feedback = skill_engine.apply(
                    decision_data=monitoring_dict,
                    context=skill_context,
                )
                logger.info(
                    "[ENHANCED_DECISION_ANALYSIS] Skill执行: room=%s, matched=%s, corrections=%s"
                    % (
                        room_id,
                        skill_feedback.get("matched_count", 0),
                        skill_feedback.get("constraint_corrections", 0),
                    )
                )
            except Exception as skill_error:
                logger.warning(
                    f"[ENHANCED_DECISION_ANALYSIS] Skill执行失败 room={room_id}: {skill_error}"
                )
                skill_feedback = {
                    "enabled": True,
                    "error": str(skill_error),
                    "matched_count": 0,
                    "constraint_corrections": 0,
                }

        if output_format == "enhanced":
            data = {"enhanced_decision": enhanced_dict}
        elif output_format == "monitoring":
            data = {
                **monitoring_dict,
                "skill_feedback": skill_feedback,
            }
        else:
            data = {
                "enhanced_decision": enhanced_dict,
                **monitoring_dict,
                "skill_feedback": skill_feedback,
            }

        output_path: Optional[Path] = None
        if persist_output_file:
            if output_file:
                output_path = (
                    Path(output_file) if isinstance(output_file, str) else output_file
                )
            else:
                timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
                output_dir = BASE_DIR.parent / OUTPUT_DIR_NAME
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = (
                    output_dir
                    / f"enhanced_decision_analysis_{room_id}_{timestamp}.json"
                )

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        result.success = True
        result.status = enhanced_output.status
        result.output_file = output_path
        result.data = data
        result.warnings = enhanced_output.metadata.warnings
        result.metadata = {
            "multi_image_count": enhanced_output.metadata.multi_image_count,
            "image_aggregation_method": enhanced_output.metadata.image_aggregation_method,
            "llm_model": enhanced_output.metadata.llm_model,
            "llm_response_time": enhanced_output.metadata.llm_response_time,
            "prompt_source": prompt_meta.get("source"),
            "prompt_uri": prompt_meta.get("prompt_uri"),
            "registered_prompt_uri": prompt_meta.get("registered_prompt_uri"),
            "skill_enabled": bool(enable_skill_engine),
            "skill_kb_prior_enabled": bool(DECISION_ANALYSIS_ENABLE_SKILL_KB_PRIOR),
            "skill_matched_count": skill_feedback.get("matched_count", 0),
            "skill_constraint_corrections": skill_feedback.get(
                "constraint_corrections", 0
            ),
            "skill_kb_prior_used": skill_feedback.get("kb_prior_used", 0),
        }

    except Exception as e:
        result.status = "error"
        result.error_message = str(e)
        logger.error(f"[ENHANCED_DECISION_ANALYSIS] Failed: {e}")

    result.processing_time = (datetime.now() - start_time).total_seconds()

    return result
