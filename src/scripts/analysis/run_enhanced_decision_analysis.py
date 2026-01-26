#!/usr/bin/env python3
"""
增强决策分析CLI脚本

该脚本提供了增强的命令行接口，用于运行具有多图像支持和结构化参数调整的
蘑菇房决策分析。

使用方法:
    # 基本用法：指定房间ID和日期时间
    python scripts/run_enhanced_decision_analysis.py --room-id 611 \
        --datetime "2024-01-15 10:00:00"

    # 使用当前时间
    python scripts/run_enhanced_decision_analysis.py --room-id 611

    # 指定输出文件
    python scripts/run_enhanced_decision_analysis.py --room-id 611 \
        --output results.json

    # 详细输出
    python scripts/run_enhanced_decision_analysis.py --room-id 611 --verbose

增强功能:
    - 多图像聚合和分析
    - 结构化参数调整，包含动作类型（保持/调整/监控）
    - 风险评估和优先级分级
    - 增强的LLM提示和解析
"""

# 标准库导入
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# 第三方库导入
import psutil
from loguru import logger

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.loguru_setting import loguru_setting

# 初始化日志设置
loguru_setting(production=False)


# ===================== 常量定义 =====================

# 日志标识符前缀
LOG_PREFIX = "ENHANCED_DECISION_ANALYSIS"

# 支持的库房ID列表
MUSHROOM_ROOM_IDS = ["607", "608", "611", "612"]

# 输出格式选项
OUTPUT_FORMATS = ["enhanced", "monitoring", "both"]

# 日期时间格式列表
DATETIME_FORMATS = [
    ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH:MM:SS"),
    ("%Y-%m-%d %H:%M", "YYYY-MM-DD HH:MM"),
    ("%Y-%m-%d", "YYYY-MM-DD"),
]

# 日志编号映射 - 按功能模块分组
LOG_CODES = {
    # 系统初始化 (001-010)
    "SYSTEM_INIT_START": "001",
    "SYSTEM_INIT_SUCCESS": "002",
    "SYSTEM_INIT_ERROR": "003",
    "DEPENDENCY_IMPORT_START": "004",
    "DEPENDENCY_IMPORT_SUCCESS": "005",
    "DEPENDENCY_IMPORT_ERROR": "006",

    # 参数验证 (011-020)
    "PARAM_VALIDATION_START": "011",
    "PARAM_VALIDATION_SUCCESS": "012",
    "PARAM_VALIDATION_ERROR": "013",
    "ROOM_ID_VALIDATION": "014",
    "DATETIME_PARSING": "015",
    "OUTPUT_PATH_GENERATION": "016",

    # 分析器初始化 (021-030)
    "ANALYZER_INIT_START": "021",
    "ANALYZER_INIT_SUCCESS": "022",
    "ANALYZER_INIT_ERROR": "023",
    "TEMPLATE_VALIDATION": "024",
    "DB_CONNECTION_CHECK": "025",

    # 数据提取 (031-050)
    "DATA_EXTRACTION_START": "031",
    "DATA_EXTRACTION_SUCCESS": "032",
    "DATA_EXTRACTION_ERROR": "033",
    "MULTI_IMAGE_FETCH_START": "034",
    "MULTI_IMAGE_FETCH_SUCCESS": "035",
    "MULTI_IMAGE_FETCH_ERROR": "036",
    "ENV_DATA_FETCH_START": "037",
    "ENV_DATA_FETCH_SUCCESS": "038",
    "ENV_DATA_FETCH_ERROR": "039",
    "DEVICE_CONFIG_FETCH_START": "040",
    "DEVICE_CONFIG_FETCH_SUCCESS": "041",
    "DEVICE_CONFIG_FETCH_ERROR": "042",

    # CLIP匹配 (051-060)
    "CLIP_MATCHING_START": "051",
    "CLIP_MATCHING_SUCCESS": "052",
    "CLIP_MATCHING_ERROR": "053",
    "SIMILARITY_CALCULATION": "054",
    "HISTORICAL_CASES_FOUND": "055",

    # LLM调用 (061-080)
    "LLM_REQUEST_START": "061",
    "LLM_REQUEST_SUCCESS": "062",
    "LLM_REQUEST_ERROR": "063",
    "LLM_RESPONSE_PARSING_START": "064",
    "LLM_RESPONSE_PARSING_SUCCESS": "065",
    "LLM_RESPONSE_PARSING_ERROR": "066",
    "LLM_RETRY_ATTEMPT": "067",
    "LLM_FALLBACK_TRIGGERED": "068",

    # 结果处理 (081-100)
    "RESULT_PROCESSING_START": "081",
    "RESULT_PROCESSING_SUCCESS": "082",
    "RESULT_PROCESSING_ERROR": "083",
    "PARAMETER_ADJUSTMENT_VALIDATION": "084",
    "RISK_ASSESSMENT_CALCULATION": "085",
    "MONITORING_POINTS_GENERATION": "086",

    # 文件保存 (101-110)
    "FILE_SAVE_START": "101",
    "FILE_SAVE_SUCCESS": "102",
    "FILE_SAVE_ERROR": "103",
    "JSON_SERIALIZATION": "104",
    "OUTPUT_FORMAT_CONVERSION": "105",

    # 性能监控 (111-120)
    "PERFORMANCE_MEMORY_USAGE": "111",
    "PERFORMANCE_EXECUTION_TIME": "112",
    "PERFORMANCE_THROUGHPUT": "113",
    "PERFORMANCE_BOTTLENECK": "114",

    # 错误处理 (121-130)
    "ERROR_RECOVERY_START": "121",
    "ERROR_RECOVERY_SUCCESS": "122",
    "ERROR_RECOVERY_FAILED": "123",
    "FALLBACK_MECHANISM_TRIGGERED": "124",
    "WARNING_THRESHOLD_EXCEEDED": "125",

    # 业务流程 (131-150)
    "BUSINESS_FLOW_START": "131",
    "BUSINESS_FLOW_CHECKPOINT": "132",
    "BUSINESS_FLOW_COMPLETE": "133",
    "DECISION_STRATEGY_GENERATED": "134",
    "DEVICE_RECOMMENDATIONS_READY": "135",
    "MONITORING_SCHEDULE_CREATED": "136",

    # 系统状态 (151-160)
    "SYSTEM_HEALTH_CHECK": "151",
    "RESOURCE_ALLOCATION": "152",
    "CACHE_STATUS": "153",
    "CONNECTION_POOL_STATUS": "154",
    "FINAL_SUMMARY": "155",
}


# ===================== 工具函数 =====================

def log_message(code: str, message: str, **kwargs: Any) -> str:
    """
    生成标准化的中文日志消息。

    Args:
        code: 日志代码（来自LOG_CODES）
        message: 中文日志消息
        **kwargs: 额外的上下文参数

    Returns:
        格式化的日志字符串
    """
    # 获取日志编号
    log_number = LOG_CODES.get(code, "999")

    # 构建基础日志格式
    log_prefix = f"[{LOG_PREFIX}_{log_number}]"

    # 添加上下文信息
    if kwargs:
        context_parts = _build_context_parts(kwargs)
        if context_parts:
            message = f"{message} | {' | '.join(context_parts)}"

    return f"{log_prefix} {message}"


def _build_context_parts(kwargs: Dict[str, Any]) -> List[str]:
    """
    构建日志上下文部分。

    Args:
        kwargs: 上下文参数字典

    Returns:
        格式化的上下文字符串列表
    """
    context_parts = []
    context_mapping = {
        "room_id": "库房",
        "processing_time": "耗时",
        "count": "数量",
        "size": "大小",
        "status": "状态",
        "error": "错误",
    }

    for key, value in kwargs.items():
        if key in context_mapping:
            if key == "processing_time":
                context_parts.append(f"{context_mapping[key]}={value:.2f}秒")
            else:
                context_parts.append(f"{context_mapping[key]}={value}")
        else:
            context_parts.append(f"{key}={value}")

    return context_parts


# ===================== 数据模型 =====================

@dataclass
class EnhancedDecisionAnalysisResult:
    """
    增强型决策分析执行结果数据模型。

    Attributes:
        success: 执行是否成功
        room_id: 库房编号
        analysis_datetime: 分析时间
        enhanced_decision_output: 增强决策输出数据
        output_file: 输出文件路径
        processing_time: 处理耗时（秒）
        error_message: 错误信息（如果有）
        metadata: 元数据信息
        warnings: 警告信息列表
    """
    success: bool = False
    room_id: Optional[str] = None
    analysis_datetime: Optional[datetime] = None
    enhanced_decision_output: Optional[Dict[str, Any]] = None
    output_file: Optional[Path] = None
    processing_time: float = 0.0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ===================== 日期时间处理 =====================

def parse_datetime(datetime_str: Optional[str]) -> datetime:
    """
    解析日期时间字符串为datetime对象。

    Args:
        datetime_str: 日期时间字符串，支持多种格式

    Returns:
        解析后的datetime对象

    Raises:
        ValueError: 日期时间格式无效
    """
    logger.debug(log_message(
        "DATETIME_PARSING",
        "开始解析日期时间字符串",
        input=datetime_str
    ))

    if datetime_str is None:
        result = datetime.now()
        logger.debug(log_message(
            "DATETIME_PARSING",
            "使用当前时间",
            current_time=result
        ))
        return result

    # 尝试多种日期时间格式
    for fmt, desc in DATETIME_FORMATS:
        try:
            result = datetime.strptime(datetime_str, fmt)
            logger.info(log_message(
                "DATETIME_PARSING",
                f"成功解析日期时间格式 '{desc}'",
                input=datetime_str,
                result=result
            ))
            return result
        except ValueError:
            logger.debug(log_message(
                "DATETIME_PARSING",
                f"格式 '{desc}' 解析失败，尝试下一个格式"
            ))
            continue

    # 所有格式都失败
    error_msg = (
        f"无效的日期时间格式: {datetime_str}. "
        f"支持的格式: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', "
        f"或 'YYYY-MM-DD'"
    )
    logger.error(log_message("PARAM_VALIDATION_ERROR", error_msg))
    raise ValueError(error_msg)


# ===================== 输出格式化 =====================

def format_enhanced_console_output(result: EnhancedDecisionAnalysisResult
                                   ) -> str:
    """
    格式化增强决策输出用于控制台显示。

    Args:
        result: 增强决策分析结果

    Returns:
        格式化的控制台输出字符串
    """
    if not result.success or not result.enhanced_decision_output:
        return "❌ 增强决策分析失败或无输出数据"

    output_lines = []
    enhanced_output = result.enhanced_decision_output

    # 标题
    output_lines.extend([
        "=" * 80,
        f"🍄 增强决策分析结果 - 库房 {result.room_id}",
        "=" * 80,
        f"📅 分析时间: {result.analysis_datetime}",
        f"⏱️  处理耗时: {result.processing_time:.2f}秒",
        ""
    ])

    # 核心决策信息
    if hasattr(enhanced_output, 'strategy') and hasattr(enhanced_output.strategy, 'core_objective'):
        output_lines.extend([
            "🎯 核心决策目标:",
            f"   {enhanced_output.strategy.core_objective}",
            ""
        ])

    # 设备推荐信息
    if hasattr(enhanced_output, 'device_recommendations'):
        device_recs = enhanced_output.device_recommendations
        output_lines.append("⚙️  设备参数推荐:")
        
        # 空调设备
        if hasattr(device_recs, 'air_cooler'):
            air_cooler = device_recs.air_cooler
            output_lines.extend([
                "   📱 空调设备:",
                f"      温度设定: {getattr(air_cooler.tem_set, 'recommended_value', 'N/A')}°C",
                f"      动作类型: {getattr(air_cooler.tem_set, 'action', 'N/A')}",
                ""
            ])
        
        # 新风扇设备
        if hasattr(device_recs, 'fresh_air_fan'):
            fresh_air_fan = device_recs.fresh_air_fan
            output_lines.extend([
                "   🌬️  新风扇设备:",
                f"      工作模式: {getattr(fresh_air_fan.model, 'recommended_value', 'N/A')}",
                f"      动作类型: {getattr(fresh_air_fan.model, 'action', 'N/A')}",
                ""
            ])
        
        # 加湿器设备
        if hasattr(device_recs, 'humidifier'):
            humidifier = device_recs.humidifier
            output_lines.extend([
                "   💧 加湿器设备:",
                f"      工作模式: {getattr(humidifier.model, 'recommended_value', 'N/A')}",
                f"      动作类型: {getattr(humidifier.model, 'action', 'N/A')}",
                ""
            ])
        
        # 生长灯设备
        if hasattr(device_recs, 'grow_light'):
            grow_light = device_recs.grow_light
            output_lines.extend([
                "   💡 生长灯设备:",
                f"      工作模式: {getattr(grow_light.model, 'recommended_value', 'N/A')}",
                f"      动作类型: {getattr(grow_light.model, 'action', 'N/A')}",
                ""
            ])

    # 多图像分析信息
    if hasattr(enhanced_output, 'multi_image_analysis'):
        multi_img = enhanced_output.multi_image_analysis
        output_lines.extend([
            "🖼️  多图像分析:",
            f"   图像数量: {getattr(multi_img, 'total_images_analyzed', 'N/A')}",
            f"   一致性评分: {getattr(multi_img, 'confidence_score', 'N/A')}",
            f"   聚合方法: {getattr(multi_img, 'aggregation_method', 'N/A')}",
            ""
        ])

    # 监控建议
    if hasattr(enhanced_output, 'monitoring_points'):
        monitoring = enhanced_output.monitoring_points
        output_lines.extend([
            "📊 监控建议:",
            f"   关键时间段: {len(getattr(monitoring, 'key_time_periods', []))} 个",
            f"   警告阈值: {len(getattr(monitoring, 'warning_thresholds', {}))} 个参数",
            f"   应急措施: {len(getattr(monitoring, 'emergency_measures', []))} 项",
            ""
        ])

    # 元数据信息
    if result.metadata:
        data_sources = result.metadata.get('data_sources', {})
        total_records = data_sources.get('total_records', 'N/A')
        multi_image_count = result.metadata.get('multi_image_count', 'N/A')
        similar_cases_count = result.metadata.get('similar_cases_count', 'N/A')

        output_lines.extend([
            "📋 分析元数据:",
            f"   数据源记录数: {total_records}",
            f"   处理的图像数: {multi_image_count}",
            f"   相似案例数: {similar_cases_count}",
            ""
        ])

    # 警告信息
    if result.warnings:
        output_lines.append("⚠️  警告信息:")
        for warning in result.warnings:
            output_lines.append(f"   • {warning}")
        output_lines.append("")

    output_lines.append("=" * 80)
    return "\n".join(output_lines)

    output_lines.append("=" * 80)
    return "\n".join(output_lines)


# ===================== 监控点格式转换 =====================

def convert_to_monitoring_points_format(result: EnhancedDecisionAnalysisResult,
                                        room_id: str) -> Dict[str, Any]:
    """
    将增强决策输出转换为监控点配置格式，并动态填充old字段。

    Args:
        result: 增强决策分析结果
        room_id: 库房编号

    Returns:
        符合monitoring_points_config.json格式的字典，包含动态填充的old字段
    """
    logger.debug(log_message(
        "OUTPUT_FORMAT_CONVERSION",
        "开始转换增强决策输出为监控点配置格式",
        room_id=room_id
    ))

    if not result.enhanced_decision_output:
        return _create_empty_monitoring_config(room_id)

    enhanced_output = result.enhanced_decision_output
    
    # Always convert from enhanced decision output to monitoring points format
    logger.info(log_message(
        "OUTPUT_FORMAT_CONVERSION", 
        "从增强决策输出转换为监控点配置格式",
        room_id=room_id
    ))
    
    return _convert_from_enhanced_decision_output(enhanced_output, room_id)


def _create_empty_monitoring_config(room_id: str) -> Dict[str, Any]:
    """创建空的监控点配置"""
    return {
        "room_id": room_id,
        "devices": {
            "air_cooler": [],
            "fresh_air_fan": [],
            "humidifier": [],
            "grow_light": []
        },
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "enhanced_decision_analysis",
            "total_points": 0
        }
    }


def _convert_from_enhanced_decision_output(enhanced_output: Any, room_id: str) -> Dict[str, Any]:
    """
    从增强决策输出转换为监控点配置格式
    
    Args:
        enhanced_output: 增强决策输出对象
        room_id: 库房编号
        
    Returns:
        监控点配置格式的字典
    """
    config = _create_empty_monitoring_config(room_id)
    
    try:
        # Extract device recommendations from enhanced output
        device_recs = None
        
        # Handle different types of enhanced output
        if hasattr(enhanced_output, 'device_recommendations'):
            device_recs = enhanced_output.device_recommendations
        elif isinstance(enhanced_output, dict) and "device_recommendations" in enhanced_output:
            device_recs = enhanced_output["device_recommendations"]
        
        if not device_recs:
            logger.warning(log_message(
                "OUTPUT_FORMAT_CONVERSION",
                "未找到设备推荐数据，返回空配置",
                room_id=room_id
            ))
            return config
        
        # Load template configuration
        template_config = _load_monitoring_points_template(room_id)
        
        # Update room_id and device names
        template_config["room_id"] = room_id
        template_config = _update_device_names_for_room(template_config, room_id)
        
        # Convert device recommendations to monitoring points
        config = template_config.copy()
        config = _update_monitoring_points_from_enhanced_recs(config, device_recs)
        
        # Try to populate with real-time data for 'old' fields
        try:
            config = _populate_old_fields_from_realtime_data(config, room_id)
        except Exception as e:
            logger.warning(log_message(
                "DATA_FETCH_ERROR",
                "实时数据填充失败，使用推荐值作为当前值",
                error=str(e)
            ))
            # If real-time data fails, use recommended values as current values
            config = _use_recommended_as_current_values(config)
        
        # 重新验证和更新change字段（在实时数据填充后）
        config = _validate_and_update_change_flags(config)
        
        # Calculate total points
        total_points = sum(
            len(device.get("point_list", []))
            for device_list in config["devices"].values()
            for device in device_list
            if isinstance(device_list, list)
        )
        
        config["metadata"] = {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "enhanced_decision_analysis",
            "total_points": total_points
        }
        
    except Exception as e:
        logger.error(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "从增强决策输出转换失败",
            error=str(e)
        ))
    
    return config


def _use_recommended_as_current_values(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    当实时数据不可用时，使用推荐值作为当前值的后备方案
    
    Args:
        config: 监控点配置
        
    Returns:
        更新后的配置
    """
    try:
        devices = config.get("devices", {})
        
        for device_type, device_list in devices.items():
            for device in device_list:
                for point in device.get("point_list", []):
                    # 如果old字段为0或None，且new字段有值，则使用new值作为old值
                    old_value = point.get("old")
                    new_value = point.get("new")
                    
                    # 如果new字段也是0，说明LLM没有提供有效的推荐值，使用合理的默认值
                    if new_value == 0 or new_value is None:
                        realistic_new_value = _get_realistic_default_value(point.get("point_alias"), point.get("change_type"))
                        point["new"] = realistic_new_value
                        
                        # 如果old和new值不同，设置为需要调整
                        if old_value != realistic_new_value and old_value != 0:
                            point["change"] = True
                            point["level"] = "medium"
                        else:
                            point["change"] = False
                            point["level"] = "low"
                    
                    if (old_value is None or old_value == 0) and new_value is not None and new_value != 0:
                        # 对于需要调整的情况，使用一个合理的当前值
                        if point.get("change", False):
                            # 如果需要调整，假设当前值与推荐值有一定差异
                            point["old"] = _generate_reasonable_current_value(new_value, point.get("change_type"))
                        else:
                            # 如果不需要调整，当前值等于推荐值
                            point["old"] = new_value
                    elif old_value is None or old_value == 0:
                        # 使用更合理的默认值
                        point["old"] = _get_realistic_default_value(point.get("point_alias"), point.get("change_type"))
        
        logger.info(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "使用推荐值作为当前值的后备方案",
            room_id=config.get("room_id")
        ))
        
        return config
        
    except Exception as e:
        logger.error(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "使用推荐值作为当前值失败",
            error=str(e)
        ))
        return config


def _get_realistic_default_value(point_alias: str, change_type: str) -> Union[int, float]:
    """
    根据参数别名和类型获取更合理的默认值
    
    Args:
        point_alias: 参数别名
        change_type: 参数类型
        
    Returns:
        合理的默认值
    """
    # 基于参数别名的合理默认值
    realistic_defaults = {
        # 冷风机参数
        "temp_set": 15.0,
        "temp_diffset": 2.0,
        "cyc_on_time": 10,
        "cyc_off_time": 10,
        "on_off": 1,
        "cyc_on_off": 1,
        "air_on_off": 0,
        "hum_on_off": 0,
        
        # 新风机参数
        "mode": 1,
        "control": 1,
        "co2_on": 1000,
        "co2_off": 800,
        "on": 10,
        "off": 10,
        
        # 加湿器参数
        "model": 1,
        
        # 补光灯参数
        "on_mset": 60,
        "off_mset": 60,
        "on_off1": 1,
        "on_off2": 1,
        "on_off3": 0,
        "on_off4": 0,
        "choose1": 0,
        "choose2": 0,
        "choose3": 0,
        "choose4": 0
    }
    
    # 加湿器的on/off参数需要特殊处理
    if point_alias == "on" and "humidifier" in str(change_type).lower():
        return 85
    elif point_alias == "off" and "humidifier" in str(change_type).lower():
        return 90
    
    # 首先尝试使用参数别名的默认值
    if point_alias in realistic_defaults:
        return realistic_defaults[point_alias]
    
    # 然后根据类型使用默认值
    type_defaults = {
        "analog_value": 0.0,
        "digital_on_off": 0,
        "enum_state": 0
    }
    return type_defaults.get(change_type, 0)


def _generate_reasonable_current_value(recommended_value: Union[int, float], change_type: str) -> Union[int, float]:
    """
    基于推荐值生成合理的当前值（用于演示调整需求）
    
    Args:
        recommended_value: 推荐值
        change_type: 参数类型
        
    Returns:
        生成的当前值
    """
    try:
        if change_type == "analog_value":
            # 对于模拟值，生成一个与推荐值有小幅差异的当前值
            if recommended_value == 0:
                return 0
            # 生成5-15%的差异
            variation = recommended_value * 0.1
            return max(0, recommended_value - variation)
        elif change_type in ["digital_on_off", "enum_state"]:
            # 对于数字值，如果推荐值不为0，则当前值可能为0（表示需要开启）
            if recommended_value != 0:
                return 0
            else:
                return recommended_value
        else:
            return recommended_value
            
    except Exception:
        return recommended_value


def _get_default_value_by_type(change_type: str) -> Union[int, float]:
    """
    根据参数类型获取默认值
    
    Args:
        change_type: 参数类型
        
    Returns:
        默认值
    """
    defaults = {
        "analog_value": 0.0,
        "digital_on_off": 0,
        "enum_state": 0
    }
    return defaults.get(change_type, 0)


def _update_monitoring_points_from_enhanced_recs(config: Dict[str, Any], device_recs: Any) -> Dict[str, Any]:
    """从增强设备推荐更新监控点"""
    devices = config.get("devices", {})
    
    # Device type mappings
    device_types = ["air_cooler", "fresh_air_fan", "humidifier", "grow_light"]
    
    for device_type in device_types:
        device_list = devices.get(device_type, [])
        
        # Get device recommendations
        device_rec = None
        if hasattr(device_recs, device_type):
            device_rec = getattr(device_recs, device_type)
        elif isinstance(device_recs, dict) and device_type in device_recs:
            device_rec = device_recs[device_type]
        
        if not device_rec or not device_list:
            continue
        
        # Update each device in the list
        for device in device_list:
            for point in device.get("point_list", []):
                point_alias = point.get("point_alias")
                
                # Find corresponding parameter adjustment
                param_adj = _find_parameter_adjustment(device_rec, point_alias)
                
                if param_adj:
                    # Extract values from parameter adjustment
                    current_val = _extract_adjustment_value(param_adj, "current_value", 0)
                    recommended_val = _extract_adjustment_value(param_adj, "recommended_value", current_val)
                    action = _extract_adjustment_value(param_adj, "action", "maintain")
                    priority = _extract_adjustment_value(param_adj, "priority", "medium")
                    
                    # Update point with extracted values
                    point["old"] = current_val  # This will be overridden by real-time data if available
                    point["new"] = recommended_val
                    
                    # 核心逻辑：根据old和new值的差异以及action来设置change标志
                    point["change"] = _should_change_parameter(
                        old_value=current_val,
                        new_value=recommended_val,
                        action=action,
                        change_type=point.get("change_type"),
                        threshold=point.get("threshold")
                    )
                    
                    # 根据change状态和priority设置level
                    point["level"] = _determine_priority_level(
                        change_required=point["change"],
                        priority=priority,
                        point_alias=point_alias,
                        device_type=device_type
                    )
                    
                    logger.debug(log_message(
                        "OUTPUT_FORMAT_CONVERSION",
                        f"更新监控点 {device_type}.{point_alias}",
                        current=current_val,
                        recommended=recommended_val,
                        action=action,
                        change=point["change"],
                        level=point["level"]
                    ))
                else:
                    # Use defaults if no parameter adjustment found
                    point["old"] = 0
                    point["new"] = 0
                    point["change"] = False
                    point["level"] = "low"
                    
                    logger.debug(log_message(
                        "OUTPUT_FORMAT_CONVERSION",
                        f"未找到参数调整，使用默认值 {device_type}.{point_alias}"
                    ))
    
    return config


def _should_change_parameter(old_value: Any, new_value: Any, action: str, 
                           change_type: str, threshold: float = None) -> bool:
    """
    判断是否需要调整参数
    
    Args:
        old_value: 当前值
        new_value: 推荐值
        action: 动作类型 ("maintain", "adjust", "monitor")
        change_type: 参数类型
        threshold: 阈值（用于模拟值的微小差异判断）
        
    Returns:
        是否需要调整参数
    """
    # 1. 如果action明确指示需要调整
    if action == "adjust":
        return True
    
    # 2. 如果action是maintain，但值不同，仍需要调整
    if action == "maintain":
        # 对于数字值，检查是否有实际差异
        if change_type == "analog_value" and threshold is not None:
            try:
                diff = abs(float(new_value) - float(old_value))
                return diff >= threshold
            except (ValueError, TypeError):
                return old_value != new_value
        else:
            # 对于数字开关和枚举值，直接比较
            return old_value != new_value
    
    # 3. 如果action是monitor，通常不需要立即调整
    if action == "monitor":
        # 但如果值差异很大，仍可能需要调整
        if change_type == "analog_value" and threshold is not None:
            try:
                diff = abs(float(new_value) - float(old_value))
                return diff >= (threshold * 2)  # 使用更大的阈值
            except (ValueError, TypeError):
                return False
        return False
    
    # 4. 默认情况：比较值是否不同
    return old_value != new_value


def _determine_priority_level(change_required: bool, priority: str, 
                            point_alias: str, device_type: str) -> str:
    """
    根据调整需求和优先级确定level等级
    
    Args:
        change_required: 是否需要调整
        priority: 原始优先级
        point_alias: 参数别名
        device_type: 设备类型
        
    Returns:
        优先级等级 ("low", "medium", "high")
    """
    # 如果不需要调整，优先级通常较低
    if not change_required:
        return "low"
    
    # 根据原始优先级映射
    priority_mapping = {
        "critical": "high",
        "high": "high",
        "medium": "medium", 
        "low": "low"
    }
    
    base_level = priority_mapping.get(priority, "medium")
    
    # 根据参数类型和设备类型调整优先级
    critical_params = {
        "air_cooler": ["temp_set", "on_off"],  # 温度设定和开关状态最重要
        "fresh_air_fan": ["mode", "co2_on", "co2_off"],  # 新风模式和CO2阈值重要
        "humidifier": ["mode", "on", "off"],  # 加湿模式和湿度阈值重要
        "grow_light": ["model", "on_off1", "on_off2"]  # 补光模式和主要光源重要
    }
    
    # 如果是关键参数，提升优先级
    if point_alias in critical_params.get(device_type, []):
        if base_level == "low":
            return "medium"
        elif base_level == "medium":
            return "high"
    
    return base_level


def _find_parameter_adjustment(device_rec: Any, point_alias: str) -> Any:
    """在设备推荐中查找参数调整"""
    if not device_rec:
        return None
    
    # Try direct attribute access
    if hasattr(device_rec, point_alias):
        return getattr(device_rec, point_alias)
    
    # Try dictionary access
    if isinstance(device_rec, dict) and point_alias in device_rec:
        return device_rec[point_alias]
    
    # Try common alias mappings
    alias_mappings = {
        "temp_set": "tem_set",
        "temp_diffset": "tem_diff_set", 
        "air_on_off": "ar_on_off",
        "mode": "model",
        "on_off1": "on_off_1",
        "on_off2": "on_off_2", 
        "on_off3": "on_off_3",
        "on_off4": "on_off_4",
        "choose1": "choose_1",
        "choose2": "choose_2",
        "choose3": "choose_3", 
        "choose4": "choose_4"
    }
    
    mapped_alias = alias_mappings.get(point_alias)
    if mapped_alias:
        if hasattr(device_rec, mapped_alias):
            return getattr(device_rec, mapped_alias)
        elif isinstance(device_rec, dict) and mapped_alias in device_rec:
            return device_rec[mapped_alias]
    
    return None


def _extract_adjustment_value(param_adj: Any, field_name: str, default_value: Any) -> Any:
    """从参数调整中提取值"""
    if not param_adj:
        return default_value
    
    # Try attribute access
    if hasattr(param_adj, field_name):
        return getattr(param_adj, field_name)
    
    # Try dictionary access
    if isinstance(param_adj, dict) and field_name in param_adj:
        return param_adj[field_name]
    
    return default_value


def _update_device_names_for_room(template: Dict, room_id: str) -> Dict:
    """Update device names and aliases for specific room"""
    devices = template.get("devices", {})
    
    for device_type, device_list in devices.items():
        for device in device_list:
            # Update device names for room 607
            if "device_name" in device:
                device_name = device["device_name"]
                if "Q1MD" in device_name:
                    # For room 607, we might need different naming convention
                    # Keep the original pattern but update room reference
                    device["device_name"] = device_name.replace("TD1_Q1MD", f"TD1_Q{room_id}MD")
            
            if "device_alias" in device:
                device_alias = device["device_alias"]
                if "_611" in device_alias:
                    device["device_alias"] = device_alias.replace("_611", f"_{room_id}")
    
    return template


def _convert_from_device_recommendations(enhanced_output: Dict[str, Any], 
                                         room_id: str) -> Dict[str, Any]:
    """
    从device_recommendations转换为监控点配置格式
    
    Args:
        enhanced_output: LLM的增强决策输出
        room_id: 库房编号
        
    Returns:
        监控点配置格式的字典
    """
    config = _create_empty_monitoring_config(room_id)
    
    if not isinstance(enhanced_output, dict) or "device_recommendations" not in enhanced_output:
        return config
    
    device_recs = enhanced_output["device_recommendations"]
    
    try:
        # 加载监控点配置模板
        template_config = _load_monitoring_points_template(room_id)
        
        # 转换各设备类型
        if "air_cooler" in device_recs:
            config["devices"]["air_cooler"] = _convert_air_cooler_recommendations(
                device_recs["air_cooler"], template_config.get("devices", {}).get("air_cooler", [])
            )
        
        if "fresh_air_fan" in device_recs:
            config["devices"]["fresh_air_fan"] = _convert_fresh_air_fan_recommendations(
                device_recs["fresh_air_fan"], template_config.get("devices", {}).get("fresh_air_fan", [])
            )
        
        if "humidifier" in device_recs:
            config["devices"]["humidifier"] = _convert_humidifier_recommendations(
                device_recs["humidifier"], template_config.get("devices", {}).get("humidifier", [])
            )
        
        if "grow_light" in device_recs:
            config["devices"]["grow_light"] = _convert_grow_light_recommendations(
                device_recs["grow_light"], template_config.get("devices", {}).get("grow_light", [])
            )
        
        # 计算总点数
        total_points = sum(
            len(device_list) * len(device_list[0].get("point_list", []))
            for device_list in config["devices"].values()
            if device_list and isinstance(device_list, list) and device_list
        )
        config["metadata"]["total_points"] = total_points
        
    except Exception as e:
        logger.error(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "从device_recommendations转换失败",
            error=str(e)
        ))
    
    return config


def _load_monitoring_points_template(room_id: str) -> Dict[str, Any]:
    """加载监控点配置模板"""
    try:
        template_path = Path(__file__).parent.parent.parent / "src" / "configs" / "monitoring_points_config.json"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = json.load(f)
        return template
    except Exception as e:
        logger.warning(log_message(
            "TEMPLATE_LOAD_ERROR",
            "加载监控点配置模板失败，使用默认结构",
            error=str(e)
        ))
        return {}


def _convert_air_cooler_recommendations(air_cooler_rec: Dict[str, Any], 
                                        template_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换冷风机推荐为监控点格式"""
    if not template_devices:
        return []
    
    result = []
    for template_device in template_devices:
        device = {
            "device_name": template_device.get("device_name", ""),
            "device_alias": template_device.get("device_alias", ""),
            "point_list": []
        }
        
        for template_point in template_device.get("point_list", []):
            point_alias = template_point.get("point_alias", "")
            
            # 从推荐中查找对应的参数
            rec_value = _find_recommendation_value(air_cooler_rec, point_alias)
            
            point = template_point.copy()
            if rec_value:
                point.update({
                    "change": rec_value.get("action") == "adjust",
                    "old": rec_value.get("current_value", 0),
                    "new": rec_value.get("recommended_value", 0),
                    "level": _map_priority_to_level(rec_value.get("priority", "medium"))
                })
            else:
                # 使用默认值
                point.update({
                    "change": False,
                    "old": 0,
                    "new": 0,
                    "level": "medium"
                })
            
            device["point_list"].append(point)
        
        result.append(device)
    
    return result


def _convert_fresh_air_fan_recommendations(fresh_air_rec: Dict[str, Any], 
                                           template_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换新风机推荐为监控点格式"""
    return _convert_device_recommendations_generic(fresh_air_rec, template_devices)


def _convert_humidifier_recommendations(humidifier_rec: Dict[str, Any], 
                                        template_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换加湿器推荐为监控点格式"""
    return _convert_device_recommendations_generic(humidifier_rec, template_devices)


def _convert_grow_light_recommendations(grow_light_rec: Dict[str, Any], 
                                        template_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换补光灯推荐为监控点格式"""
    return _convert_device_recommendations_generic(grow_light_rec, template_devices)


def _convert_device_recommendations_generic(device_rec: Dict[str, Any], 
                                            template_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """通用设备推荐转换函数"""
    if not template_devices:
        return []
    
    result = []
    for template_device in template_devices:
        device = {
            "device_name": template_device.get("device_name", ""),
            "device_alias": template_device.get("device_alias", ""),
            "point_list": []
        }
        
        for template_point in template_device.get("point_list", []):
            point_alias = template_point.get("point_alias", "")
            
            # 从推荐中查找对应的参数
            rec_value = _find_recommendation_value(device_rec, point_alias)
            
            point = template_point.copy()
            if rec_value:
                point.update({
                    "change": rec_value.get("action") == "adjust",
                    "old": rec_value.get("current_value", 0),
                    "new": rec_value.get("recommended_value", 0),
                    "level": _map_priority_to_level(rec_value.get("priority", "medium"))
                })
            else:
                # 使用默认值
                point.update({
                    "change": False,
                    "old": 0,
                    "new": 0,
                    "level": "medium"
                })
            
            device["point_list"].append(point)
        
        result.append(device)
    
    return result


def _find_recommendation_value(device_rec: Dict[str, Any], point_alias: str) -> Optional[Dict[str, Any]]:
    """在设备推荐中查找指定参数的值"""
    # 尝试直接匹配
    if point_alias in device_rec:
        return device_rec[point_alias]
    
    # 尝试映射常见的别名
    alias_mapping = {
        "on_off": "on_off",
        "temp_set": "tem_set", 
        "temp_diffset": "tem_diff_set",
        "cyc_on_time": "cyc_on_time",
        "cyc_off_time": "cyc_off_time",
        "air_on_off": "ar_on_off",
        "hum_on_off": "hum_on_off",
        "cyc_on_off": "cyc_on_off",
        "mode": "model",
        "control": "control",
        "co2_on": "co2_on",
        "co2_off": "co2_off",
        "on": "on",
        "off": "off"
    }
    
    mapped_alias = alias_mapping.get(point_alias)
    if mapped_alias and mapped_alias in device_rec:
        return device_rec[mapped_alias]
    
    return None


def _map_priority_to_level(priority: str) -> str:
    """将优先级映射为level"""
    priority_mapping = {
        "critical": "high",
        "high": "high", 
        "medium": "medium",
        "low": "low"
    }
    return priority_mapping.get(priority, "medium")


def _populate_old_fields_from_realtime_data(config: Dict[str, Any], room_id: str) -> Dict[str, Any]:
    """从实时数据填充old字段"""
    try:
        from utils.realtime_data_populator import populate_monitoring_points_old_fields

        populated_config, population_stats = populate_monitoring_points_old_fields(
            config, room_id
        )

        # 记录填充统计信息
        success_count = population_stats.get("successful_matches", 0)
        total_count = population_stats.get("total_points", 0)
        success_rate = population_stats.get("success_rate", 0)
        
        logger.info(log_message(
            "DATA_FETCH_SUCCESS",
            "实时数据填充完成",
            room_id=room_id,
            success_count=success_count,
            total_count=total_count,
            success_rate=success_rate
        ))

        # 如果实时数据获取成功率很低，使用备用策略
        if success_rate < 10:  # 成功率低于10%
            logger.warning(log_message(
                "DATA_FETCH_WARNING",
                "实时数据获取成功率过低，使用备用策略",
                room_id=room_id,
                success_rate=success_rate
            ))
            
            # 尝试使用设备配置中的默认值或历史平均值
            populated_config = _populate_with_fallback_values(populated_config, room_id)

        return populated_config

    except ImportError as e:
        logger.warning(log_message(
            "DEPENDENCY_IMPORT_ERROR",
            "无法导入实时数据填充器，使用备用策略",
            error=str(e)
        ))
        return _populate_with_fallback_values(config, room_id)
    except Exception as e:
        logger.warning(log_message(
            "DATA_FETCH_ERROR",
            "实时数据填充失败，使用备用策略",
            error=str(e)
        ))
        return _populate_with_fallback_values(config, room_id)


def _populate_with_fallback_values(config: Dict[str, Any], room_id: str) -> Dict[str, Any]:
    """
    使用备用策略填充old字段
    
    Args:
        config: 监控点配置
        room_id: 库房编号
        
    Returns:
        填充后的配置
    """
    try:
        # 定义各类型参数的典型值（基于实际生产环境的合理值）
        typical_values = {
            "air_cooler": {
                "temp_set": 15.0,      # 冷风机温度设定，通常在12-18℃
                "temp_diffset": 2.0,   # 温差设定，通常1.5-3℃
                "cyc_on_time": 10,     # 循环开启时间，通常5-15分钟
                "cyc_off_time": 10,    # 循环关闭时间，通常5-15分钟
                "on_off": 1,           # 冷风机通常是开启状态
                "cyc_on_off": 1,       # 循环模式通常开启
                "air_on_off": 0,       # 新风联动根据需要
                "hum_on_off": 0        # 加湿联动根据需要
            },
            "fresh_air_fan": {
                "mode": 1,             # 自动模式
                "control": 1,          # CO2控制
                "co2_on": 1000,        # CO2启动阈值，通常800-1200ppm
                "co2_off": 800,        # CO2停止阈值，通常600-1000ppm
                "on": 10,              # 时控开启时间，通常5-15分钟
                "off": 10              # 时控关闭时间，通常5-15分钟
            },
            "humidifier": {
                "mode": 1,             # 自动模式
                "on": 85,              # 加湿开启阈值，通常80-90%
                "off": 90              # 加湿停止阈值，通常85-95%
            },
            "grow_light": {
                "model": 1,            # 自动模式
                "on_mset": 60,         # 开启时长，通常30-120分钟
                "off_mset": 60,        # 关闭时长，通常30-120分钟
                "on_off1": 1,          # 1号补光灯通常开启
                "on_off2": 1,          # 2号补光灯通常开启
                "on_off3": 0,          # 3号补光灯根据需要
                "on_off4": 0,          # 4号补光灯根据需要
                "choose1": 0,          # 1号光源选择白光
                "choose2": 0,          # 2号光源选择白光
                "choose3": 0,          # 3号光源选择白光
                "choose4": 0           # 4号光源选择白光
            }
        }
        
        devices = config.get("devices", {})
        filled_count = 0
        
        for device_type, device_list in devices.items():
            device_typical_values = typical_values.get(device_type, {})
            
            for device in device_list:
                for point in device.get("point_list", []):
                    point_alias = point.get("point_alias")
                    
                    # 如果old字段为空或为0，使用典型值
                    if point.get("old") is None or point.get("old") == 0:
                        typical_value = device_typical_values.get(point_alias)
                        
                        if typical_value is not None:
                            point["old"] = typical_value
                            filled_count += 1
                            
                            logger.debug(log_message(
                                "DATA_FETCH_FALLBACK",
                                f"使用典型值填充 {device_type}.{point_alias}",
                                value=typical_value
                            ))
                        else:
                            # 使用类型默认值
                            default_value = _get_default_value_by_type(point.get("change_type"))
                            point["old"] = default_value
                            filled_count += 1
        
        logger.info(log_message(
            "DATA_FETCH_FALLBACK",
            "使用备用策略填充完成",
            room_id=room_id,
            filled_count=filled_count
        ))
        
        return config
        
    except Exception as e:
        logger.error(log_message(
            "DATA_FETCH_FALLBACK",
            "备用策略填充失败",
            error=str(e)
        ))
        return config


def _validate_and_update_change_flags(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证并更新监控点配置中的change字段和level字段
    
    在实时数据填充完成后，重新检查old和new值的差异，
    确保change字段正确反映是否需要参数调整。
    
    Args:
        config: 监控点配置
        
    Returns:
        更新后的配置
    """
    try:
        devices = config.get("devices", {})
        updated_count = 0
        
        for device_type, device_list in devices.items():
            for device in device_list:
                for point in device.get("point_list", []):
                    old_value = point.get("old")
                    new_value = point.get("new")
                    change_type = point.get("change_type")
                    threshold = point.get("threshold")
                    point_alias = point.get("point_alias")
                    
                    # 原始change状态
                    original_change = point.get("change", False)
                    
                    # 重新计算是否需要调整
                    should_change = _should_change_parameter(
                        old_value=old_value,
                        new_value=new_value,
                        action="adjust" if original_change else "maintain",
                        change_type=change_type,
                        threshold=threshold
                    )
                    
                    # 更新change字段
                    if should_change != original_change:
                        point["change"] = should_change
                        updated_count += 1
                        
                        logger.debug(log_message(
                            "OUTPUT_FORMAT_CONVERSION",
                            f"更新change标志 {device_type}.{point_alias}",
                            old=old_value,
                            new=new_value,
                            original_change=original_change,
                            updated_change=should_change
                        ))
                    
                    # 重新计算优先级
                    original_level = point.get("level", "medium")
                    updated_level = _determine_priority_level(
                        change_required=should_change,
                        priority=original_level,
                        point_alias=point_alias,
                        device_type=device_type
                    )
                    
                    if updated_level != original_level:
                        point["level"] = updated_level
                        logger.debug(log_message(
                            "OUTPUT_FORMAT_CONVERSION",
                            f"更新优先级 {device_type}.{point_alias}",
                            original_level=original_level,
                            updated_level=updated_level
                        ))
        
        logger.info(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "验证并更新change字段完成",
            room_id=config.get("room_id"),
            updated_count=updated_count
        ))
        
        return config
        
    except Exception as e:
        logger.error(log_message(
            "OUTPUT_FORMAT_CONVERSION",
            "验证change字段时发生错误",
            error=str(e)
        ))
        return config




# ===================== 文件保存 =====================

def save_enhanced_json_output(result: EnhancedDecisionAnalysisResult,
                              output_path: Path,
                              output_format: str = "both") -> None:
    """
    保存增强决策输出到JSON文件。

    Args:
        result: 增强决策分析结果
        output_path: 输出文件路径
        output_format: 输出格式 ("enhanced", "monitoring", "both")

    Raises:
        Exception: 文件保存失败
    """
    logger.debug(log_message(
        "FILE_SAVE_START",
        "开始保存增强决策输出到文件",
        path=str(output_path),
        format=output_format
    ))

    if not result.enhanced_decision_output:
        raise ValueError("没有可保存的增强决策输出数据")

    # 准备输出数据
    output_data = _prepare_output_data(result, output_format)

    # 写入文件，使用美化格式
    try:
        logger.debug(log_message(
            "JSON_SERIALIZATION",
            "开始写入JSON数据到文件",
            path=str(output_path)
        ))

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2,
                      default=str)

        file_size = output_path.stat().st_size if output_path.exists() else 0
        logger.info(log_message(
            "FILE_SAVE_SUCCESS",
            "增强决策结果保存成功",
            path=str(output_path),
            format=output_format,
            size=f"{file_size}字节"
        ))

        # 如果格式为both，还要保存监控点配置到单独文件
        if output_format == "both" and "monitoring_points" in output_data:
            monitoring_points_path = (
                output_path.parent /
                f"monitoring_points_{output_path.stem}.json"
            )
            try:
                logger.debug(log_message(
                    "FILE_SAVE_START",
                    "保存监控点配置到单独文件",
                    path=str(monitoring_points_path)
                ))

                with open(monitoring_points_path, 'w', encoding='utf-8') as f:
                    json.dump(output_data["monitoring_points"], f,
                              ensure_ascii=False, indent=2, default=str)

                mp_file_size = (
                    monitoring_points_path.stat().st_size
                    if monitoring_points_path.exists() else 0
                )
                logger.info(log_message(
                    "FILE_SAVE_SUCCESS",
                    "监控点配置文件保存成功",
                    path=str(monitoring_points_path),
                    size=f"{mp_file_size}字节"
                ))
            except Exception as e:
                logger.warning(log_message(
                    "FILE_SAVE_ERROR",
                    "监控点配置文件保存失败",
                    error=str(e)
                ))

    except Exception as e:
        error_msg = (
            f"保存增强决策结果到 {output_path} 失败: {str(e)}"
        )
        logger.error(log_message("FILE_SAVE_ERROR", error_msg))
        raise


def _convert_enhanced_output_to_json(enhanced_output: Any) -> Dict[str, Any]:
    """
    Convert enhanced decision output to clean JSON format, removing string representations
    and image quality scores.
    
    Args:
        enhanced_output: Enhanced decision output object
        
    Returns:
        Clean JSON dictionary
    """
    if not enhanced_output:
        return {}
    
    # If it's already a dict, return it
    if isinstance(enhanced_output, dict):
        return enhanced_output
    
    # Convert object to dict, handling dataclass objects
    try:
        import dataclasses
        if dataclasses.is_dataclass(enhanced_output):
            result = dataclasses.asdict(enhanced_output)
        else:
            # Try to get attributes
            result = {}
            for attr in dir(enhanced_output):
                if not attr.startswith('_'):
                    try:
                        value = getattr(enhanced_output, attr)
                        if not callable(value):
                            if dataclasses.is_dataclass(value):
                                result[attr] = dataclasses.asdict(value)
                            elif hasattr(value, '__dict__'):
                                result[attr] = value.__dict__
                            else:
                                result[attr] = value
                    except:
                        continue
        
        # Clean up the result - remove image quality scores and other unnecessary data
        if 'multi_image_analysis' in result:
            multi_img = result['multi_image_analysis']
            if isinstance(multi_img, dict):
                # Keep only essential multi-image analysis data
                cleaned_multi_img = {
                    'total_images_analyzed': multi_img.get('total_images_analyzed', 0),
                    'confidence_score': multi_img.get('confidence_score', 0.0),
                    'view_consistency': multi_img.get('view_consistency', 'unknown'),
                    'aggregation_method': multi_img.get('aggregation_method', 'single_image')
                }
                # Remove image quality scores and detailed observations
                result['multi_image_analysis'] = cleaned_multi_img
        
        return result
        
    except Exception as e:
        logger.warning(f"Failed to convert enhanced output to JSON: {e}")
        return {"error": "Failed to convert enhanced output", "raw_type": str(type(enhanced_output))}


def _prepare_output_data(result: EnhancedDecisionAnalysisResult,
                         output_format: str) -> Dict[str, Any]:
    """
    准备输出数据。

    Args:
        result: 增强决策分析结果
        output_format: 输出格式

    Returns:
        准备好的输出数据
    """
    if output_format == "enhanced":
        # Convert enhanced decision output to clean JSON format
        return _convert_enhanced_output_to_json(result.enhanced_decision_output)

    elif output_format == "monitoring":
        return convert_to_monitoring_points_format(result, result.room_id)

    elif output_format == "both":
        monitoring_points = convert_to_monitoring_points_format(
            result, result.room_id
        )
        return {
            "enhanced_decision": _convert_enhanced_output_to_json(result.enhanced_decision_output),
            "monitoring_points": monitoring_points,
            "metadata": {
                "room_id": result.room_id,
                "analysis_datetime": (
                    result.analysis_datetime.isoformat()
                    if result.analysis_datetime else None
                ),
                "processing_time": result.processing_time,
                "generated_at": datetime.now().isoformat()
            }
        }

    else:
        raise ValueError(f"不支持的输出格式: {output_format}")


# ===================== 文件名生成 =====================

def generate_enhanced_output_filename(room_id: str,
                                      analysis_datetime: datetime,
                                      output_dir: Optional[Path] = None
                                      ) -> Path:
    """
    生成增强决策输出文件名。

    Args:
        room_id: 库房编号
        analysis_datetime: 分析时间
        output_dir: 输出目录，默认为项目根目录下的output文件夹

    Returns:
        完整的输出文件路径

    Raises:
        Exception: 文件名生成失败
    """
    logger.debug(log_message(
        "OUTPUT_PATH_GENERATION",
        "开始生成输出文件名",
        room_id=room_id,
        datetime=analysis_datetime
    ))

    try:
        # 获取项目根目录
        base_dir = Path(__file__).parent.parent.parent

        if output_dir is None:
            output_dir = base_dir / "output"
            logger.debug(log_message(
                "OUTPUT_PATH_GENERATION",
                "使用默认输出目录",
                path=str(output_dir)
            ))
        else:
            logger.debug(log_message(
                "OUTPUT_PATH_GENERATION",
                "使用自定义输出目录",
                path=str(output_dir)
            ))

        # 确保输出目录存在
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(log_message(
            "OUTPUT_PATH_GENERATION",
            "输出目录已确保存在",
            path=str(output_dir)
        ))

        # 生成文件名
        timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
        filename = f"enhanced_decision_analysis_{room_id}_{timestamp}.json"
        full_path = output_dir / filename

        logger.info(log_message(
            "OUTPUT_PATH_GENERATION",
            "输出文件名生成成功",
            room_id=room_id,
            filename=filename,
            path=str(full_path)
        ))

        return full_path

    except Exception as e:
        error_msg = f"生成输出文件名失败: {str(e)}"
        logger.error(log_message("OUTPUT_PATH_GENERATION", error_msg))
        raise


# ===================== 核心执行函数 =====================

def execute_enhanced_decision_analysis(
    room_id: str,
    analysis_datetime: Optional[datetime] = None,
    output_file: Optional[Union[str, Path]] = None,
    verbose: bool = False,
    output_format: str = "monitoring"
) -> EnhancedDecisionAnalysisResult:
    """
    执行增强决策分析的核心函数。

    Args:
        room_id: 库房编号
        analysis_datetime: 分析时间，默认为当前时间
        output_file: 输出文件路径，默认自动生成
        verbose: 是否启用详细输出
        output_format: 输出格式 ("enhanced", "monitoring", "both")

    Returns:
        增强决策分析结果

    Raises:
        ValueError: 参数验证失败
        ImportError: 依赖导入失败
        Exception: 其他执行错误
    """
    result = EnhancedDecisionAnalysisResult()
    start_time = time.time()

    logger.info(log_message(
        "BUSINESS_FLOW_START",
        "开始执行增强决策分析",
        room_id=room_id
    ))
    logger.debug(log_message(
        "PARAM_VALIDATION_START",
        "输入参数验证",
        room_id=room_id,
        analysis_datetime=analysis_datetime,
        output_file=output_file,
        verbose=verbose,
        output_format=output_format
    ))

    # 记录初始内存使用情况
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    logger.info(log_message(
        "PERFORMANCE_MEMORY_USAGE",
        "初始内存使用情况",
        rss_mb=f"{memory_info.rss / 1024 / 1024:.2f}",
        vms_mb=f"{memory_info.vms / 1024 / 1024:.2f}"
    ))

    try:
        # 设置默认分析时间
        if analysis_datetime is None:
            analysis_datetime = datetime.now()
            logger.debug(log_message(
                "PARAM_VALIDATION_SUCCESS",
                "使用当前分析时间",
                analysis_time=analysis_datetime
            ))

        result.room_id = room_id
        result.analysis_datetime = analysis_datetime

        # 验证房间ID
        logger.info(log_message(
            "ROOM_ID_VALIDATION",
            "开始验证库房ID",
            room_id=room_id
        ))

        if room_id not in MUSHROOM_ROOM_IDS:
            result.error_message = (
                f"无效的库房ID: {room_id}. "
                f"必须是以下之一: {MUSHROOM_ROOM_IDS}"
            )
            result.processing_time = time.time() - start_time
            logger.error(log_message(
                "PARAM_VALIDATION_ERROR",
                "库房ID验证失败",
                room_id=room_id,
                valid_ids=MUSHROOM_ROOM_IDS
            ))
            return result

        logger.info(log_message(
            "PARAM_VALIDATION_SUCCESS",
            "库房ID验证通过",
            room_id=room_id
        ))

        # 导入依赖
        logger.debug(log_message(
            "DEPENDENCY_IMPORT_START", "开始导入系统依赖"
        ))

        from global_const.global_const import (
            BASE_DIR,
            settings,
            static_settings,
            pgsql_engine
        )
        from decision_analysis.decision_analyzer import DecisionAnalyzer

        logger.debug(log_message("DEPENDENCY_IMPORT_SUCCESS", "系统依赖导入成功"))

        # 获取模板路径
        template_path = BASE_DIR / "configs" / "decision_prompt.jinja"
        logger.debug(log_message(
            "TEMPLATE_VALIDATION",
            "检查决策模板文件",
            path=str(template_path)
        ))

        if not template_path.exists():
            result.error_message = f"模板文件未找到: {template_path}"
            result.processing_time = time.time() - start_time
            logger.error(log_message("TEMPLATE_VALIDATION", result.error_message))
            return result

        logger.info(log_message(
            "TEMPLATE_VALIDATION",
            "决策模板文件验证成功",
            path=str(template_path)
        ))

        # 初始化DecisionAnalyzer
        logger.info(log_message(
            "ANALYZER_INIT_START",
            "开始初始化决策分析器",
            template="decision_prompt.jinja"
        ))

        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )

        logger.info(log_message("ANALYZER_INIT_SUCCESS", "决策分析器初始化成功"))
        logger.debug(log_message(
            "DB_CONNECTION_CHECK",
            "数据库引擎配置完成",
            engine_type=type(pgsql_engine).__name__
        ))

        # 执行增强分析
        analysis_start = time.time()
        logger.info(log_message(
            "BUSINESS_FLOW_CHECKPOINT",
            "开始执行增强决策分析核心流程",
            room_id=room_id,
            analysis_time=analysis_datetime
        ))

        # 记录分析前的上下文信息
        logger.info(log_message(
            "BUSINESS_FLOW_CHECKPOINT",
            "分析上下文信息记录",
            room_id=room_id,
            analysis_time=analysis_datetime,
            template_name="decision_prompt.jinja"
        ))

        enhanced_decision_output = analyzer.analyze_enhanced(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )

        analysis_duration = time.time() - analysis_start
        logger.info(log_message(
            "BUSINESS_FLOW_CHECKPOINT",
            "增强决策分析核心流程完成",
            room_id=room_id,
            processing_time=analysis_duration
        ))

        result.enhanced_decision_output = enhanced_decision_output
        result.metadata = {
            "data_sources": enhanced_decision_output.metadata.data_sources,
            "similar_cases_count": enhanced_decision_output.metadata.similar_cases_count,
            "avg_similarity_score": enhanced_decision_output.metadata.avg_similarity_score,
            "llm_model": enhanced_decision_output.metadata.llm_model,
            "llm_response_time": enhanced_decision_output.metadata.llm_response_time,
            "total_processing_time": enhanced_decision_output.metadata.total_processing_time,
            "warnings": enhanced_decision_output.metadata.warnings,
            "errors": enhanced_decision_output.metadata.errors,
            "multi_image_count": getattr(enhanced_decision_output.metadata, "multi_image_count", 0),
            "image_aggregation_method": getattr(
                enhanced_decision_output.metadata, "image_aggregation_method", "single_image"
            ),
            "enhanced_format": True,
        }

        # 记录增强功能使用情况
        enhanced_features = [
            "multi_image_aggregation",
            "structured_parameter_adjustments",
            "risk_assessment",
            "enhanced_llm_prompting"
        ]

        logger.debug(log_message(
            "BUSINESS_FLOW_CHECKPOINT",
            "增强功能使用记录",
            features=enhanced_features,
            multi_image_count=result.metadata.get("multi_image_count", 0)
        ))

        # 生成输出文件路径
        if output_file:
            output_path = (
                Path(output_file) if isinstance(output_file, str)
                else output_file
            )
            logger.debug(log_message(
                "OUTPUT_PATH_GENERATION",
                "使用自定义输出文件路径",
                path=str(output_path)
            ))
        else:
            output_path = generate_enhanced_output_filename(
                room_id, analysis_datetime
            )
            logger.debug(log_message(
                "OUTPUT_PATH_GENERATION",
                "自动生成输出文件路径",
                path=str(output_path)
            ))

        # 保存结果
        save_start = time.time()
        save_enhanced_json_output(result, output_path, output_format)
        save_duration = time.time() - save_start
        logger.debug(log_message(
            "PERFORMANCE_EXECUTION_TIME",
            "JSON文件保存完成",
            processing_time=save_duration
        ))

        result.output_file = output_path
        result.success = True
        result.processing_time = time.time() - start_time

        # 记录最终性能指标
        final_memory = process.memory_info()
        memory_delta = final_memory.rss - memory_info.rss

        performance_summary = {
            "total_time": result.processing_time,
            "analysis_time": analysis_duration,
            "save_time": save_duration,
            "memory_delta_mb": memory_delta / 1024 / 1024,
            "final_memory_mb": final_memory.rss / 1024 / 1024
        }

        logger.debug(log_message(
            "PERFORMANCE_EXECUTION_TIME",
            "性能指标总结",
            **performance_summary
        ))

        logger.info(log_message(
            "FINAL_SUMMARY",
            "增强决策分析执行成功",
            room_id=room_id,
            processing_time=result.processing_time,
            output_file=str(result.output_file)
        ))

        return result

    except Exception as e:
        result.error_message = f"增强决策分析执行失败: {str(e)}"
        result.processing_time = time.time() - start_time
        logger.error(log_message(
            "BUSINESS_FLOW_COMPLETE",
            "增强决策分析执行失败",
            room_id=room_id,
            error=str(e),
            processing_time=result.processing_time
        ))
        return result


# ===================== CLI接口 =====================

def create_argument_parser() -> argparse.ArgumentParser:
    """
    创建命令行参数解析器。

    Returns:
        配置好的ArgumentParser实例
    """
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
  # Analyze room 611 at current time with enhanced features (both formats)
  python scripts/run_enhanced_decision_analysis.py --room-id 611
  
  # Analyze room 611 at specific datetime
  python scripts/run_enhanced_decision_analysis.py --room-id 611 \\
      --datetime "2024-01-15 10:00:00"
  
  # Save results to custom file
  python scripts/run_enhanced_decision_analysis.py --room-id 611 \\
      --output my_enhanced_results.json
  
  # Output only monitoring points config format
  python scripts/run_enhanced_decision_analysis.py --room-id 611 \\
      --format monitoring
  
  # Output only enhanced analysis format
  python scripts/run_enhanced_decision_analysis.py --room-id 611 \\
      --format enhanced
  
  # Verbose output with debug logs
  python scripts/run_enhanced_decision_analysis.py --room-id 611 --verbose
        """
    )

    parser.add_argument(
        "--room-id",
        type=str,
        choices=MUSHROOM_ROOM_IDS,
        default="607",
        help="Room ID (607, 608, 611, or 612)"
    )

    parser.add_argument(
        "--datetime",
        type=str,
        help=(
            "Analysis datetime in format 'YYYY-MM-DD HH:MM:SS' "
            "(default: current time)"
        )
    )

    parser.add_argument(
        "--output",
        type=str,
        help=(
            "Output JSON file path "
            "(default: enhanced_decision_analysis_<room_id>_<timestamp>.json)"
        )
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output (DEBUG level logs)"
    )

    parser.add_argument(
        "--format",
        type=str,
        choices=OUTPUT_FORMATS,
        default="monitoring",
        help=(
            "Output format: 'enhanced' (original format), "
            "'monitoring' (monitoring points config format), "
            "or 'both' (default: monitoring)"
        )
    )

    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Skip console output, only save to JSON file"
    )

    return parser


def main() -> int:
    """
    主CLI入口点。

    Returns:
        退出码：0表示成功，1表示失败
    """
    # 日志设置已在模块加载时初始化
    pass

    logger.info("=" * 80)
    logger.info("Enhanced Decision Analysis CLI")
    logger.info("=" * 80)

    # 解析命令行参数
    parser = create_argument_parser()
    args = parser.parse_args()

    # 记录CLI启动上下文
    logger.info(log_message(
        "SYSTEM_INIT_START",
        "CLI启动",
        room_id=args.room_id,
        datetime=args.datetime,
        output=args.output,
        output_format=args.format,
        verbose=args.verbose,
        no_console=args.no_console
    ))

    # Parse datetime
    try:
        logger.debug(log_message(
            "DATETIME_PARSING",
            "解析日期时间参数",
            input=args.datetime
        ))
        analysis_datetime = parse_datetime(args.datetime)
        logger.info(log_message(
            "PARAM_VALIDATION_SUCCESS",
            "CLI参数解析成功",
            room_id=args.room_id,
            analysis_time=analysis_datetime
        ))
    except ValueError as e:
        logger.error(log_message(
            "PARAM_VALIDATION_ERROR",
            "日期时间解析失败",
            error=str(e)
        ))
        return 1

    # 设置日志级别
    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    # 执行增强决策分析
    try:
        logger.info(log_message(
            "BUSINESS_FLOW_START",
            "从CLI执行增强决策分析"
        ))

        result = execute_enhanced_decision_analysis(
            room_id=args.room_id,
            analysis_datetime=analysis_datetime,
            output_file=args.output,
            verbose=args.verbose,
            output_format=args.format
        )

        if result.success:
            logger.info(log_message(
                "FINAL_SUMMARY",
                "增强决策分析执行成功",
                room_id=result.room_id,
                processing_time=result.processing_time
            ))

            # 控制台输出（除非禁用）
            if not args.no_console:
                console_output = format_enhanced_console_output(result)
                print(console_output)

            logger.info(log_message(
                "FILE_SAVE_SUCCESS",
                "输出文件",
                path=str(result.output_file.absolute())
            ))

        else:
            logger.error(log_message(
                "FINAL_SUMMARY",
                "增强决策分析执行失败",
                room_id=args.room_id,
                error=result.error_message
            ))
            print(f"❌ 错误: {result.error_message}")

    except Exception as e:
        logger.error(log_message(
            "SYSTEM_INIT_ERROR",
            "CLI执行过程中发生未预期错误",
            error=str(e)
        ))
        print(f"❌ 未预期错误: {str(e)}")
        return 1

    logger.info("=" * 80)

    # 根据状态返回退出码
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())