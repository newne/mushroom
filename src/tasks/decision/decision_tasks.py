"""
决策分析任务模块

负责蘑菇房的决策分析相关任务。

功能:
- 多图像综合分析
- 结构化参数调整建议
- 风险评估和优先级指导
- LLM提示和解析
- 自动存储到数据库（仅动态结果表，优化版）
"""

import sys
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    DECISION_ANALYSIS_MAX_RETRIES,
    DECISION_ANALYSIS_RETRY_DELAY,
)
from utils.loguru_setting import logger


def safe_decision_analysis_for_room(room_id: str) -> None:
    """
    执行单个蘑菇房的决策分析任务（优化版：仅存储动态结果）
    
    此函数作为定时任务的入口点，为指定的蘑菇房执行决策分析。
    包含完整的错误处理和重试机制，确保单个任务失败不会影响调度器。
    
    优化特性:
    - 不生成JSON文件，减少磁盘I/O开销
    - 仅存储动态结果表，静态配置相对固定无需重复存储
    - 直接从内存获取分析数据，提高处理效率
    
    功能:
    - 多图像聚合和分析
    - 结构化参数调整建议 (maintain/adjust/monitor)
    - 风险评估和优先级指导
    - LLM提示和解析
    - 仅存储动态结果到数据库
    
    Args:
        room_id: 蘑菇房编号（"607", "608", "611", "612"）
    """
    max_retries = DECISION_ANALYSIS_MAX_RETRIES
    retry_delay = DECISION_ANALYSIS_RETRY_DELAY
    
    task_id = f"decision_analysis_{room_id}"
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[DECISION_TASK] 开始执行决策分析任务: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries})"
            )
            start_time = datetime.now()
            
            # 确保 scripts 目录在 path 中
            scripts_path = Path(__file__).parent.parent.parent / "scripts" / "analysis"
            if str(scripts_path) not in sys.path:
                sys.path.insert(0, str(scripts_path))
            
            from run_enhanced_decision_analysis import execute_enhanced_decision_analysis
            
            # 执行决策分析（仅用于数据库存储，不生成JSON文件）
            analysis_datetime = datetime.now()
            result = execute_enhanced_decision_analysis(
                room_id=room_id,
                analysis_datetime=analysis_datetime,
                output_file=None,  # 不生成JSON文件
                verbose=False,
                output_format="monitoring"  # 仅生成监控点格式用于动态结果存储
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # 记录执行结果
            if result.success:
                logger.info(
                    f"[DECISION_TASK] 决策分析完成: 库房{room_id}, "
                    f"成功={result.success}, 多图像数量={result.metadata.get('multi_image_count', 0)}, 耗时={duration:.2f}秒"
                )
                
                # ==================== 新增：仅存储动态结果到数据库 ====================
                try:
                    logger.info(f"[DECISION_TASK] 开始存储动态结果到数据库: 库房{room_id}")
                    
                    # 导入存储函数
                    sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
                    from utils.create_table import store_decision_analysis_dynamic_results_only
                    
                    # 直接从result中获取数据，无需读取JSON文件
                    if hasattr(result, 'data') and result.data:
                        json_data = result.data
                    else:
                        logger.warning(f"[DECISION_TASK] 结果数据为空，跳过数据库存储: 库房{room_id}")
                        # 不使用continue，而是跳过存储但继续执行
                        json_data = None
                    
                    if json_data:
                        # 仅存储动态结果到数据库
                        storage_result = store_decision_analysis_dynamic_results_only(
                            json_data=json_data,
                            room_id=room_id,
                            analysis_time=analysis_datetime
                        )
                        
                        logger.info(f"[DECISION_TASK] 动态结果存储完成: 库房{room_id}")
                        logger.info(f"[DECISION_TASK]   - 批次ID: {storage_result.get('batch_id')}")
                        logger.info(f"[DECISION_TASK]   - 动态结果: {storage_result.get('dynamic_results_count', 0)}条")
                        logger.info(f"[DECISION_TASK]   - 变更记录: {storage_result.get('change_count', 0)}条")
                    
                except Exception as storage_error:
                    # 数据库存储失败不影响分析任务的成功状态
                    logger.error(f"[DECISION_TASK] 动态结果存储失败: 库房{room_id}, 错误={storage_error}")
                    logger.warning(f"[DECISION_TASK] 分析任务成功但数据库存储失败，请检查数据库连接")
                # ==================== 动态结果存储结束 ====================
                
                if result.warnings:
                    logger.warning(
                        f"[DECISION_TASK] 库房{room_id}分析警告: {len(result.warnings)}条"
                    )
                    for warning in result.warnings[:3]:  # 只显示前3条警告
                        logger.warning(f"[DECISION_TASK]   - {warning}")
                
                # 成功执行，退出重试循环
                return
                
            else:
                # 分析执行但有错误
                error_msg = result.error_message or "未知错误"
                logger.error(
                    f"[DECISION_TASK] 决策分析失败: 库房{room_id}, "
                    f"错误={error_msg}, 耗时={duration:.2f}秒"
                )
                
                # 判断是否需要重试
                is_connection_error = any(
                    keyword in error_msg.lower() 
                    for keyword in ['timeout', 'connection', 'connect', 'database', 'server']
                )
                
                if is_connection_error and attempt < max_retries:
                    logger.warning(
                        f"[DECISION_TASK] 检测到连接错误，{retry_delay}秒后重试..."
                    )
                    time.sleep(retry_delay)
                    continue
                elif attempt >= max_retries:
                    logger.error(
                        f"[DECISION_TASK] 库房{room_id}决策分析失败，"
                        f"已达到最大重试次数 ({max_retries})"
                    )
                    return
                else:
                    # 非连接错误，不重试
                    logger.error(
                        f"[DECISION_TASK] 库房{room_id}决策分析遇到非连接错误，不再重试"
                    )
                    return
                    
        except ImportError as e:
            logger.error(f"[DECISION_TASK] 导入决策分析模块失败: {e}")
            # 导入错误不重试
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[DECISION_TASK] 决策分析异常: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries}): {error_msg}"
            )
            
            is_connection_error = any(
                keyword in error_msg.lower() 
                for keyword in ['timeout', 'connection', 'connect', 'database', 'server']
            )
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[DECISION_TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(
                    f"[DECISION_TASK] 库房{room_id}决策分析失败，"
                    f"已达到最大重试次数 ({max_retries})"
                )
                return
            else:
                logger.error(f"[DECISION_TASK] 决策分析遇到非连接错误，不再重试")
                return


def safe_batch_decision_analysis(schedule_hour: int = None, schedule_minute: int = None) -> None:
    """
    批量执行所有蘑菇房的决策分析任务（优化版：仅存储动态结果）
    
    此函数按顺序为所有蘑菇房执行决策分析，确保即使某个房间失败也不会影响其他房间。
    每次分析完成后仅存储动态结果到数据库（静态配置相对固定，无需重复存储）。
    
    优化特性:
    - 不生成JSON文件，减少磁盘I/O
    - 仅存储动态结果表，提高存储效率
    - 直接从内存获取分析数据，减少文件读写
    
    功能:
    - 多图像综合分析
    - 结构化参数调整建议
    - 风险评估和优先级指导
    - 仅存储动态结果到数据库
    - 详细的执行统计和报告
    
    Args:
        schedule_hour: 计划执行的小时（可选，用于日志记录）
        schedule_minute: 计划执行的分钟（可选，用于日志记录）
    """
    # 如果没有提供时间参数，使用当前时间
    if schedule_hour is None or schedule_minute is None:
        current_time = datetime.now()
        schedule_hour = current_time.hour
        schedule_minute = current_time.minute
    
    logger.info(
        f"[DECISION_TASK] =========================================="
    )
    logger.info(
        f"[DECISION_TASK] 开始批量决策分析任务 (执行时间: {schedule_hour:02d}:{schedule_minute:02d})"
    )
    logger.info(
        f"[DECISION_TASK] 待分析库房: {MUSHROOM_ROOM_IDS}"
    )
    logger.info(
        f"[DECISION_TASK] 功能: 多图像分析, 结构化参数调整, 风险评估, 仅动态结果存储（优化版）"
    )
    logger.info(
        f"[DECISION_TASK] =========================================="
    )
    
    batch_start_time = datetime.now()
    results: Dict[str, Dict[str, Any]] = {}
    
    for room_id in MUSHROOM_ROOM_IDS:
        room_start_time = datetime.now()
        
        try:
            safe_decision_analysis_for_room(room_id)
            results[room_id] = {
                "status": "success",
                "duration": (datetime.now() - room_start_time).total_seconds(),
            }
        except Exception as e:
            results[room_id] = {
                "status": "failed",
                "error": str(e),
                "duration": (datetime.now() - room_start_time).total_seconds(),
            }
            logger.error(f"[DECISION_TASK] 库房{room_id}分析异常: {e}")
    
    # 汇总报告
    batch_duration = (datetime.now() - batch_start_time).total_seconds()
    success_count = sum(1 for r in results.values() if r["status"] == "success")
    failed_count = len(results) - success_count
    
    logger.info(
        f"[DECISION_TASK] =========================================="
    )
    logger.info(
        f"[DECISION_TASK] 批量决策分析完成"
    )
    logger.info(
        f"[DECISION_TASK] 成功: {success_count}/{len(MUSHROOM_ROOM_IDS)}, "
        f"失败: {failed_count}/{len(MUSHROOM_ROOM_IDS)}"
    )
    logger.info(
        f"[DECISION_TASK] 总耗时: {batch_duration:.2f}秒"
    )
    
    for room_id, result in results.items():
        status_icon = "✓" if result["status"] == "success" else "✗"
        logger.info(
            f"[DECISION_TASK]   库房{room_id}: [{status_icon}] {result['duration']:.2f}秒"
        )
    
    # 数据库存储统计（如果有成功的分析）
    if success_count > 0:
        logger.info(f"[DECISION_TASK] 数据库存储: {success_count}个库房的动态结果已自动存储")
        logger.info(f"[DECISION_TASK] 存储内容: 仅动态结果表（静态配置已优化跳过）")
    
    logger.info(
        f"[DECISION_TASK] =========================================="
    )

