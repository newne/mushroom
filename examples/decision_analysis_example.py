#!/usr/bin/env python3
"""
决策分析模块使用示例 / Decision Analysis Module Usage Examples

本脚本演示如何使用决策分析模块进行蘑菇种植环境调控决策分析。
包含基本使用、错误处理、输出格式等多个场景的示例。

This script demonstrates how to use the decision analysis module for mushroom
growing environment control decision analysis. It includes examples of basic
usage, error handling, output formats, and more.

Requirements:
- PostgreSQL database with mushroom data
- LLaMA API endpoint configured
- All dependencies installed (see requirements.txt)

Usage:
    python examples/decision_analysis_example.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到路径 / Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger

# 配置日志输出 / Configure logger output
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)


def example_1_basic_usage():
    """
    示例1: 基本使用方法
    Example 1: Basic Usage
    
    演示如何初始化DecisionAnalyzer并执行基本的决策分析。
    Demonstrates how to initialize DecisionAnalyzer and perform basic decision analysis.
    """
    logger.info("=" * 80)
    logger.info("示例1: 基本使用方法 / Example 1: Basic Usage")
    logger.info("=" * 80)
    
    try:
        # 导入必要的模块 / Import required modules
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        # 获取模板路径 / Get template path
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        # 初始化决策分析器 / Initialize DecisionAnalyzer
        logger.info("初始化决策分析器... / Initializing DecisionAnalyzer...")
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        logger.success("✓ 决策分析器初始化成功 / DecisionAnalyzer initialized successfully")
        
        # 执行决策分析 / Perform decision analysis
        room_id = "611"
        analysis_datetime = datetime.now()
        
        logger.info(f"执行决策分析... / Performing decision analysis...")
        logger.info(f"  库房编号 / Room ID: {room_id}")
        logger.info(f"  分析时间 / Analysis Time: {analysis_datetime}")
        
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # 输出结果摘要 / Output result summary
        logger.success(f"✓ 决策分析完成 / Decision analysis completed")
        logger.info(f"  状态 / Status: {result.status}")
        logger.info(f"  核心目标 / Core Objective: {result.strategy.core_objective}")
        logger.info(f"  处理时间 / Processing Time: {result.metadata.total_processing_time:.2f}s")
        logger.info(f"  相似案例数 / Similar Cases: {result.metadata.similar_cases_count}")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 示例1执行失败 / Example 1 failed: {e}")
        logger.exception(e)
        return None


def example_2_with_specific_datetime():
    """
    示例2: 指定分析时间
    Example 2: Specify Analysis DateTime
    
    演示如何分析特定时间点的数据。
    Demonstrates how to analyze data at a specific point in time.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例2: 指定分析时间 / Example 2: Specify Analysis DateTime")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        # 分析昨天的数据 / Analyze yesterday's data
        room_id = "611"
        analysis_datetime = datetime.now() - timedelta(days=1)
        
        logger.info(f"分析历史数据... / Analyzing historical data...")
        logger.info(f"  库房编号 / Room ID: {room_id}")
        logger.info(f"  分析时间 / Analysis Time: {analysis_datetime}")
        
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        logger.success(f"✓ 历史数据分析完成 / Historical data analysis completed")
        logger.info(f"  数据源数量 / Data Sources: {len(result.metadata.data_sources)}")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 示例2执行失败 / Example 2 failed: {e}")
        return None


def example_3_error_handling():
    """
    示例3: 错误处理
    Example 3: Error Handling
    
    演示如何处理各种错误情况，包括数据缺失、API失败等。
    Demonstrates how to handle various error conditions including missing data, API failures, etc.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例3: 错误处理 / Example 3: Error Handling")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        # 尝试分析一个可能没有数据的时间点 / Try analyzing a time point that may have no data
        room_id = "611"
        # 使用很久以前的日期，可能没有数据 / Use a date far in the past, may have no data
        analysis_datetime = datetime(2020, 1, 1, 10, 0, 0)
        
        logger.info(f"尝试分析可能缺少数据的时间点... / Trying to analyze a time point that may lack data...")
        logger.info(f"  库房编号 / Room ID: {room_id}")
        logger.info(f"  分析时间 / Analysis Time: {analysis_datetime}")
        
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # 检查警告和错误 / Check warnings and errors
        if result.metadata.warnings:
            logger.warning(f"⚠ 发现 {len(result.metadata.warnings)} 个警告 / Found {len(result.metadata.warnings)} warnings:")
            for warning in result.metadata.warnings:
                logger.warning(f"  • {warning}")
        
        if result.metadata.errors:
            logger.error(f"✗ 发现 {len(result.metadata.errors)} 个错误 / Found {len(result.metadata.errors)} errors:")
            for error in result.metadata.errors:
                logger.error(f"  • {error}")
        
        # 即使有错误，系统也应该返回降级的决策 / Even with errors, system should return degraded decision
        if result.status == "success":
            logger.success("✓ 系统成功处理了数据缺失情况 / System successfully handled missing data")
        else:
            logger.info("ℹ 系统返回了降级决策 / System returned degraded decision")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 示例3执行失败 / Example 3 failed: {e}")
        return None


def example_4_output_formats():
    """
    示例4: 输出格式演示
    Example 4: Output Format Demonstration
    
    演示如何访问和使用决策输出的各个部分。
    Demonstrates how to access and use different parts of the decision output.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例4: 输出格式演示 / Example 4: Output Format Demonstration")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        room_id = "611"
        analysis_datetime = datetime.now()
        
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # 演示如何访问各个部分的输出 / Demonstrate how to access different parts of output
        
        # 1. 调控总体策略 / Control Strategy
        logger.info("\n【调控总体策略 / Control Strategy】")
        logger.info(f"  核心目标 / Core Objective: {result.strategy.core_objective}")
        if result.strategy.priority_ranking:
            logger.info(f"  优先级排序 / Priority Ranking:")
            for i, priority in enumerate(result.strategy.priority_ranking, 1):
                logger.info(f"    {i}. {priority}")
        if result.strategy.key_risk_points:
            logger.info(f"  关键风险点 / Key Risk Points:")
            for risk in result.strategy.key_risk_points:
                logger.info(f"    • {risk}")
        
        # 2. 冷风机参数建议 / Air Cooler Recommendations
        logger.info("\n【冷风机参数建议 / Air Cooler Recommendations】")
        ac = result.device_recommendations.air_cooler
        logger.info(f"  温度设定 / Temp Set: {ac.tem_set}°C")
        logger.info(f"  温差设定 / Temp Diff: {ac.tem_diff_set}°C")
        logger.info(f"  循环开关 / Cycle On/Off: {ac.cyc_on_off}")
        logger.info(f"  循环开启时间 / Cycle On Time: {ac.cyc_on_time}分钟")
        logger.info(f"  循环关闭时间 / Cycle Off Time: {ac.cyc_off_time}分钟")
        if ac.rationale:
            logger.info(f"  判断依据 / Rationale: {ac.rationale[0]}")
        
        # 3. 新风机参数建议 / Fresh Air Fan Recommendations
        logger.info("\n【新风机参数建议 / Fresh Air Fan Recommendations】")
        faf = result.device_recommendations.fresh_air_fan
        logger.info(f"  模式 / Mode: {faf.model}")
        logger.info(f"  控制方式 / Control: {faf.control}")
        logger.info(f"  CO2启动阈值 / CO2 On: {faf.co2_on}ppm")
        logger.info(f"  CO2停止阈值 / CO2 Off: {faf.co2_off}ppm")
        
        # 4. 加湿器参数建议 / Humidifier Recommendations
        logger.info("\n【加湿器参数建议 / Humidifier Recommendations】")
        hum = result.device_recommendations.humidifier
        logger.info(f"  模式 / Mode: {hum.model}")
        logger.info(f"  开启湿度阈值 / On Threshold: {hum.on}%")
        logger.info(f"  停止湿度阈值 / Off Threshold: {hum.off}%")
        
        # 5. 补光灯参数建议 / Grow Light Recommendations
        logger.info("\n【补光灯参数建议 / Grow Light Recommendations】")
        gl = result.device_recommendations.grow_light
        logger.info(f"  模式 / Mode: {gl.model}")
        logger.info(f"  开启时长 / On Duration: {gl.on_mset}分钟")
        logger.info(f"  停止时长 / Off Duration: {gl.off_mset}分钟")
        logger.info(f"  1#补光 / Light 1: On/Off={gl.on_off_1}, Source={gl.choose_1}")
        
        # 6. 监控重点 / Monitoring Points
        logger.info("\n【24小时监控重点 / 24-Hour Monitoring Points】")
        if result.monitoring_points.key_time_periods:
            logger.info(f"  关键时段 / Key Time Periods:")
            for period in result.monitoring_points.key_time_periods[:3]:  # 显示前3个
                logger.info(f"    • {period}")
        
        # 7. 元数据 / Metadata
        logger.info("\n【元数据 / Metadata】")
        logger.info(f"  数据源 / Data Sources: {result.metadata.data_sources}")
        logger.info(f"  相似案例数 / Similar Cases: {result.metadata.similar_cases_count}")
        logger.info(f"  平均相似度 / Avg Similarity: {result.metadata.avg_similarity_score:.2f}%")
        logger.info(f"  LLM响应时间 / LLM Response Time: {result.metadata.llm_response_time:.2f}s")
        logger.info(f"  总处理时间 / Total Processing Time: {result.metadata.total_processing_time:.2f}s")
        
        logger.success("✓ 输出格式演示完成 / Output format demonstration completed")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 示例4执行失败 / Example 4 failed: {e}")
        return None


def example_5_save_to_json():
    """
    示例5: 保存结果到JSON文件
    Example 5: Save Results to JSON File
    
    演示如何将决策结果保存为JSON格式。
    Demonstrates how to save decision results to JSON format.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例5: 保存结果到JSON文件 / Example 5: Save Results to JSON File")
    logger.info("=" * 80)
    
    try:
        import json
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        room_id = "611"
        analysis_datetime = datetime.now()
        
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # 将结果转换为字典 / Convert result to dictionary
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
        
        # 保存到文件 / Save to file
        output_file = project_root / "decision_analysis_example_output.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_dict, f, ensure_ascii=False, indent=2)
        
        logger.success(f"✓ 结果已保存到 / Results saved to: {output_file}")
        logger.info(f"  文件大小 / File size: {output_file.stat().st_size} bytes")
        
        return output_dict
        
    except Exception as e:
        logger.error(f"✗ 示例5执行失败 / Example 5 failed: {e}")
        return None


def example_6_multiple_rooms():
    """
    示例6: 批量分析多个库房
    Example 6: Batch Analysis for Multiple Rooms
    
    演示如何批量分析多个库房的数据。
    Demonstrates how to perform batch analysis for multiple rooms.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例6: 批量分析多个库房 / Example 6: Batch Analysis for Multiple Rooms")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        # 初始化一次，重复使用 / Initialize once, reuse multiple times
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        # 要分析的库房列表 / List of rooms to analyze
        room_ids = ["607", "608", "611", "612"]
        analysis_datetime = datetime.now()
        
        results = {}
        
        logger.info(f"开始批量分析 {len(room_ids)} 个库房... / Starting batch analysis for {len(room_ids)} rooms...")
        
        for room_id in room_ids:
            logger.info(f"\n分析库房 {room_id}... / Analyzing room {room_id}...")
            
            try:
                result = analyzer.analyze(
                    room_id=room_id,
                    analysis_datetime=analysis_datetime
                )
                
                results[room_id] = result
                
                logger.success(f"✓ 库房 {room_id} 分析完成 / Room {room_id} analysis completed")
                logger.info(f"  状态 / Status: {result.status}")
                logger.info(f"  处理时间 / Processing Time: {result.metadata.total_processing_time:.2f}s")
                
            except Exception as e:
                logger.error(f"✗ 库房 {room_id} 分析失败 / Room {room_id} analysis failed: {e}")
                results[room_id] = None
        
        # 汇总统计 / Summary statistics
        logger.info("\n" + "=" * 60)
        logger.info("批量分析汇总 / Batch Analysis Summary")
        logger.info("=" * 60)
        
        successful = sum(1 for r in results.values() if r and r.status == "success")
        failed = len(results) - successful
        
        logger.info(f"总计 / Total: {len(results)} 个库房")
        logger.info(f"成功 / Successful: {successful} 个")
        logger.info(f"失败 / Failed: {failed} 个")
        
        if successful > 0:
            avg_time = sum(
                r.metadata.total_processing_time 
                for r in results.values() 
                if r and r.status == "success"
            ) / successful
            logger.info(f"平均处理时间 / Avg Processing Time: {avg_time:.2f}s")
        
        return results
        
    except Exception as e:
        logger.error(f"✗ 示例6执行失败 / Example 6 failed: {e}")
        return None


def example_7_custom_configuration():
    """
    示例7: 自定义配置
    Example 7: Custom Configuration
    
    演示如何使用自定义配置参数。
    Demonstrates how to use custom configuration parameters.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例7: 自定义配置 / Example 7: Custom Configuration")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        # 可以在这里修改settings中的参数 / Can modify settings parameters here
        # 例如：修改LLM温度参数 / For example: modify LLM temperature parameter
        logger.info("当前配置 / Current Configuration:")
        logger.info(f"  LLM模型 / LLM Model: {settings.llama.model}")
        logger.info(f"  LLM端点 / LLM Endpoint: {settings.llama.url}")
        logger.info(f"  数据库主机 / Database Host: {settings.host.host}")
        logger.info(f"  数据库端口 / Database Port: {settings.host.port}")
        
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        room_id = "611"
        analysis_datetime = datetime.now()
        
        # 执行分析 / Perform analysis
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        logger.success("✓ 使用自定义配置的分析完成 / Analysis with custom configuration completed")
        logger.info(f"  使用的LLM模型 / LLM Model Used: {result.metadata.llm_model}")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 示例7执行失败 / Example 7 failed: {e}")
        return None


def example_8_performance_monitoring():
    """
    示例8: 性能监控
    Example 8: Performance Monitoring
    
    演示如何监控决策分析的性能指标。
    Demonstrates how to monitor performance metrics of decision analysis.
    """
    logger.info("\n" + "=" * 80)
    logger.info("示例8: 性能监控 / Example 8: Performance Monitoring")
    logger.info("=" * 80)
    
    try:
        import time
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        template_path = project_root / "src" / "configs" / "decision_prompt.jinja"
        
        # 记录初始化时间 / Record initialization time
        init_start = time.time()
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        init_time = time.time() - init_start
        
        logger.info(f"初始化时间 / Initialization Time: {init_time:.2f}s")
        
        # 执行多次分析，监控性能 / Perform multiple analyses, monitor performance
        room_id = "611"
        analysis_datetime = datetime.now()
        num_runs = 3
        
        logger.info(f"\n执行 {num_runs} 次分析以监控性能... / Performing {num_runs} analyses to monitor performance...")
        
        times = []
        for i in range(num_runs):
            logger.info(f"\n第 {i+1} 次运行 / Run {i+1}:")
            
            result = analyzer.analyze(
                room_id=room_id,
                analysis_datetime=analysis_datetime
            )
            
            total_time = result.metadata.total_processing_time
            llm_time = result.metadata.llm_response_time
            data_time = total_time - llm_time
            
            times.append({
                "total": total_time,
                "llm": llm_time,
                "data": data_time
            })
            
            logger.info(f"  总时间 / Total Time: {total_time:.2f}s")
            logger.info(f"  LLM时间 / LLM Time: {llm_time:.2f}s ({llm_time/total_time*100:.1f}%)")
            logger.info(f"  数据处理时间 / Data Processing Time: {data_time:.2f}s ({data_time/total_time*100:.1f}%)")
        
        # 计算平均值 / Calculate averages
        logger.info("\n" + "=" * 60)
        logger.info("性能统计 / Performance Statistics")
        logger.info("=" * 60)
        
        avg_total = sum(t["total"] for t in times) / num_runs
        avg_llm = sum(t["llm"] for t in times) / num_runs
        avg_data = sum(t["data"] for t in times) / num_runs
        
        logger.info(f"平均总时间 / Avg Total Time: {avg_total:.2f}s")
        logger.info(f"平均LLM时间 / Avg LLM Time: {avg_llm:.2f}s ({avg_llm/avg_total*100:.1f}%)")
        logger.info(f"平均数据处理时间 / Avg Data Processing Time: {avg_data:.2f}s ({avg_data/avg_total*100:.1f}%)")
        
        # 性能建议 / Performance recommendations
        logger.info("\n性能建议 / Performance Recommendations:")
        if avg_llm > avg_total * 0.8:
            logger.warning("  ⚠ LLM调用占用了大部分时间，考虑优化提示词长度或使用更快的模型")
            logger.warning("  ⚠ LLM calls take most of the time, consider optimizing prompt length or using faster model")
        if avg_data > 10:
            logger.warning("  ⚠ 数据处理时间较长，考虑优化数据库查询或添加索引")
            logger.warning("  ⚠ Data processing time is long, consider optimizing database queries or adding indexes")
        if avg_total < 30:
            logger.success("  ✓ 性能良好，处理时间在可接受范围内")
            logger.success("  ✓ Good performance, processing time is acceptable")
        
        return times
        
    except Exception as e:
        logger.error(f"✗ 示例8执行失败 / Example 8 failed: {e}")
        return None


def main():
    """
    主函数：运行所有示例
    Main function: Run all examples
    """
    logger.info("\n" + "=" * 80)
    logger.info("决策分析模块使用示例")
    logger.info("Decision Analysis Module Usage Examples")
    logger.info("=" * 80)
    logger.info("")
    
    try:
        # 运行各个示例 / Run each example
        # 注意：某些示例可能需要较长时间，特别是涉及LLM调用的示例
        # Note: Some examples may take a long time, especially those involving LLM calls
        
        # 示例1: 基本使用 / Example 1: Basic Usage
        result1 = example_1_basic_usage()
        
        # 示例2: 指定分析时间 / Example 2: Specify Analysis DateTime
        result2 = example_2_with_specific_datetime()
        
        # 示例3: 错误处理 / Example 3: Error Handling
        result3 = example_3_error_handling()
        
        # 示例4: 输出格式演示 / Example 4: Output Format Demonstration
        result4 = example_4_output_formats()
        
        # 示例5: 保存结果到JSON文件 / Example 5: Save Results to JSON File
        result5 = example_5_save_to_json()
        
        # 示例6: 批量分析多个库房 / Example 6: Batch Analysis for Multiple Rooms
        # 注意：这个示例会分析4个库房，可能需要较长时间
        # Note: This example analyzes 4 rooms and may take a long time
        # result6 = example_6_multiple_rooms()
        
        # 示例7: 自定义配置 / Example 7: Custom Configuration
        result7 = example_7_custom_configuration()
        
        # 示例8: 性能监控 / Example 8: Performance Monitoring
        # 注意：这个示例会运行3次分析，可能需要较长时间
        # Note: This example runs 3 analyses and may take a long time
        # result8 = example_8_performance_monitoring()
        
        # 最终总结 / Final Summary
        logger.info("\n" + "=" * 80)
        logger.success("所有示例运行完成 / All examples completed")
        logger.info("=" * 80)
        logger.info("")
        logger.info("提示 / Tips:")
        logger.info("  • 取消注释示例6和示例8以运行批量分析和性能监控")
        logger.info("  • Uncomment examples 6 and 8 to run batch analysis and performance monitoring")
        logger.info("  • 查看生成的JSON文件以了解完整的输出格式")
        logger.info("  • Check the generated JSON file to understand the complete output format")
        logger.info("  • 使用 scripts/run_decision_analysis.py 进行生产环境的决策分析")
        logger.info("  • Use scripts/run_decision_analysis.py for production decision analysis")
        logger.info("")
        
    except KeyboardInterrupt:
        logger.warning("\n用户中断执行 / User interrupted execution")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"\n运行示例时发生异常 / Exception occurred while running examples: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
