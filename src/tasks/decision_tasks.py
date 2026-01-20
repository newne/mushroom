"""
决策分析任务模块
负责蘑菇房的决策分析相关任务

增强功能:
- 多图像综合分析
- 结构化参数调整建议
- 风险评估和优先级指导
- 增强的LLM提示和解析
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from global_const.const_config import (
    MUSHROOM_ROOM_IDS,
    DECISION_ANALYSIS_MAX_RETRIES,
    DECISION_ANALYSIS_RETRY_DELAY,
)
from utils.loguru_setting import logger


def safe_enhanced_decision_analysis_for_room(room_id: str) -> None:
    """
    执行单个蘑菇房的增强决策分析任务（带重试机制）
    
    此函数作为定时任务的入口点，为指定的蘑菇房执行增强决策分析。
    包含完整的错误处理和重试机制，确保单个任务失败不会影响调度器。
    
    增强功能:
    - 多图像聚合和分析
    - 结构化参数调整建议 (maintain/adjust/monitor)
    - 风险评估和优先级指导
    - 增强的LLM提示和解析
    
    Args:
        room_id: 蘑菇房编号（"607", "608", "611", "612"）
    """
    max_retries = DECISION_ANALYSIS_MAX_RETRIES
    retry_delay = DECISION_ANALYSIS_RETRY_DELAY
    
    task_id = f"enhanced_decision_analysis_{room_id}"
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[ENHANCED_DECISION_TASK] 开始执行增强决策分析任务: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries})"
            )
            start_time = datetime.now()
            
            # 确保 scripts 目录在 path 中
            scripts_path = Path(__file__).parent.parent.parent / "scripts" / "analysis"
            if str(scripts_path) not in sys.path:
                sys.path.insert(0, str(scripts_path))
            
            from run_enhanced_decision_analysis import execute_enhanced_decision_analysis
            
            # 执行增强决策分析
            analysis_datetime = datetime.now()
            result = execute_enhanced_decision_analysis(
                room_id=room_id,
                analysis_datetime=analysis_datetime,
                output_file=None,  # 使用默认路径
                verbose=False
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # 记录执行结果
            if result.success:
                logger.info(
                    f"[ENHANCED_DECISION_TASK] 增强决策分析完成: 库房{room_id}, "
                    f"状态={result.status}, 多图像数量={result.multi_image_count}, 耗时={duration:.2f}秒"
                )
                if result.output_file:
                    logger.info(f"[ENHANCED_DECISION_TASK] 输出文件: {result.output_file}")
                
                if result.enhanced_features_used:
                    logger.info(f"[ENHANCED_DECISION_TASK] 使用的增强功能: {', '.join(result.enhanced_features_used)}")
                
                if result.warnings:
                    logger.warning(
                        f"[ENHANCED_DECISION_TASK] 库房{room_id}分析警告: {len(result.warnings)}条"
                    )
                    for warning in result.warnings[:3]:  # 只显示前3条警告
                        logger.warning(f"[ENHANCED_DECISION_TASK]   - {warning}")
                
                # 成功执行，退出重试循环
                return
                
            else:
                # 分析执行但有错误
                error_msg = result.error_message or "未知错误"
                logger.error(
                    f"[ENHANCED_DECISION_TASK] 增强决策分析失败: 库房{room_id}, "
                    f"错误={error_msg}, 耗时={duration:.2f}秒"
                )
                
                # 判断是否需要重试
                is_connection_error = any(
                    keyword in error_msg.lower() 
                    for keyword in ['timeout', 'connection', 'connect', 'database', 'server']
                )
                
                if is_connection_error and attempt < max_retries:
                    logger.warning(
                        f"[ENHANCED_DECISION_TASK] 检测到连接错误，{retry_delay}秒后重试..."
                    )
                    time.sleep(retry_delay)
                    continue
                elif attempt >= max_retries:
                    logger.error(
                        f"[ENHANCED_DECISION_TASK] 库房{room_id}增强决策分析失败，"
                        f"已达到最大重试次数 ({max_retries})"
                    )
                    return
                else:
                    # 非连接错误，不重试
                    logger.error(
                        f"[ENHANCED_DECISION_TASK] 库房{room_id}增强决策分析遇到非连接错误，不再重试"
                    )
                    return
                    
        except ImportError as e:
            logger.error(f"[ENHANCED_DECISION_TASK] 导入增强决策分析模块失败: {e}")
            # 导入错误不重试
            return
            
        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[ENHANCED_DECISION_TASK] 增强决策分析异常: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries}): {error_msg}"
            )
            
            is_connection_error = any(
                keyword in error_msg.lower() 
                for keyword in ['timeout', 'connection', 'connect', 'database', 'server']
            )
            
            if is_connection_error and attempt < max_retries:
                logger.warning(f"[ENHANCED_DECISION_TASK] 检测到连接错误，{retry_delay}秒后重试...")
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(
                    f"[ENHANCED_DECISION_TASK] 库房{room_id}增强决策分析失败，"
                    f"已达到最大重试次数 ({max_retries})"
                )
                return
            else:
                logger.error(f"[ENHANCED_DECISION_TASK] 增强决策分析遇到非连接错误，不再重试")
                return


def safe_decision_analysis_for_room(room_id: str) -> None:
    """
    执行单个蘑菇房的决策分析任务（带重试机制）
    
    此函数作为定时任务的入口点，为指定的蘑菇房执行决策分析。
    包含完整的错误处理和重试机制，确保单个任务失败不会影响调度器。
    
    注意: 此函数保留用于向后兼容，建议使用 safe_enhanced_decision_analysis_for_room
    
    Args:
        room_id: 蘑菇房编号（"607", "608", "611", "612"）
    """
    logger.warning(
        f"[DECISION_TASK] 使用传统决策分析方法，建议升级到增强版本: 库房{room_id}"
    )
    
    # 直接调用增强版本
    safe_enhanced_decision_analysis_for_room(room_id)


def safe_enhanced_batch_decision_analysis(schedule_hour: int, schedule_minute: int) -> None:
    """
    批量执行所有蘑菇房的增强决策分析任务
    
    此函数按顺序为所有蘑菇房执行增强决策分析，确保即使某个房间失败也不会影响其他房间。
    
    增强功能:
    - 多图像综合分析
    - 结构化参数调整建议
    - 风险评估和优先级指导
    - 详细的执行统计和报告
    
    Args:
        schedule_hour: 计划执行的小时
        schedule_minute: 计划执行的分钟
    """
    logger.info(
        f"[ENHANCED_DECISION_TASK] =========================================="
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 开始批量增强决策分析任务 (计划时间: {schedule_hour:02d}:{schedule_minute:02d})"
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 待分析库房: {MUSHROOM_ROOM_IDS}"
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 增强功能: 多图像分析, 结构化参数调整, 风险评估"
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] =========================================="
    )
    
    batch_start_time = datetime.now()
    results: Dict[str, Dict[str, Any]] = {}
    
    for room_id in MUSHROOM_ROOM_IDS:
        room_start_time = datetime.now()
        
        try:
            safe_enhanced_decision_analysis_for_room(room_id)
            results[room_id] = {
                "status": "success",
                "duration": (datetime.now() - room_start_time).total_seconds(),
                "enhanced": True
            }
        except Exception as e:
            results[room_id] = {
                "status": "failed",
                "error": str(e),
                "duration": (datetime.now() - room_start_time).total_seconds(),
                "enhanced": False
            }
            logger.error(f"[ENHANCED_DECISION_TASK] 库房{room_id}增强分析异常: {e}")
    
    # 汇总报告
    batch_duration = (datetime.now() - batch_start_time).total_seconds()
    success_count = sum(1 for r in results.values() if r["status"] == "success")
    failed_count = len(results) - success_count
    enhanced_count = sum(1 for r in results.values() if r.get("enhanced", False))
    
    logger.info(
        f"[ENHANCED_DECISION_TASK] =========================================="
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 批量增强决策分析完成"
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 成功: {success_count}/{len(MUSHROOM_ROOM_IDS)}, "
        f"失败: {failed_count}/{len(MUSHROOM_ROOM_IDS)}, "
        f"增强功能: {enhanced_count}/{len(MUSHROOM_ROOM_IDS)}"
    )
    logger.info(
        f"[ENHANCED_DECISION_TASK] 总耗时: {batch_duration:.2f}秒"
    )
    
    for room_id, result in results.items():
        status_icon = "✓" if result["status"] == "success" else "✗"
        enhanced_icon = "🔧" if result.get("enhanced", False) else "📊"
        logger.info(
            f"[ENHANCED_DECISION_TASK]   库房{room_id}: [{status_icon}] {enhanced_icon} {result['duration']:.2f}秒"
        )
    
    logger.info(
        f"[ENHANCED_DECISION_TASK] =========================================="
    )


def safe_batch_decision_analysis(schedule_hour: int, schedule_minute: int) -> None:
    """
    批量执行所有蘑菇房的决策分析任务
    
    此函数按顺序为所有蘑菇房执行决策分析，确保即使某个房间失败也不会影响其他房间。
    
    注意: 此函数保留用于向后兼容，建议使用 safe_enhanced_batch_decision_analysis
    
    Args:
        schedule_hour: 计划执行的小时
        schedule_minute: 计划执行的分钟
    """
    logger.warning(
        f"[DECISION_TASK] 使用传统批量决策分析方法，建议升级到增强版本"
    )
    
    # 直接调用增强版本
    safe_enhanced_batch_decision_analysis(schedule_hour, schedule_minute)


# 为每个时间点创建独立的增强任务函数（避免闭包序列化问题）
def safe_enhanced_decision_analysis_10_00() -> None:
    """10:00 增强决策分析批量任务"""
    safe_enhanced_batch_decision_analysis(10, 0)


def safe_enhanced_decision_analysis_12_00() -> None:
    """12:00 增强决策分析批量任务"""
    safe_enhanced_batch_decision_analysis(12, 0)


def safe_enhanced_decision_analysis_14_00() -> None:
    """14:00 增强决策分析批量任务"""
    safe_enhanced_batch_decision_analysis(14, 0)


# 保留传统任务函数用于向后兼容
def safe_decision_analysis_10_00() -> None:
    """10:00 决策分析批量任务（传统版本，建议使用增强版本）"""
    logger.warning("[DECISION_TASK] 使用传统决策分析任务，建议升级到增强版本")
    safe_enhanced_decision_analysis_10_00()


def safe_decision_analysis_12_00() -> None:
    """12:00 决策分析批量任务（传统版本，建议使用增强版本）"""
    logger.warning("[DECISION_TASK] 使用传统决策分析任务，建议升级到增强版本")
    safe_enhanced_decision_analysis_12_00()


def safe_decision_analysis_14_00() -> None:
    """14:00 决策分析批量任务（传统版本，建议使用增强版本）"""
    logger.warning("[DECISION_TASK] 使用传统决策分析任务，建议升级到增强版本")
    safe_enhanced_decision_analysis_14_00()