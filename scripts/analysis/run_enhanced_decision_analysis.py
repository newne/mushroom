#!/usr/bin/env python3
"""
Enhanced Decision Analysis CLI Script

This script provides an enhanced command-line interface for running decision analysis
with multi-image support and structured parameter adjustments on mushroom growing rooms.

Usage:
    # Basic usage with room ID and datetime
    python scripts/run_enhanced_decision_analysis.py --room-id 611 --datetime "2024-01-15 10:00:00"

    # Use current time
    python scripts/run_enhanced_decision_analysis.py --room-id 611

    # Specify output file
    python scripts/run_enhanced_decision_analysis.py --room-id 611 --output results.json

    # Verbose output
    python scripts/run_enhanced_decision_analysis.py --room-id 611 --verbose

Enhanced Features:
    - Multi-image aggregation and analysis
    - Structured parameter adjustments with actions (maintain/adjust/monitor)
    - Risk assessments and priority levels
    - Enhanced LLM prompting and parsing
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loguru import logger

# 导入日志设置
from utils.loguru_setting import loguru_setting

# 初始化日志设置
loguru_setting(production=False)

# ===================== 日志常量定义 =====================

# 日志标识符常量
LOG_PREFIX = "ENHANCED_DECISION_ANALYSIS"

# 日志编号映射
LOG_CODES = {
    "INIT_START": "001",
    "INIT_SUCCESS": "002",
    "INIT_ERROR": "003",
    "VALIDATION_START": "004",
    "VALIDATION_SUCCESS": "005",
    "VALIDATION_ERROR": "006",
    "PARAM_PARSE": "007",
    "PARAM_SUCCESS": "008",
    "PARAM_ERROR": "009",
    "ANALYZER_INIT_START": "010",
    "ANALYZER_INIT_SUCCESS": "011",
    "ANALYZER_INIT_ERROR": "012",
    "ANALYSIS_START": "013",
    "ANALYSIS_SUCCESS": "014",
    "ANALYSIS_ERROR": "015",
    "TEMPLATE_CHECK": "016",
    "TEMPLATE_FOUND": "017",
    "TEMPLATE_NOT_FOUND": "018",
    "SAVE_START": "019",
    "SAVE_SUCCESS": "020",
    "SAVE_ERROR": "021",
    "IMPORT_ERROR": "022",
    "DB_CONNECTION_ERROR": "023",
    "LLM_CALL_START": "024",
    "LLM_CALL_SUCCESS": "025",
    "LLM_CALL_ERROR": "026",
    "CLIP_MATCH_START": "027",
    "CLIP_MATCH_SUCCESS": "028",
    "CLIP_MATCH_ERROR": "029",
    "MULTI_IMAGE_START": "030",
    "MULTI_IMAGE_SUCCESS": "031",
    "MULTI_IMAGE_ERROR": "032",
    "PERFORMANCE_METRICS": "033",
    "FINAL_SUMMARY": "034",
    "DB_QUERY_START": "035",
    "DB_QUERY_SUCCESS": "036",
    "DB_QUERY_ERROR": "037",
    "IMAGE_PROCESSING_START": "038",
    "IMAGE_PROCESSING_SUCCESS": "039",
    "IMAGE_PROCESSING_ERROR": "040",
    "CLIP_EMBEDDING_START": "041",
    "CLIP_EMBEDDING_SUCCESS": "042",
    "CLIP_EMBEDDING_ERROR": "043",
    "DATA_FETCH_START": "044",
    "DATA_FETCH_SUCCESS": "045",
    "DATA_FETCH_ERROR": "046",
    "LLM_RESPONSE_PARSE_START": "047",
    "LLM_RESPONSE_PARSE_SUCCESS": "048",
    "LLM_RESPONSE_PARSE_ERROR": "049",
    "CONFIG_LOAD_START": "050",
    "CONFIG_LOAD_SUCCESS": "051",
    "CONFIG_LOAD_ERROR": "052",
    "CACHE_HIT": "053",
    "CACHE_MISS": "054",
    "CACHE_UPDATE": "055",
    "REQUEST_SENT": "056",
    "RESPONSE_RECEIVED": "057",
    "RESPONSE_DESERIALIZED": "058",
    "API_CALL_START": "059",
    "API_CALL_SUCCESS": "060",
    "API_CALL_ERROR": "061",
    "CONNECTION_POOL_STATUS": "062",
    "MEMORY_USAGE": "063",
    "THREAD_INFO": "064",
    "ASYNC_OPERATION_START": "065",
    "ASYNC_OPERATION_COMPLETE": "066",
    "ASYNC_OPERATION_FAILED": "067",
    "RETRY_ATTEMPT": "068",
    "RETRY_SUCCESS": "069",
    "RETRY_FAILED": "070",
    "RATE_LIMIT_WAIT": "071",
    "RATE_LIMIT_EXCEEDED": "072",
    "AUTHENTICATION_START": "073",
    "AUTHENTICATION_SUCCESS": "074",
    "AUTHENTICATION_FAILED": "075",
    "TOKEN_REFRESHED": "076",
    "TOKEN_EXPIRED": "077",
    "SECURITY_CHECK": "078",
    "PERMISSION_DENIED": "079",
    "AUDIT_LOG": "080",
    "HEALTH_CHECK": "081",
    "SHUTDOWN_SEQUENCE": "082",
    "HEARTBEAT": "083",
    "BACKGROUND_TASK_START": "084",
    "BACKGROUND_TASK_COMPLETE": "085",
    "BACKGROUND_TASK_FAILED": "086",
    "BATCH_PROCESS_START": "087",
    "BATCH_PROCESS_SUCCESS": "088",
    "BATCH_PROCESS_PARTIAL": "089",
    "BATCH_PROCESS_FAILED": "090",
    "TRANSACTION_START": "091",
    "TRANSACTION_COMMIT": "092",
    "TRANSACTION_ROLLBACK": "093",
    "LOCK_ACQUIRED": "094",
    "LOCK_RELEASED": "095",
    "LOCK_TIMEOUT": "096",
    "METRICS_COLLECTED": "097",
    "EVENT_PUBLISHED": "098",
    "EVENT_CONSUMED": "099",
    "STATE_CHANGE": "100",
    "CONTEXT_INFO": "101",
}


def format_log_message(code: str, message: str, level: str = "INFO") -> str:
    """
    格式化统一日志消息

    Args:
        code: 日志代码
        message: 日志消息
        level: 日志级别

    Returns:
        格式化的日志字符串
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"[{LOG_PREFIX}_{code}] [{timestamp}] [{level}] {message}"


# ===================== 数据模型 =====================


@dataclass
class EnhancedDecisionAnalysisResult:
    """
    增强型决策分析执行结果数据模型

    包含执行状态、错误信息和增强分析结果的结构化对象

    Attributes:
        success: 执行是否成功
        status: 结果状态 ("success", "partial", "error", "fallback")
        room_id: 蘑菇房编号
        analysis_time: 分析执行时间
        output_file: 输出文件路径（如果有）
        result: EnhancedDecisionOutput 对象（如果成功）
        error_message: 错误信息（如果失败）
        warnings: 警告信息列表
        processing_time: 处理耗时（秒）
        metadata: 额外元数据
        multi_image_count: 分析的图像数量
        enhanced_features_used: 使用的增强功能列表
    """

    success: bool
    status: str
    room_id: str
    analysis_time: datetime
    output_file: Optional[Path] = None
    result: Optional[Any] = None  # EnhancedDecisionOutput
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    processing_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    multi_image_count: int = 0
    enhanced_features_used: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """
        将结果转换为字典格式

        Returns:
            包含所有字段的字典
        """
        return {
            "success": self.success,
            "status": self.status,
            "room_id": self.room_id,
            "analysis_time": self.analysis_time.isoformat(),
            "output_file": str(self.output_file) if self.output_file else None,
            "error_message": self.error_message,
            "warnings": self.warnings,
            "processing_time": self.processing_time,
            "metadata": self.metadata,
            "multi_image_count": self.multi_image_count,
            "enhanced_features_used": self.enhanced_features_used,
        }


# ===================== 辅助函数 =====================





def parse_datetime(datetime_str: Optional[str]) -> datetime:
    """
    解析日期时间字符串为datetime对象

    Args:
        datetime_str: 日期时间字符串，格式为 "YYYY-MM-DD HH:MM:SS"
                     如果为None，返回当前时间

    Returns:
        datetime对象

    Raises:
        ValueError: 日期时间格式无效
    """
    logger.debug(
        format_log_message("PARAM_PARSE", f"Parsing datetime string: {datetime_str}")
    )

    if datetime_str is None:
        result = datetime.now()
        logger.debug(
            format_log_message("PARAM_SUCCESS", f"Using current time: {result}")
        )
        return result

    # 尝试不同的时间格式
    formats = [
        ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH:MM:SS"),
        ("%Y-%m-%d %H:%M", "YYYY-MM-DD HH:MM"),
        ("%Y-%m-%d", "YYYY-MM-DD"),
    ]

    for fmt, desc in formats:
        try:
            result = datetime.strptime(datetime_str, fmt)
            logger.info(
                format_log_message(
                    "PARAM_SUCCESS",
                    f"Successfully parsed datetime '{datetime_str}' as format '{desc}': {result}",
                )
            )
            return result
        except ValueError:
            logger.debug(
                format_log_message(
                    "PARAM_PARSE",
                    f"Failed to parse with format '{desc}', trying next format",
                )
            )
            continue

    # 所有格式都失败
    error_msg = (
        f"Invalid datetime format: {datetime_str}. "
        f"Expected formats: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD'"
    )
    logger.error(format_log_message("PARAM_ERROR", error_msg))
    raise ValueError(error_msg)


def format_enhanced_console_output(result) -> str:
    """
    格式化增强决策输出用于控制台显示

    Args:
        result: EnhancedDecisionOutput对象

    Returns:
        格式化的控制台输出字符串
    """
    lines = []
    lines.append("=" * 80)
    lines.append("增强型决策分析结果 / Enhanced Decision Analysis Results")
    lines.append("=" * 80)
    lines.append("")

    # Basic info
    lines.append(f"状态 (Status): {result.status}")
    lines.append(f"库房编号 (Room ID): {result.room_id}")
    lines.append(f"分析时间 (Analysis Time): {result.analysis_time}")
    lines.append("")

    # Multi-image analysis info
    if result.multi_image_analysis:
        lines.append("=" * 80)
        lines.append("多图像分析信息 / Multi-Image Analysis Info")
        lines.append("=" * 80)
        lines.append(
            f"分析图像数量 (Images Analyzed): {result.multi_image_analysis.total_images_analyzed}"
        )
        lines.append(
            f"聚合方法 (Aggregation Method): {result.multi_image_analysis.aggregation_method}"
        )
        lines.append(
            f"置信度分数 (Confidence Score): {result.multi_image_analysis.confidence_score:.2f}"
        )
        lines.append(
            f"视角一致性 (View Consistency): {result.multi_image_analysis.view_consistency}"
        )

        if result.multi_image_analysis.key_observations:
            lines.append("\n关键观察 (Key Observations):")
            for obs in result.multi_image_analysis.key_observations:
                lines.append(f"  • {obs}")
        lines.append("")

    # Strategy
    lines.append("=" * 80)
    lines.append("调控总体策略 / Control Strategy")
    lines.append("=" * 80)
    lines.append(f"核心目标 (Core Objective): {result.strategy.core_objective}")

    if result.strategy.priority_ranking:
        lines.append("\n优先级排序 (Priority Ranking):")
        for i, priority in enumerate(result.strategy.priority_ranking, 1):
            lines.append(f"  {i}. {priority}")

    if result.strategy.key_risk_points:
        lines.append("\n关键风险点 (Key Risk Points):")
        for risk in result.strategy.key_risk_points:
            lines.append(f"  • {risk}")

    lines.append("")

    # Enhanced Device Recommendations
    lines.append("=" * 80)
    lines.append("增强型设备参数建议 / Enhanced Device Recommendations")
    lines.append("=" * 80)

    # Air Cooler
    lines.append("\n【冷风机 / Air Cooler】")
    ac = result.device_recommendations.air_cooler

    def format_parameter_adjustment(param_name, param_adj, unit=""):
        """格式化参数调整信息"""
        action_map = {"maintain": "保持", "adjust": "调整", "monitor": "监控"}
        priority_map = {"low": "低", "medium": "中", "high": "高", "critical": "紧急"}
        urgency_map = {
            "routine": "常规",
            "within_day": "一天内",
            "within_hour": "一小时内",
            "immediate": "立即",
        }

        action_cn = action_map.get(param_adj.action, param_adj.action)
        priority_cn = priority_map.get(param_adj.priority, param_adj.priority)
        urgency_cn = urgency_map.get(param_adj.urgency, param_adj.urgency)

        param_lines = []
        param_lines.append(
            f"  {param_name}: {param_adj.current_value}{unit} → {param_adj.recommended_value}{unit}"
        )
        param_lines.append(
            f"    动作 (Action): {action_cn} | 优先级 (Priority): {priority_cn} | 紧急度 (Urgency): {urgency_cn}"
        )
        param_lines.append(f"    原因 (Reason): {param_adj.change_reason}")
        param_lines.append(
            f"    风险评估 (Risk): 调整风险={param_adj.risk_assessment.adjustment_risk}, 不调整风险={param_adj.risk_assessment.no_action_risk}"
        )
        return param_lines

    lines.extend(format_parameter_adjustment("温度设定 (Temp Set)", ac.tem_set, "°C"))
    lines.extend(
        format_parameter_adjustment("温差设定 (Temp Diff)", ac.tem_diff_set, "°C")
    )
    lines.extend(format_parameter_adjustment("循环开关 (Cycle On/Off)", ac.cyc_on_off))
    lines.extend(
        format_parameter_adjustment(
            "循环开启时间 (Cycle On Time)", ac.cyc_on_time, "分钟"
        )
    )
    lines.extend(
        format_parameter_adjustment(
            "循环关闭时间 (Cycle Off Time)", ac.cyc_off_time, "分钟"
        )
    )
    lines.extend(format_parameter_adjustment("新风联动 (Fresh Air Link)", ac.ar_on_off))
    lines.extend(
        format_parameter_adjustment("加湿联动 (Humidifier Link)", ac.hum_on_off)
    )

    if ac.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in ac.rationale:
            lines.append(f"    • {reason}")

    # Fresh Air Fan
    lines.append("\n【新风机 / Fresh Air Fan】")
    faf = result.device_recommendations.fresh_air_fan
    lines.extend(format_parameter_adjustment("模式 (Mode)", faf.model))
    lines.extend(format_parameter_adjustment("控制方式 (Control)", faf.control))
    lines.extend(format_parameter_adjustment("CO2启动阈值 (CO2 On)", faf.co2_on, "ppm"))
    lines.extend(
        format_parameter_adjustment("CO2停止阈值 (CO2 Off)", faf.co2_off, "ppm")
    )
    lines.extend(format_parameter_adjustment("开启时间 (On Time)", faf.on, "分钟"))
    lines.extend(format_parameter_adjustment("停止时间 (Off Time)", faf.off, "分钟"))

    if faf.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in faf.rationale:
            lines.append(f"    • {reason}")

    # Humidifier
    lines.append("\n【加湿器 / Humidifier】")
    hum = result.device_recommendations.humidifier
    lines.extend(format_parameter_adjustment("模式 (Mode)", hum.model))
    lines.extend(
        format_parameter_adjustment("开启湿度阈值 (On Threshold)", hum.on, "%")
    )
    lines.extend(
        format_parameter_adjustment("停止湿度阈值 (Off Threshold)", hum.off, "%")
    )

    if hum.left_right_strategy:
        lines.append(f"  左右侧策略 (Left/Right Strategy): {hum.left_right_strategy}")
    if hum.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in hum.rationale:
            lines.append(f"    • {reason}")

    # Grow Light
    lines.append("\n【补光灯 / Grow Light】")
    gl = result.device_recommendations.grow_light
    lines.extend(format_parameter_adjustment("模式 (Mode)", gl.model))
    lines.extend(
        format_parameter_adjustment("开启时长 (On Duration)", gl.on_mset, "分钟")
    )
    lines.extend(
        format_parameter_adjustment("停止时长 (Off Duration)", gl.off_mset, "分钟")
    )
    lines.extend(
        format_parameter_adjustment("1#补光开关 (Light 1 On/Off)", gl.on_off_1)
    )
    lines.extend(
        format_parameter_adjustment("1#光源选择 (Light 1 Source)", gl.choose_1)
    )
    lines.extend(
        format_parameter_adjustment("2#补光开关 (Light 2 On/Off)", gl.on_off_2)
    )
    lines.extend(
        format_parameter_adjustment("2#光源选择 (Light 2 Source)", gl.choose_2)
    )
    lines.extend(
        format_parameter_adjustment("3#补光开关 (Light 3 On/Off)", gl.on_off_3)
    )
    lines.extend(
        format_parameter_adjustment("3#光源选择 (Light 3 Source)", gl.choose_3)
    )
    lines.extend(
        format_parameter_adjustment("4#补光开关 (Light 4 On/Off)", gl.on_off_4)
    )
    lines.extend(
        format_parameter_adjustment("4#光源选择 (Light 4 Source)", gl.choose_4)
    )

    if gl.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in gl.rationale:
            lines.append(f"    • {reason}")

    lines.append("")

    # Monitoring Points
    lines.append("=" * 80)
    lines.append("24小时监控重点 / 24-Hour Monitoring Points")
    lines.append("=" * 80)

    if result.monitoring_points.key_time_periods:
        lines.append("\n关键时段 (Key Time Periods):")
        for period in result.monitoring_points.key_time_periods:
            lines.append(f"  • {period}")

    if result.monitoring_points.warning_thresholds:
        lines.append("\n预警阈值 (Warning Thresholds):")
        for param, threshold in result.monitoring_points.warning_thresholds.items():
            lines.append(f"  • {param}: {threshold}")

    if result.monitoring_points.emergency_measures:
        lines.append("\n应急措施 (Emergency Measures):")
        for measure in result.monitoring_points.emergency_measures:
            lines.append(f"  • {measure}")

    lines.append("")

    # Enhanced Metadata
    lines.append("=" * 80)
    lines.append("增强型元数据 / Enhanced Metadata")
    lines.append("=" * 80)
    lines.append(f"数据源 (Data Sources): {result.metadata.data_sources}")
    lines.append(f"相似案例数 (Similar Cases): {result.metadata.similar_cases_count}")
    lines.append(
        f"平均相似度 (Avg Similarity): {result.metadata.avg_similarity_score:.2f}%"
    )
    lines.append(f"LLM模型 (LLM Model): {result.metadata.llm_model}")
    lines.append(
        f"LLM响应时间 (LLM Response Time): {result.metadata.llm_response_time:.2f}秒"
    )
    lines.append(
        f"总处理时间 (Total Processing Time): {result.metadata.total_processing_time:.2f}秒"
    )

    if hasattr(result.metadata, "multi_image_count"):
        lines.append(
            f"多图像数量 (Multi-Image Count): {result.metadata.multi_image_count}"
        )
    if hasattr(result.metadata, "image_aggregation_method"):
        lines.append(
            f"图像聚合方法 (Image Aggregation Method): {result.metadata.image_aggregation_method}"
        )

    if result.metadata.warnings:
        lines.append(f"\n警告 (Warnings): {len(result.metadata.warnings)}")
        for warning in result.metadata.warnings:
            lines.append(f"  ⚠ {warning}")

    if result.metadata.errors:
        lines.append(f"\n错误 (Errors): {len(result.metadata.errors)}")
        for error in result.metadata.errors:
            lines.append(f"  ✗ {error}")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def save_enhanced_json_output(result, output_path: Path) -> None:
    """
    保存增强决策输出到JSON文件

    Args:
        result: EnhancedDecisionOutput对象
        output_path: 输出JSON文件路径
    """
    logger.debug(
        format_log_message(
            "SAVE_START", f"Starting to save enhanced output to: {output_path}"
        )
    )

    def serialize_parameter_adjustment(param_adj):
        """序列化参数调整对象"""
        return {
            "current_value": param_adj.current_value,
            "recommended_value": param_adj.recommended_value,
            "action": param_adj.action,
            "change_reason": param_adj.change_reason,
            "priority": param_adj.priority,
            "urgency": param_adj.urgency,
            "risk_assessment": {
                "adjustment_risk": param_adj.risk_assessment.adjustment_risk,
                "no_action_risk": param_adj.risk_assessment.no_action_risk,
                "impact_scope": param_adj.risk_assessment.impact_scope,
            },
        }

    # Convert result to dictionary
    output_dict = {
        "status": result.status,
        "room_id": result.room_id,
        "analysis_time": result.analysis_time.isoformat(),
        "strategy": {
            "core_objective": result.strategy.core_objective,
            "priority_ranking": result.strategy.priority_ranking,
            "key_risk_points": result.strategy.key_risk_points,
        },
        "device_recommendations": {
            "air_cooler": {
                "tem_set": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.tem_set
                ),
                "tem_diff_set": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.tem_diff_set
                ),
                "cyc_on_off": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.cyc_on_off
                ),
                "cyc_on_time": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.cyc_on_time
                ),
                "cyc_off_time": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.cyc_off_time
                ),
                "ar_on_off": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.ar_on_off
                ),
                "hum_on_off": serialize_parameter_adjustment(
                    result.device_recommendations.air_cooler.hum_on_off
                ),
                "rationale": result.device_recommendations.air_cooler.rationale,
            },
            "fresh_air_fan": {
                "model": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.model
                ),
                "control": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.control
                ),
                "co2_on": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.co2_on
                ),
                "co2_off": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.co2_off
                ),
                "on": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.on
                ),
                "off": serialize_parameter_adjustment(
                    result.device_recommendations.fresh_air_fan.off
                ),
                "rationale": result.device_recommendations.fresh_air_fan.rationale,
            },
            "humidifier": {
                "model": serialize_parameter_adjustment(
                    result.device_recommendations.humidifier.model
                ),
                "on": serialize_parameter_adjustment(
                    result.device_recommendations.humidifier.on
                ),
                "off": serialize_parameter_adjustment(
                    result.device_recommendations.humidifier.off
                ),
                "left_right_strategy": result.device_recommendations.humidifier.left_right_strategy,
                "rationale": result.device_recommendations.humidifier.rationale,
            },
            "grow_light": {
                "model": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.model
                ),
                "on_mset": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.on_mset
                ),
                "off_mset": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.off_mset
                ),
                "on_off_1": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.on_off_1
                ),
                "choose_1": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.choose_1
                ),
                "on_off_2": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.on_off_2
                ),
                "choose_2": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.choose_2
                ),
                "on_off_3": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.on_off_3
                ),
                "choose_3": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.choose_3
                ),
                "on_off_4": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.on_off_4
                ),
                "choose_4": serialize_parameter_adjustment(
                    result.device_recommendations.grow_light.choose_4
                ),
                "rationale": result.device_recommendations.grow_light.rationale,
            },
        },
        "monitoring_points": {
            "key_time_periods": result.monitoring_points.key_time_periods,
            "warning_thresholds": result.monitoring_points.warning_thresholds,
            "emergency_measures": result.monitoring_points.emergency_measures,
        },
        "multi_image_analysis": {
            "total_images_analyzed": result.multi_image_analysis.total_images_analyzed
            if result.multi_image_analysis
            else 0,
            "image_quality_scores": result.multi_image_analysis.image_quality_scores
            if result.multi_image_analysis
            else [],
            "aggregation_method": result.multi_image_analysis.aggregation_method
            if result.multi_image_analysis
            else "single_image",
            "confidence_score": result.multi_image_analysis.confidence_score
            if result.multi_image_analysis
            else 0.0,
            "view_consistency": result.multi_image_analysis.view_consistency
            if result.multi_image_analysis
            else "unknown",
            "key_observations": result.multi_image_analysis.key_observations
            if result.multi_image_analysis
            else [],
        }
        if result.multi_image_analysis
        else None,
        "metadata": {
            "data_sources": result.metadata.data_sources,
            "similar_cases_count": result.metadata.similar_cases_count,
            "avg_similarity_score": result.metadata.avg_similarity_score,
            "llm_model": result.metadata.llm_model,
            "llm_response_time": result.metadata.llm_response_time,
            "total_processing_time": result.metadata.total_processing_time,
            "warnings": result.metadata.warnings,
            "errors": result.metadata.errors,
            "multi_image_count": getattr(result.metadata, "multi_image_count", 0),
            "image_aggregation_method": getattr(
                result.metadata, "image_aggregation_method", "single_image"
            ),
            "enhanced_format": True,
        },
    }

    # Write to file with pretty formatting
    try:
        logger.debug(
            format_log_message(
                "SAVE_START", f"Writing JSON data to file: {output_path}"
            )
        )
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_dict, f, ensure_ascii=False, indent=2)

        file_size = output_path.stat().st_size if output_path.exists() else 0
        logger.info(
            format_log_message(
                "SAVE_SUCCESS",
                f"Enhanced results saved successfully: {output_path} (size: {file_size} bytes)",
            )
        )

    except Exception as e:
        error_msg = f"Failed to save enhanced results to {output_path}: {str(e)}"
        logger.error(format_log_message("SAVE_ERROR", error_msg))
        raise


def generate_enhanced_output_filename(
    room_id: str, analysis_datetime: datetime, output_dir: Optional[Path] = None
) -> Path:
    """
    生成增强输出文件名

    Args:
        room_id: 蘑菇房编号
        analysis_datetime: 分析时间
        output_dir: 输出目录，默认为项目根目录的output文件夹

    Returns:
        完整的输出文件路径
    """
    logger.debug(
        format_log_message(
            "PARAM_PARSE",
            f"Generating output filename for room_id: {room_id}, datetime: {analysis_datetime}",
        )
    )

    try:
        from global_const.global_const import BASE_DIR

        # 确定输出目录
        if output_dir is None:
            output_dir = BASE_DIR.parent / "output"
            logger.debug(
                format_log_message(
                    "PARAM_SUCCESS", f"Using default output directory: {output_dir}"
                )
            )
        else:
            logger.debug(
                format_log_message(
                    "PARAM_SUCCESS", f"Using custom output directory: {output_dir}"
                )
            )

        # 确保输出目录存在
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(
            format_log_message(
                "PARAM_SUCCESS", f"Output directory ensured: {output_dir}"
            )
        )

        # 生成文件名
        timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
        filename = f"enhanced_decision_analysis_{room_id}_{timestamp}.json"
        full_path = output_dir / filename

        logger.info(
            format_log_message(
                "PARAM_SUCCESS", f"Generated output filename: {full_path}"
            )
        )
        return full_path

    except Exception as e:
        error_msg = f"Failed to generate output filename: {str(e)}"
        logger.error(format_log_message("PARAM_ERROR", error_msg))
        raise


# ===================== 核心执行函数 =====================


def execute_enhanced_decision_analysis(
    room_id: str,
    analysis_datetime: Optional[datetime] = None,
    output_file: Optional[Union[str, Path]] = None,
    verbose: bool = False,
) -> EnhancedDecisionAnalysisResult:
    """
    执行增强决策分析的主要入口函数

    此函数封装了完整的增强决策分析流程，包括：
    - 多图像数据提取和聚合
    - 增强的CLIP相似度匹配
    - 增强的模板渲染
    - 增强的LLM决策生成
    - 结构化参数调整验证和格式化
    - 增强的JSON文件输出

    Args:
        room_id: 蘑菇房编号（"607", "608", "611", "612"）
        analysis_datetime: 分析时间点，默认为当前时间
        output_file: 输出JSON文件路径，默认自动生成
        verbose: 是否启用详细日志输出

    Returns:
        EnhancedDecisionAnalysisResult: 包含执行状态、错误信息和增强结果的结构化对象
    """
    import time
    import psutil
    import os

    start_time = time.time()

    logger.info(
        format_log_message(
            "ANALYSIS_START", f"Starting enhanced decision analysis for room: {room_id}"
        )
    )
    logger.debug(
        format_log_message(
            "PARAM_PARSE",
            f"Input parameters - room_id: {room_id}, analysis_datetime: {analysis_datetime}, output_file: {output_file}, verbose: {verbose}",
        )
    )

    # 记录系统资源使用情况
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    logger.info(
        format_log_message(
            "MEMORY_USAGE", 
            f"Initial memory usage: RSS={memory_info.rss / 1024 / 1024:.2f}MB, VMS={memory_info.vms / 1024 / 1024:.2f}MB"
        )
    )

    # 初始化结果对象
    if analysis_datetime is None:
        analysis_datetime = datetime.now()
        logger.debug(
            format_log_message(
                "PARAM_SUCCESS", f"Using current analysis time: {analysis_datetime}"
            )
        )

    result = EnhancedDecisionAnalysisResult(
        success=False,
        status="pending",
        room_id=room_id,
        analysis_time=analysis_datetime,
        warnings=[],
        enhanced_features_used=[],
    )

    logger.info(
        format_log_message("VALIDATION_START", f"Validating room_id: {room_id}")
    )

    # 验证房间ID
    try:
        from global_const.const_config import MUSHROOM_ROOM_IDS

        if room_id not in MUSHROOM_ROOM_IDS:
            result.status = "error"
            result.error_message = (
                f"Invalid room_id: {room_id}. Must be one of {MUSHROOM_ROOM_IDS}"
            )
            result.processing_time = time.time() - start_time
            logger.error(
                format_log_message(
                    "VALIDATION_ERROR",
                    f"Invalid room_id validation: {result.error_message}",
                )
            )
            return result

        logger.info(
            format_log_message(
                "VALIDATION_SUCCESS", f"Room ID validation passed: {room_id}"
            )
        )

    except ImportError as e:
        result.status = "error"
        result.error_message = f"Failed to import room configuration: {str(e)}"
        result.processing_time = time.time() - start_time
        logger.error(
            format_log_message("IMPORT_ERROR", f"Import error during validation: {e}")
        )
        return result

    try:
        # 导入依赖
        logger.debug(
            format_log_message("ANALYZER_INIT_START", "Importing dependencies...")
        )

        from global_const.global_const import (
            settings,
            static_settings,
            pgsql_engine,
            BASE_DIR,
        )
        from decision_analysis.decision_analyzer import DecisionAnalyzer

        logger.debug(
            format_log_message("IMPORT_SUCCESS", "Dependencies imported successfully")
        )

        # 获取模板路径
        template_path = BASE_DIR / "configs" / "decision_prompt.jinja"
        logger.debug(
            format_log_message(
                "TEMPLATE_CHECK", f"Checking template path: {template_path}"
            )
        )

        if not template_path.exists():
            result.status = "error"
            result.error_message = f"Template file not found: {template_path}"
            result.processing_time = time.time() - start_time
            logger.error(format_log_message("TEMPLATE_NOT_FOUND", result.error_message))
            return result

        logger.info(
            format_log_message(
                "TEMPLATE_FOUND", f"Template file found: {template_path}"
            )
        )

        # 初始化DecisionAnalyzer
        logger.info(
            format_log_message(
                "ANALYZER_INIT_START",
                f"Initializing DecisionAnalyzer with template: {template_path}",
            )
        )

        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path),
        )

        logger.info(
            format_log_message(
                "ANALYZER_INIT_SUCCESS", "DecisionAnalyzer initialized successfully"
            )
        )
        logger.debug(
            format_log_message(
                "DB_CONNECTION",
                f"Database engine configured: {type(pgsql_engine).__name__}",
            )
        )

        # 执行增强分析
        analysis_start = time.time()
        logger.info(
            format_log_message(
                "ANALYSIS_START",
                f"Starting enhanced analysis for room {room_id} at {analysis_datetime}",
            )
        )
        
        # 记录分析前的上下文信息
        logger.info(
            format_log_message(
                "CONTEXT_INFO",
                f"Analysis context - Room: {room_id}, Time: {analysis_datetime}, "
                f"Template: {template_path.name}"
            )
        )

        enhanced_decision_output = analyzer.analyze_enhanced(
            room_id=room_id, analysis_datetime=analysis_datetime
        )

        analysis_duration = time.time() - analysis_start
        logger.info(
            format_log_message(
                "ANALYSIS_SUCCESS",
                f"Enhanced analysis completed successfully in {analysis_duration:.2f}s",
            )
        )
        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS",
                f"Analysis result status: {enhanced_decision_output.status}",
            )
        )

        # 记录使用的增强功能
        result.enhanced_features_used = [
            "multi_image_aggregation",
            "structured_parameter_adjustments",
            "risk_assessments",
            "priority_levels",
            "enhanced_llm_prompting",
        ]

        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS",
                f"Enhanced features used: {', '.join(result.enhanced_features_used)}",
            )
        )

        # 确定输出文件路径
        if output_file:
            output_path = (
                Path(output_file) if isinstance(output_file, str) else output_file
            )
            logger.debug(
                format_log_message(
                    "PARAM_PARSE", f"Using custom output file: {output_path}"
                )
            )
        else:
            output_path = generate_enhanced_output_filename(room_id, analysis_datetime)
            logger.debug(
                format_log_message(
                    "PARAM_SUCCESS", f"Generated output file: {output_path}"
                )
            )

        # 保存增强JSON输出
        save_start = time.time()
        save_enhanced_json_output(enhanced_decision_output, output_path)
        save_duration = time.time() - save_start
        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS", f"JSON save completed in {save_duration:.2f}s"
            )
        )

        # 设置成功结果
        result.success = True
        result.status = enhanced_decision_output.status
        result.result = enhanced_decision_output
        result.output_file = output_path
        result.warnings = enhanced_decision_output.metadata.warnings.copy()
        result.multi_image_count = getattr(
            enhanced_decision_output.metadata, "multi_image_count", 0
        )
        result.metadata = {
            "data_sources": enhanced_decision_output.metadata.data_sources,
            "similar_cases_count": enhanced_decision_output.metadata.similar_cases_count,
            "avg_similarity_score": enhanced_decision_output.metadata.avg_similarity_score,
            "llm_model": enhanced_decision_output.metadata.llm_model,
            "llm_response_time": enhanced_decision_output.metadata.llm_response_time,
            "multi_image_count": getattr(
                enhanced_decision_output.metadata, "multi_image_count", 0
            ),
            "image_aggregation_method": getattr(
                enhanced_decision_output.metadata,
                "image_aggregation_method",
                "single_image",
            ),
        }

        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS", f"Multi-image count: {result.multi_image_count}"
            )
        )
        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS",
                f"Similar cases found: {result.metadata['similar_cases_count']}",
            )
        )
        logger.debug(
            format_log_message(
                "PERFORMANCE_METRICS",
                f"Average similarity: {result.metadata['avg_similarity_score']:.2f}%",
            )
        )

        # 检查是否有错误
        critical_errors = []
        warning_errors = []
        for error in enhanced_decision_output.metadata.errors:
            if "No embedding data found" not in error:
                critical_errors.append(error)
            else:
                warning_errors.append(error)

        if critical_errors:
            result.success = False
            result.status = "partial"
            result.error_message = "; ".join(critical_errors)
            logger.warning(
                format_log_message(
                    "ANALYSIS_ERROR",
                    f"Analysis completed with critical errors: {len(critical_errors)}",
                )
            )
        elif warning_errors:
            result.success = True
            result.status = "success"
            result.warnings.extend(warning_errors)
            logger.info(
                format_log_message(
                    "ANALYSIS_SUCCESS",
                    f"Analysis completed with warnings: {len(warning_errors)}",
                )
            )
        else:
            logger.info(
                format_log_message(
                    "ANALYSIS_SUCCESS", "Analysis completed successfully without errors"
                )
            )

        logger.info(
            format_log_message(
                "SAVE_SUCCESS", f"Enhanced results saved to: {output_path}"
            )
        )

    except ImportError as e:
        result.status = "error"
        result.error_message = f"Failed to import dependencies: {e}"
        logger.error(
            format_log_message("IMPORT_ERROR", f"Import error during analysis: {e}")
        )

    except Exception as e:
        result.status = "error"
        result.error_message = f"Enhanced analysis failed: {str(e)}"
        logger.error(
            format_log_message("ANALYSIS_ERROR", f"Enhanced analysis failed: {e}"),
            exc_info=verbose,
        )

    result.processing_time = time.time() - start_time
    
    # 记录最终内存使用情况
    memory_info = process.memory_info()
    logger.info(
        format_log_message(
            "MEMORY_USAGE", 
            f"Final memory usage: RSS={memory_info.rss / 1024 / 1024:.2f}MB, VMS={memory_info.vms / 1024 / 1024:.2f}MB, "
            f"Processing time: {result.processing_time:.2f}s"
        )
    )

    # 记录最终状态
    if result.success:
        logger.info(
            format_log_message(
                "FINAL_SUMMARY",
                f"Analysis completed successfully - room: {room_id}, status: {result.status}, "
                f"multi_images: {result.multi_image_count}, processing_time: {result.processing_time:.2f}s, "
                f"output_file: {result.output_file}",
            )
        )
    else:
        logger.error(
            format_log_message(
                "ANALYSIS_ERROR",
                f"Analysis failed - room: {room_id}, error: {result.error_message}, "
                f"processing_time: {result.processing_time:.2f}s",
            )
        )

    return result


# ===================== 命令行入口 =====================


def main() -> int:
    """
    主CLI入口点

    解析命令行参数，初始化增强DecisionAnalyzer，
    运行增强分析，并输出结果到控制台和JSON文件

    Returns:
        退出码：0表示成功，1表示失败
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Run enhanced decision analysis for mushroom growing rooms",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Enhanced Features:
  • Multi-image aggregation and analysis
  • Structured parameter adjustments with actions (maintain/adjust/monitor)
  • Risk assessments and priority levels
  • Enhanced LLM prompting and parsing
  • Comprehensive validation and fallback mechanisms

Examples:
  # Analyze room 611 at current time with enhanced features
  python scripts/run_enhanced_decision_analysis.py --room-id 611
  
  # Analyze room 611 at specific datetime
  python scripts/run_enhanced_decision_analysis.py --room-id 611 --datetime "2024-01-15 10:00:00"
  
  # Save results to custom file
  python scripts/run_enhanced_decision_analysis.py --room-id 611 --output my_enhanced_results.json
  
  # Verbose output with debug logs
  python scripts/run_enhanced_decision_analysis.py --room-id 611 --verbose
        """,
    )

    parser.add_argument(
        "--room-id",
        type=str,
        required=False,
        default="611",
        choices=["607", "608", "611", "612"],
        help="Room ID (607, 608, 611, or 612)",
    )

    parser.add_argument(
        "--datetime",
        type=str,
        default=None,
        help="Analysis datetime in format 'YYYY-MM-DD HH:MM:SS' (default: current time)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: enhanced_decision_analysis_<room_id>_<timestamp>.json)",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output (DEBUG level logs)",
    )

    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Skip console output, only save to JSON file",
    )

    args = parser.parse_args()

    # 日志设置已在模块加载时初始化
    pass

    logger.info("=" * 80)
    logger.info("Enhanced Decision Analysis CLI")
    logger.info("=" * 80)
    
    # 记录CLI启动上下文
    logger.info(
        format_log_message(
            "CONTEXT_INFO",
            f"CLI started with args - room_id: {args.room_id}, datetime: {args.datetime}, "
            f"output: {args.output}, verbose: {args.verbose}, no_console: {args.no_console}"
        )
    )

    # Parse datetime
    try:
        logger.debug(
            format_log_message(
                "PARAM_PARSE", f"Parsing datetime argument: {args.datetime}"
            )
        )
        analysis_datetime = parse_datetime(args.datetime)
        logger.info(
            format_log_message(
                "PARAM_SUCCESS",
                f"CLI parameters parsed - Room ID: {args.room_id}, Analysis Time: {analysis_datetime}",
            )
        )
    except ValueError as e:
        logger.error(
            format_log_message("PARAM_ERROR", f"CLI datetime parsing failed: {e}")
        )
        return 1

    # Execute enhanced decision analysis
    logger.info(
        format_log_message(
            "ANALYSIS_START", "Executing enhanced decision analysis from CLI"
        )
    )
    result = execute_enhanced_decision_analysis(
        room_id=args.room_id,
        analysis_datetime=analysis_datetime,
        output_file=args.output,
        verbose=args.verbose,
    )

    # Output results to console
    if not args.no_console and result.success and result.result:
        logger.debug(format_log_message("PARAM_PARSE", "Formatting console output"))
        console_output = format_enhanced_console_output(result.result)
        print(console_output)

    # Final summary
    logger.info("=" * 80)
    logger.info(format_log_message("FINAL_SUMMARY", "Enhanced Analysis Summary"))
    logger.info("=" * 80)
    logger.info(format_log_message("FINAL_SUMMARY", f"Success: {result.success}"))
    logger.info(format_log_message("FINAL_SUMMARY", f"Status: {result.status}"))
    logger.info(
        format_log_message(
            "FINAL_SUMMARY", f"Multi-Image Count: {result.multi_image_count}"
        )
    )
    logger.info(
        format_log_message(
            "FINAL_SUMMARY",
            f"Enhanced Features Used: {', '.join(result.enhanced_features_used)}",
        )
    )
    logger.info(
        format_log_message(
            "PERFORMANCE_METRICS",
            f"Total Processing Time: {result.processing_time:.2f}s",
        )
    )

    if result.warnings:
        for i, warning in enumerate(result.warnings, 1):
            logger.warning(
                format_log_message("ANALYSIS_ERROR", f"Warning {i}: {warning}")
            )

    if result.error_message:
        logger.error(
            format_log_message("ANALYSIS_ERROR", f"Final Error: {result.error_message}")
        )

    if result.output_file:
        logger.info(
            format_log_message(
                "SAVE_SUCCESS", f"Output File: {result.output_file.absolute()}"
            )
        )

    logger.info("=" * 80)

    # Return exit code based on status
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
