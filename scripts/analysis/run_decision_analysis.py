#!/usr/bin/env python3
"""
Decision Analysis CLI Script

This script provides a command-line interface for running decision analysis
on mushroom growing rooms. It extracts data from the database, performs CLIP
matching, renders prompts, calls LLM, and outputs structured recommendations.

Usage:
    # Basic usage with room ID and datetime
    python scripts/run_decision_analysis.py --room-id 611 --datetime "2024-01-15 10:00:00"
    
    # Use current time
    python scripts/run_decision_analysis.py --room-id 611
    
    # Specify output file
    python scripts/run_decision_analysis.py --room-id 611 --output results.json
    
    # Verbose output
    python scripts/run_decision_analysis.py --room-id 611 --verbose

Requirements: 8.5
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


# ===================== 数据模型 =====================

@dataclass
class DecisionAnalysisResult:
    """
    决策分析执行结果数据模型
    
    包含执行状态、错误信息和分析结果的结构化对象
    
    Attributes:
        success: 执行是否成功
        status: 结果状态 ("success", "partial", "error", "fallback")
        room_id: 蘑菇房编号
        analysis_time: 分析执行时间
        output_file: 输出文件路径（如果有）
        result: DecisionOutput 对象（如果成功）
        error_message: 错误信息（如果失败）
        warnings: 警告信息列表
        processing_time: 处理耗时（秒）
        metadata: 额外元数据
    """
    success: bool
    status: str
    room_id: str
    analysis_time: datetime
    output_file: Optional[Path] = None
    result: Optional[Any] = None  # DecisionOutput
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    processing_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
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
            "metadata": self.metadata
        }


# ===================== 辅助函数 =====================

def setup_cli_logger(verbose: bool = False) -> None:
    """
    配置CLI输出的logger（仅用于独立命令行调用）
    
    注意：此函数仅在脚本作为独立命令行工具运行时使用
    在作为模块导入时，应使用 utils.loguru_setting 的全局配置
    
    Args:
        verbose: 如果为True，显示DEBUG级别日志
    """
    logger.remove()
    
    log_level = "DEBUG" if verbose else "INFO"
    
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=log_level
    )


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
    if datetime_str is None:
        return datetime.now()
    
    try:
        return datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Try alternative format without seconds
        try:
            return datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
        except ValueError:
            # Try date only format
            try:
                return datetime.strptime(datetime_str, "%Y-%m-%d")
            except ValueError:
                raise ValueError(
                    f"Invalid datetime format: {datetime_str}. "
                    f"Expected formats: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD'"
                )


def format_console_output(result) -> str:
    """
    格式化决策输出用于控制台显示
    
    Args:
        result: DecisionOutput对象
    
    Returns:
        格式化的控制台输出字符串
    """
    lines = []
    lines.append("=" * 80)
    lines.append("决策分析结果 / Decision Analysis Results")
    lines.append("=" * 80)
    lines.append("")
    
    # Basic info
    lines.append(f"状态 (Status): {result.status}")
    lines.append(f"库房编号 (Room ID): {result.room_id}")
    lines.append(f"分析时间 (Analysis Time): {result.analysis_time}")
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
    
    # Device Recommendations
    lines.append("=" * 80)
    lines.append("设备参数建议 / Device Recommendations")
    lines.append("=" * 80)
    
    # Air Cooler
    lines.append("\n【冷风机 / Air Cooler】")
    ac = result.device_recommendations.air_cooler
    lines.append(f"  温度设定 (Temp Set): {ac.tem_set}°C")
    lines.append(f"  温差设定 (Temp Diff): {ac.tem_diff_set}°C")
    lines.append(f"  循环开关 (Cycle On/Off): {ac.cyc_on_off}")
    lines.append(f"  循环开启时间 (Cycle On Time): {ac.cyc_on_time}分钟")
    lines.append(f"  循环关闭时间 (Cycle Off Time): {ac.cyc_off_time}分钟")
    lines.append(f"  新风联动 (Fresh Air Link): {ac.ar_on_off}")
    lines.append(f"  加湿联动 (Humidifier Link): {ac.hum_on_off}")
    if ac.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in ac.rationale:
            lines.append(f"    • {reason}")
    
    # Fresh Air Fan
    lines.append("\n【新风机 / Fresh Air Fan】")
    faf = result.device_recommendations.fresh_air_fan
    lines.append(f"  模式 (Mode): {faf.model}")
    lines.append(f"  控制方式 (Control): {faf.control}")
    lines.append(f"  CO2启动阈值 (CO2 On): {faf.co2_on}ppm")
    lines.append(f"  CO2停止阈值 (CO2 Off): {faf.co2_off}ppm")
    lines.append(f"  开启时间 (On Time): {faf.on}分钟")
    lines.append(f"  停止时间 (Off Time): {faf.off}分钟")
    if faf.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in faf.rationale:
            lines.append(f"    • {reason}")
    
    # Humidifier
    lines.append("\n【加湿器 / Humidifier】")
    hum = result.device_recommendations.humidifier
    lines.append(f"  模式 (Mode): {hum.model}")
    lines.append(f"  开启湿度阈值 (On Threshold): {hum.on}%")
    lines.append(f"  停止湿度阈值 (Off Threshold): {hum.off}%")
    if hum.left_right_strategy:
        lines.append(f"  左右侧策略 (Left/Right Strategy): {hum.left_right_strategy}")
    if hum.rationale:
        lines.append("  判断依据 (Rationale):")
        for reason in hum.rationale:
            lines.append(f"    • {reason}")
    
    # Grow Light
    lines.append("\n【补光灯 / Grow Light】")
    gl = result.device_recommendations.grow_light
    lines.append(f"  模式 (Mode): {gl.model}")
    lines.append(f"  开启时长 (On Duration): {gl.on_mset}分钟")
    lines.append(f"  停止时长 (Off Duration): {gl.off_mset}分钟")
    lines.append(f"  1#补光开关 (Light 1 On/Off): {gl.on_off_1}, 光源选择 (Source): {gl.choose_1}")
    lines.append(f"  2#补光开关 (Light 2 On/Off): {gl.on_off_2}, 光源选择 (Source): {gl.choose_2}")
    lines.append(f"  3#补光开关 (Light 3 On/Off): {gl.on_off_3}, 光源选择 (Source): {gl.choose_3}")
    lines.append(f"  4#补光开关 (Light 4 On/Off): {gl.on_off_4}, 光源选择 (Source): {gl.choose_4}")
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
    
    # Metadata
    lines.append("=" * 80)
    lines.append("元数据 / Metadata")
    lines.append("=" * 80)
    lines.append(f"数据源 (Data Sources): {result.metadata.data_sources}")
    lines.append(f"相似案例数 (Similar Cases): {result.metadata.similar_cases_count}")
    lines.append(f"平均相似度 (Avg Similarity): {result.metadata.avg_similarity_score:.2f}%")
    lines.append(f"LLM模型 (LLM Model): {result.metadata.llm_model}")
    lines.append(f"LLM响应时间 (LLM Response Time): {result.metadata.llm_response_time:.2f}秒")
    lines.append(f"总处理时间 (Total Processing Time): {result.metadata.total_processing_time:.2f}秒")
    
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


def save_json_output(result, output_path: Path) -> None:
    """
    保存决策输出到JSON文件
    
    Args:
        result: DecisionOutput对象
        output_path: 输出JSON文件路径
    """
    # Convert result to dictionary
    output_dict = {
        "status": result.status,
        "room_id": result.room_id,
        "analysis_time": result.analysis_time.isoformat(),
        "strategy": {
            "core_objective": result.strategy.core_objective,
            "priority_ranking": result.strategy.priority_ranking,
            "key_risk_points": result.strategy.key_risk_points
        },
        "device_recommendations": {
            "air_cooler": {
                "tem_set": result.device_recommendations.air_cooler.tem_set,
                "tem_diff_set": result.device_recommendations.air_cooler.tem_diff_set,
                "cyc_on_off": result.device_recommendations.air_cooler.cyc_on_off,
                "cyc_on_time": result.device_recommendations.air_cooler.cyc_on_time,
                "cyc_off_time": result.device_recommendations.air_cooler.cyc_off_time,
                "ar_on_off": result.device_recommendations.air_cooler.ar_on_off,
                "hum_on_off": result.device_recommendations.air_cooler.hum_on_off,
                "rationale": result.device_recommendations.air_cooler.rationale
            },
            "fresh_air_fan": {
                "model": result.device_recommendations.fresh_air_fan.model,
                "control": result.device_recommendations.fresh_air_fan.control,
                "co2_on": result.device_recommendations.fresh_air_fan.co2_on,
                "co2_off": result.device_recommendations.fresh_air_fan.co2_off,
                "on": result.device_recommendations.fresh_air_fan.on,
                "off": result.device_recommendations.fresh_air_fan.off,
                "rationale": result.device_recommendations.fresh_air_fan.rationale
            },
            "humidifier": {
                "model": result.device_recommendations.humidifier.model,
                "on": result.device_recommendations.humidifier.on,
                "off": result.device_recommendations.humidifier.off,
                "left_right_strategy": result.device_recommendations.humidifier.left_right_strategy,
                "rationale": result.device_recommendations.humidifier.rationale
            },
            "grow_light": {
                "model": result.device_recommendations.grow_light.model,
                "on_mset": result.device_recommendations.grow_light.on_mset,
                "off_mset": result.device_recommendations.grow_light.off_mset,
                "on_off_1": result.device_recommendations.grow_light.on_off_1,
                "choose_1": result.device_recommendations.grow_light.choose_1,
                "on_off_2": result.device_recommendations.grow_light.on_off_2,
                "choose_2": result.device_recommendations.grow_light.choose_2,
                "on_off_3": result.device_recommendations.grow_light.on_off_3,
                "choose_3": result.device_recommendations.grow_light.choose_3,
                "on_off_4": result.device_recommendations.grow_light.on_off_4,
                "choose_4": result.device_recommendations.grow_light.choose_4,
                "rationale": result.device_recommendations.grow_light.rationale
            }
        },
        "monitoring_points": {
            "key_time_periods": result.monitoring_points.key_time_periods,
            "warning_thresholds": result.monitoring_points.warning_thresholds,
            "emergency_measures": result.monitoring_points.emergency_measures
        },
        "metadata": {
            "data_sources": result.metadata.data_sources,
            "similar_cases_count": result.metadata.similar_cases_count,
            "avg_similarity_score": result.metadata.avg_similarity_score,
            "llm_model": result.metadata.llm_model,
            "llm_response_time": result.metadata.llm_response_time,
            "total_processing_time": result.metadata.total_processing_time,
            "warnings": result.metadata.warnings,
            "errors": result.metadata.errors
        }
    }
    
    # Write to file with pretty formatting
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_dict, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Results saved to: {output_path}")


def generate_output_filename(
    room_id: str,
    analysis_datetime: datetime,
    output_dir: Optional[Path] = None
) -> Path:
    """
    生成输出文件名
    
    Args:
        room_id: 蘑菇房编号
        analysis_datetime: 分析时间
        output_dir: 输出目录，默认为项目根目录的output文件夹
    
    Returns:
        完整的输出文件路径
    """
    from global_const.const_config import (
        DECISION_OUTPUT_FILENAME_PATTERN,
        OUTPUT_DIR_NAME
    )
    from global_const.global_const import BASE_DIR
    
    # 确定输出目录
    if output_dir is None:
        output_dir = BASE_DIR.parent / OUTPUT_DIR_NAME
    
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成文件名
    timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
    filename = DECISION_OUTPUT_FILENAME_PATTERN.format(
        room_id=room_id,
        timestamp=timestamp
    )
    
    return output_dir / filename


# ===================== 核心执行函数 =====================

def execute_decision_analysis(
    room_id: str,
    analysis_datetime: Optional[datetime] = None,
    output_file: Optional[Union[str, Path]] = None,
    verbose: bool = False
) -> DecisionAnalysisResult:
    """
    执行决策分析的主要入口函数
    
    此函数封装了完整的决策分析流程，包括：
    - 数据提取和预处理
    - CLIP相似度匹配
    - 模板渲染
    - LLM决策生成
    - 输出验证和格式化
    - JSON文件输出
    
    Args:
        room_id: 蘑菇房编号（"607", "608", "611", "612"）
        analysis_datetime: 分析时间点，默认为当前时间
        output_file: 输出JSON文件路径，默认自动生成
        verbose: 是否启用详细日志输出
    
    Returns:
        DecisionAnalysisResult: 包含执行状态、错误信息和结果的结构化对象
    
    Raises:
        无异常抛出，所有错误都封装在返回结果中
    
    Example:
        >>> result = execute_decision_analysis(
        ...     room_id="611",
        ...     analysis_datetime=datetime.now(),
        ...     output_file="output/result.json",
        ...     verbose=True
        ... )
        >>> if result.success:
        ...     print(f"分析成功: {result.output_file}")
        ... else:
        ...     print(f"分析失败: {result.error_message}")
    """
    import time
    start_time = time.time()
    
    # 初始化结果对象
    if analysis_datetime is None:
        analysis_datetime = datetime.now()
    
    result = DecisionAnalysisResult(
        success=False,
        status="pending",
        room_id=room_id,
        analysis_time=analysis_datetime,
        warnings=[]
    )
    
    # 验证房间ID
    from global_const.const_config import MUSHROOM_ROOM_IDS
    if room_id not in MUSHROOM_ROOM_IDS:
        result.status = "error"
        result.error_message = f"Invalid room_id: {room_id}. Must be one of {MUSHROOM_ROOM_IDS}"
        result.processing_time = time.time() - start_time
        return result
    
    try:
        # 导入依赖
        logger.info(f"[DECISION_ANALYSIS] Starting analysis for room {room_id}")
        logger.info(f"[DECISION_ANALYSIS] Analysis time: {analysis_datetime}")
        
        from global_const.global_const import settings, static_settings, pgsql_engine, BASE_DIR
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        # 获取模板路径
        template_path = BASE_DIR / "configs" / "decision_prompt.jinja"
        
        if not template_path.exists():
            result.status = "error"
            result.error_message = f"Template file not found: {template_path}"
            result.processing_time = time.time() - start_time
            return result
        
        # 初始化DecisionAnalyzer
        logger.info("[DECISION_ANALYSIS] Initializing DecisionAnalyzer...")
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        logger.info("[DECISION_ANALYSIS] DecisionAnalyzer initialized successfully")
        
        # 执行分析
        logger.info("[DECISION_ANALYSIS] Running analysis...")
        decision_output = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        logger.info("[DECISION_ANALYSIS] Analysis completed successfully")
        
        # 确定输出文件路径
        if output_file:
            output_path = Path(output_file) if isinstance(output_file, str) else output_file
        else:
            output_path = generate_output_filename(room_id, analysis_datetime)
        
        # 保存JSON输出
        save_json_output(decision_output, output_path)
        
        # 设置成功结果
        result.success = True
        result.status = decision_output.status
        result.result = decision_output
        result.output_file = output_path
        result.warnings = decision_output.metadata.warnings.copy()
        result.metadata = {
            "data_sources": decision_output.metadata.data_sources,
            "similar_cases_count": decision_output.metadata.similar_cases_count,
            "avg_similarity_score": decision_output.metadata.avg_similarity_score,
            "llm_model": decision_output.metadata.llm_model,
            "llm_response_time": decision_output.metadata.llm_response_time
        }
        
        # 检查是否有错误
        # 过滤掉非关键错误，如缺少embedding数据的情况
        critical_errors = []
        for error in decision_output.metadata.errors:
            # 如果错误是关于缺少embedding数据的，这通常不是致命错误
            # 因为LLM仍然可以根据其他数据（环境统计、设备变更等）生成决策
            if "No embedding data found" not in error:
                critical_errors.append(error)
        
        if critical_errors:
            result.success = False
            result.status = "partial"
            result.error_message = "; ".join(critical_errors)
        elif decision_output.metadata.errors:
            # 如果只有非关键错误（如缺少embedding数据），但仍生成了决策，则认为是成功的
            result.success = True
            result.status = "success"
            # 将非关键错误作为警告处理
            result.warnings.extend([err for err in decision_output.metadata.errors if "No embedding data found" in err])
        
        logger.info(f"[DECISION_ANALYSIS] Results saved to: {output_path}")
        
    except ImportError as e:
        result.status = "error"
        result.error_message = f"Failed to import dependencies: {e}"
        logger.error(f"[DECISION_ANALYSIS] Import error: {e}")
        
    except Exception as e:
        result.status = "error"
        result.error_message = f"Analysis failed: {str(e)}"
        logger.error(f"[DECISION_ANALYSIS] Analysis failed: {e}", exc_info=verbose)
    
    result.processing_time = time.time() - start_time
    
    # 记录最终状态
    if result.success:
        logger.info(
            f"[DECISION_ANALYSIS] Completed: room={room_id}, status={result.status}, "
            f"time={result.processing_time:.2f}s"
        )
    else:
        logger.error(
            f"[DECISION_ANALYSIS] Failed: room={room_id}, error={result.error_message}, "
            f"time={result.processing_time:.2f}s"
        )
    
    return result


# ===================== 命令行入口 =====================

def main() -> int:
    """
    主CLI入口点
    
    解析命令行参数，初始化DecisionAnalyzer，
    运行分析，并输出结果到控制台和JSON文件
    
    Returns:
        退出码：0表示成功，1表示失败
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Run decision analysis for mushroom growing rooms",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze room 611 at current time
  python scripts/run_decision_analysis.py --room-id 611
  
  # Analyze room 611 at specific datetime
  python scripts/run_decision_analysis.py --room-id 611 --datetime "2024-01-15 10:00:00"
  
  # Save results to custom file
  python scripts/run_decision_analysis.py --room-id 611 --output my_results.json
  
  # Verbose output with debug logs
  python scripts/run_decision_analysis.py --room-id 611 --verbose
        """
    )
    
    parser.add_argument(
        "--room-id",
        type=str,
        required=True,
        choices=["607", "608", "611", "612"],
        help="Room ID (607, 608, 611, or 612)"
    )
    
    parser.add_argument(
        "--datetime",
        type=str,
        default=None,
        help="Analysis datetime in format 'YYYY-MM-DD HH:MM:SS' (default: current time)"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: decision_analysis_<room_id>_<timestamp>.json)"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output (DEBUG level logs)"
    )
    
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Skip console output, only save to JSON file"
    )
    
    args = parser.parse_args()
    
    # Setup logger for CLI mode
    setup_cli_logger(verbose=args.verbose)
    
    logger.info("=" * 80)
    logger.info("Decision Analysis CLI")
    logger.info("=" * 80)
    
    # Parse datetime
    try:
        analysis_datetime = parse_datetime(args.datetime)
        logger.info(f"Room ID: {args.room_id}")
        logger.info(f"Analysis Time: {analysis_datetime}")
    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1
    
    # Execute decision analysis
    result = execute_decision_analysis(
        room_id=args.room_id,
        analysis_datetime=analysis_datetime,
        output_file=args.output,
        verbose=args.verbose
    )
    
    # Output results to console
    if not args.no_console and result.success and result.result:
        console_output = format_console_output(result.result)
        print(console_output)
    
    # Final summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("Summary")
    logger.info("=" * 80)
    logger.info(f"Success: {result.success}")
    logger.info(f"Status: {result.status}")
    logger.info(f"Total Processing Time: {result.processing_time:.2f}s")
    
    if result.warnings:
        logger.info(f"Warnings: {len(result.warnings)}")
    
    if result.error_message:
        logger.error(f"Error: {result.error_message}")
    
    if result.output_file:
        logger.info(f"Output File: {result.output_file.absolute()}")
    
    logger.info("=" * 80)
    
    # Return exit code based on status
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
