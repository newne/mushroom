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
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger


def setup_logger(verbose: bool = False):
    """
    Configure logger for CLI output
    
    Args:
        verbose: If True, show DEBUG level logs
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
    Parse datetime string to datetime object
    
    Args:
        datetime_str: Datetime string in format "YYYY-MM-DD HH:MM:SS"
                     If None, uses current time
    
    Returns:
        datetime object
    
    Raises:
        ValueError: If datetime string format is invalid
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
    Format decision output for console display
    
    Args:
        result: DecisionOutput object
    
    Returns:
        Formatted string for console output
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


def save_json_output(result, output_path: Path):
    """
    Save decision output to JSON file
    
    Args:
        result: DecisionOutput object
        output_path: Path to output JSON file
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


def main():
    """
    Main CLI entry point
    
    Parses command-line arguments, initializes DecisionAnalyzer,
    runs analysis, and outputs results to console and JSON file.
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
    
    # Setup logger
    setup_logger(verbose=args.verbose)
    
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
    
    # Import dependencies
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
    except ImportError as e:
        logger.error(f"Failed to import dependencies: {e}")
        logger.error("Make sure you are running from the project root directory")
        return 1
    
    # Get template path
    template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
    
    if not template_path.exists():
        logger.error(f"Template file not found: {template_path}")
        return 1
    
    # Initialize DecisionAnalyzer
    try:
        logger.info("Initializing DecisionAnalyzer...")
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        logger.info("DecisionAnalyzer initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize DecisionAnalyzer: {e}")
        if args.verbose:
            logger.exception(e)
        return 1
    
    # Run analysis
    try:
        logger.info("")
        logger.info("Starting decision analysis...")
        logger.info("")
        
        result = analyzer.analyze(
            room_id=args.room_id,
            analysis_datetime=analysis_datetime
        )
        
        logger.info("")
        logger.info("Analysis completed successfully")
        logger.info("")
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        if args.verbose:
            logger.exception(e)
        return 1
    
    # Output results to console
    if not args.no_console:
        console_output = format_console_output(result)
        print(console_output)
    
    # Save results to JSON file
    if args.output:
        output_path = Path(args.output)
    else:
        # Generate default filename
        timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
        output_filename = f"decision_analysis_{args.room_id}_{timestamp}.json"
        output_path = Path(output_filename)
    
    try:
        save_json_output(result, output_path)
    except Exception as e:
        logger.error(f"Failed to save JSON output: {e}")
        if args.verbose:
            logger.exception(e)
        return 1
    
    # Final summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("Summary")
    logger.info("=" * 80)
    logger.info(f"Status: {result.status}")
    logger.info(f"Total Processing Time: {result.metadata.total_processing_time:.2f}s")
    logger.info(f"Warnings: {len(result.metadata.warnings)}")
    logger.info(f"Errors: {len(result.metadata.errors)}")
    logger.info(f"Output File: {output_path.absolute()}")
    logger.info("=" * 80)
    
    # Return exit code based on status
    if result.status == "success":
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
