"""
决策分析任务模块

负责蘑菇房的决策分析相关任务。

功能:
- 多图像综合分析
- 结构化参数调整建议
- 风险评估和优先级指导
- LLM提示和解析
    - 自动存储到数据库（静态配置 + 动态结果）
"""

import time
from datetime import datetime
from typing import Any, Dict

from global_const.const_config import (
    DECISION_ANALYSIS_EMBEDDING_SIM_WEIGHT,
    DECISION_ANALYSIS_ENABLE_SKILL_ENGINE,
    DECISION_ANALYSIS_ENABLE_SKILL_KB_PRIOR,
    DECISION_ANALYSIS_ENABLE_KB_HUMAN_PRIOR,
    DECISION_ANALYSIS_MAX_RETRIES,
    DECISION_ANALYSIS_MULTI_IMAGE_BOOST,
    DECISION_ANALYSIS_RETRY_DELAY,
    DECISION_ANALYSIS_SIMILAR_CASE_TOP_K,
    DECISION_ANALYSIS_ENV_SIM_WEIGHT,
    MUSHROOM_ROOM_IDS,
)
from global_const.global_const import ensure_src_path
from scripts.analysis.run_enhanced_decision_analysis import (
    execute_enhanced_decision_analysis,
)
from scripts.processing.build_control_knowledge_base import build_and_persist_cluster_kb
from utils.create_table import store_decision_analysis_results
from utils.loguru_setting import logger


def safe_decision_analysis_for_room(room_id: str) -> Dict[str, Any]:
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
    _ = task_id

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[DECISION_TASK] 开始执行决策分析任务: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries})"
            )
            start_time = datetime.now()

            ensure_src_path()

            # 执行决策分析（仅用于数据库存储，不生成JSON文件）
            analysis_datetime = datetime.now()
            result = execute_enhanced_decision_analysis(
                room_id=room_id,
                analysis_datetime=analysis_datetime,
                output_file=None,  # 不生成JSON文件
                verbose=False,
                output_format="both",  # 同时生成静态配置与动态结果
                similar_case_top_k=DECISION_ANALYSIS_SIMILAR_CASE_TOP_K,
                embedding_similarity_weight=DECISION_ANALYSIS_EMBEDDING_SIM_WEIGHT,
                env_similarity_weight=DECISION_ANALYSIS_ENV_SIM_WEIGHT,
                enable_kb_human_prior=DECISION_ANALYSIS_ENABLE_KB_HUMAN_PRIOR,
                multi_image_boost=DECISION_ANALYSIS_MULTI_IMAGE_BOOST,
                enable_skill_engine=DECISION_ANALYSIS_ENABLE_SKILL_ENGINE,
            )

            duration = (datetime.now() - start_time).total_seconds()

            # 记录执行结果
            if result.success:
                skill_enabled = bool(result.metadata.get("skill_enabled", False))
                skill_matched_count = int(result.metadata.get("skill_matched_count", 0))
                skill_constraint_corrections = int(
                    result.metadata.get("skill_constraint_corrections", 0)
                )

                logger.info(
                    f"[DECISION_TASK] 决策分析完成: 库房{room_id}, "
                    f"成功={result.success}, 多图像数量={result.metadata.get('multi_image_count', 0)}, 耗时={duration:.2f}秒"
                )
                logger.info(
                    "[DECISION_TASK] Skill反馈: 启用=%s, KB先验=%s, 命中=%s, 约束修正=%s, KB修正引用=%s"
                    % (
                        skill_enabled,
                        result.metadata.get("skill_kb_prior_enabled", False),
                        skill_matched_count,
                        skill_constraint_corrections,
                        result.metadata.get("skill_kb_prior_used", 0),
                    )
                )

                dynamic_results_count = 0
                change_count = 0

                # ==================== 新增：仅存储动态结果到数据库 ====================
                try:
                    logger.info(
                        f"[DECISION_TASK] 开始存储动态结果到数据库: 库房{room_id}"
                    )

                    ensure_src_path()

                    # 直接从result中获取数据，无需读取JSON文件
                    if hasattr(result, "data") and result.data:
                        json_data = result.data
                    else:
                        logger.warning(
                            f"[DECISION_TASK] 结果数据为空，跳过数据库存储: 库房{room_id}"
                        )
                        # 不使用continue，而是跳过存储但继续执行
                        json_data = None

                    if json_data:
                        storage_result = store_decision_analysis_results(
                            json_data=json_data,
                            room_id=room_id,
                            analysis_time=analysis_datetime,
                        )

                        logger.info(f"[DECISION_TASK] 动态结果存储完成: 库房{room_id}")
                        logger.info(
                            f"[DECISION_TASK]   - 批次ID: {storage_result.get('batch_id')}"
                        )
                        logger.info(
                            f"[DECISION_TASK]   - 静态配置: {storage_result.get('static_configs_stored', 0)}条"
                        )
                        logger.info(
                            f"[DECISION_TASK]   - 动态结果: {storage_result.get('dynamic_results_count', 0)}条"
                        )
                        logger.info(
                            f"[DECISION_TASK]   - 变更记录: {storage_result.get('change_count', 0)}条"
                        )
                        logger.info(
                            f"[DECISION_TASK]   - Skill审计: {storage_result.get('skill_audit_count', 0)}条"
                        )
                        dynamic_results_count = int(
                            storage_result.get("dynamic_results_count", 0)
                        )
                        change_count = int(storage_result.get("change_count", 0))

                except Exception as storage_error:
                    # 数据库存储失败不影响分析任务的成功状态
                    logger.error(
                        f"[DECISION_TASK] 动态结果存储失败: 库房{room_id}, 错误={storage_error}"
                    )
                    logger.warning(
                        "[DECISION_TASK] 分析任务成功但数据库存储失败，请检查数据库连接"
                    )
                # ==================== 动态结果存储结束 ====================

                if result.warnings:
                    logger.warning(
                        f"[DECISION_TASK] 库房{room_id}分析警告: {len(result.warnings)}条"
                    )
                    for warning in result.warnings[:3]:  # 只显示前3条警告
                        logger.warning(f"[DECISION_TASK]   - {warning}")

                # 成功执行，退出重试循环
                return {
                    "success": True,
                    "duration": duration,
                    "skill_enabled": skill_enabled,
                    "skill_matched_count": skill_matched_count,
                    "skill_constraint_corrections": skill_constraint_corrections,
                    "skill_kb_prior_used": int(
                        result.metadata.get("skill_kb_prior_used", 0)
                    ),
                    "warnings_count": len(result.warnings or []),
                    "dynamic_results_count": dynamic_results_count,
                    "change_count": change_count,
                }

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
                    for keyword in [
                        "timeout",
                        "connection",
                        "connect",
                        "database",
                        "server",
                    ]
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
                    return {
                        "success": False,
                        "duration": duration,
                        "error": error_msg,
                    }
                else:
                    # 非连接错误，不重试
                    logger.error(
                        f"[DECISION_TASK] 库房{room_id}决策分析遇到非连接错误，不再重试"
                    )
                    return {
                        "success": False,
                        "duration": duration,
                        "error": error_msg,
                    }

        except ImportError as e:
            logger.error(f"[DECISION_TASK] 导入决策分析模块失败: {e}")
            # 导入错误不重试
            return {
                "success": False,
                "duration": 0.0,
                "error": str(e),
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[DECISION_TASK] 决策分析异常: 库房{room_id} "
                f"(尝试 {attempt}/{max_retries}): {error_msg}"
            )

            is_connection_error = any(
                keyword in error_msg.lower()
                for keyword in [
                    "timeout",
                    "connection",
                    "connect",
                    "database",
                    "server",
                ]
            )

            if is_connection_error and attempt < max_retries:
                logger.warning(
                    f"[DECISION_TASK] 检测到连接错误，{retry_delay}秒后重试..."
                )
                time.sleep(retry_delay)
            elif attempt >= max_retries:
                logger.error(
                    f"[DECISION_TASK] 库房{room_id}决策分析失败，"
                    f"已达到最大重试次数 ({max_retries})"
                )
                return {
                    "success": False,
                    "duration": 0.0,
                    "error": error_msg,
                }
            else:
                logger.error("[DECISION_TASK] 决策分析遇到非连接错误，不再重试")
                return {
                    "success": False,
                    "duration": 0.0,
                    "error": error_msg,
                }


def safe_batch_decision_analysis(
    schedule_hour: int = None, schedule_minute: int = None
) -> None:
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
    from utils.task_common import check_database_connection

    if not check_database_connection():
        error_msg = "[DECISION_TASK] 数据库不可达，任务终止（按配置不启用容错）"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # 如果没有提供时间参数，使用当前时间
    if schedule_hour is None or schedule_minute is None:
        current_time = datetime.now()
        schedule_hour = current_time.hour
        schedule_minute = current_time.minute

    logger.info("[DECISION_TASK] ==========================================")
    logger.info(
        f"[DECISION_TASK] 开始批量决策分析任务 (执行时间: {schedule_hour:02d}:{schedule_minute:02d})"
    )
    logger.info(f"[DECISION_TASK] 待分析库房: {MUSHROOM_ROOM_IDS}")
    logger.info(
        "[DECISION_TASK] 功能: 多图像分析, 结构化参数调整, 风险评估, 仅动态结果存储（优化版）"
    )
    logger.info(
        "[DECISION_TASK] 策略: KB人工偏好优先=%s, 相似案例top_k=%s, embedding权重=%.2f, env权重=%.2f, multi_image_boost=%s, skill_engine=%s"
        % (
            DECISION_ANALYSIS_ENABLE_KB_HUMAN_PRIOR,
            DECISION_ANALYSIS_SIMILAR_CASE_TOP_K,
            DECISION_ANALYSIS_EMBEDDING_SIM_WEIGHT,
            DECISION_ANALYSIS_ENV_SIM_WEIGHT,
            DECISION_ANALYSIS_MULTI_IMAGE_BOOST,
            DECISION_ANALYSIS_ENABLE_SKILL_ENGINE,
        )
    )
    logger.info(
        f"[DECISION_TASK] Skill策略: 启用KB先验={DECISION_ANALYSIS_ENABLE_SKILL_KB_PRIOR}"
    )
    logger.info("[DECISION_TASK] ==========================================")

    batch_start_time = datetime.now()
    results: Dict[str, Dict[str, Any]] = {}

    for room_id in MUSHROOM_ROOM_IDS:
        room_start_time = datetime.now()

        try:
            room_result = safe_decision_analysis_for_room(room_id)
            results[room_id] = {
                "status": "success" if room_result.get("success") else "failed",
                "duration": (datetime.now() - room_start_time).total_seconds(),
                "skill_enabled": bool(room_result.get("skill_enabled", False)),
                "skill_matched_count": int(room_result.get("skill_matched_count", 0)),
                "skill_constraint_corrections": int(
                    room_result.get("skill_constraint_corrections", 0)
                ),
                "skill_kb_prior_used": int(room_result.get("skill_kb_prior_used", 0)),
                "warnings_count": int(room_result.get("warnings_count", 0)),
                "dynamic_results_count": int(room_result.get("dynamic_results_count", 0)),
                "change_count": int(room_result.get("change_count", 0)),
                "error": room_result.get("error"),
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
    skill_enabled_rooms = sum(
        1 for r in results.values() if r.get("status") == "success" and r.get("skill_enabled")
    )
    skill_hit_rooms = sum(
        1
        for r in results.values()
        if r.get("status") == "success" and int(r.get("skill_matched_count", 0)) > 0
    )
    skill_total_matched = sum(int(r.get("skill_matched_count", 0)) for r in results.values())
    skill_total_corrections = sum(
        int(r.get("skill_constraint_corrections", 0)) for r in results.values()
    )
    skill_total_kb_prior_used = sum(
        int(r.get("skill_kb_prior_used", 0)) for r in results.values()
    )
    total_dynamic_results = sum(int(r.get("dynamic_results_count", 0)) for r in results.values())
    total_change_count = sum(int(r.get("change_count", 0)) for r in results.values())

    logger.info("[DECISION_TASK] ==========================================")
    logger.info("[DECISION_TASK] 批量决策分析完成")
    logger.info(
        f"[DECISION_TASK] 成功: {success_count}/{len(MUSHROOM_ROOM_IDS)}, "
        f"失败: {failed_count}/{len(MUSHROOM_ROOM_IDS)}"
    )
    logger.info(f"[DECISION_TASK] 总耗时: {batch_duration:.2f}秒")

    for room_id, result in results.items():
        status_icon = "✓" if result["status"] == "success" else "✗"
        skill_summary = (
            f"skill_hit={result.get('skill_matched_count', 0)}, "
            f"skill_fix={result.get('skill_constraint_corrections', 0)}, "
            f"kb_ref={result.get('skill_kb_prior_used', 0)}, "
            f"dynamic={result.get('dynamic_results_count', 0)}"
        )
        logger.info(
            f"[DECISION_TASK]   库房{room_id}: [{status_icon}] {result['duration']:.2f}秒, {skill_summary}"
        )

    # 数据库存储统计（如果有成功的分析）
    if success_count > 0:
        logger.info(
            f"[DECISION_TASK] 数据库存储: {success_count}个库房的动态结果已自动存储"
        )
        logger.info("[DECISION_TASK] 存储内容: 静态配置表 + 动态结果表")
        logger.info(
            f"[DECISION_TASK] 动态结果汇总: dynamic_results={total_dynamic_results}, changes={total_change_count}"
        )

    if skill_enabled_rooms > 0:
        skill_hit_rate = skill_hit_rooms / skill_enabled_rooms
        correction_rate_by_hit = (
            skill_total_corrections / skill_hit_rooms if skill_hit_rooms > 0 else 0.0
        )
        logger.info(
            "[DECISION_TASK] Skill汇总: 启用库房=%s, 命中库房=%s, 命中率=%.2f, 匹配总数=%s, 修正总数=%s, 平均每命中修正=%.2f"
            % (
                skill_enabled_rooms,
                skill_hit_rooms,
                skill_hit_rate,
                skill_total_matched,
                skill_total_corrections,
                correction_rate_by_hit,
            )
        )
        logger.info(
            f"[DECISION_TASK] Skill-KB汇总: KB先验引用次数={skill_total_kb_prior_used}"
        )

    logger.info("[DECISION_TASK] ==========================================")


def safe_refresh_control_strategy_cluster_kb(
    interval_days: int = 27,
    min_samples_per_point: int = 12,
) -> None:
    """定时刷新聚类控制知识库（默认27天间隔，结果直接落库）。"""
    logger.info("[CONTROL_KB_TASK] ==========================================")
    logger.info(
        f"[CONTROL_KB_TASK] 开始执行聚类知识库刷新任务: interval_days={interval_days}, "
        f"min_samples_per_point={min_samples_per_point}"
    )

    start_time = datetime.now()
    try:
        result = build_and_persist_cluster_kb(
            room_ids=MUSHROOM_ROOM_IDS,
            min_samples_per_point=int(min_samples_per_point),
            interval_days=int(interval_days),
            force_run=False,
            persist=True,
            export_csv=False,
        )

        duration = (datetime.now() - start_time).total_seconds()
        if result.get("skipped"):
            logger.info(
                "[CONTROL_KB_TASK] 跳过执行：未达到间隔要求，"
                f"last={result.get('last_generated_at')}, next={result.get('next_due_at')}"
            )
            logger.info(f"[CONTROL_KB_TASK] 任务结束（跳过），耗时={duration:.2f}秒")
            logger.info("[CONTROL_KB_TASK] ==========================================")
            return

        persist_stats = result.get("persist_stats") or {}
        logger.info(
            "[CONTROL_KB_TASK] 刷新完成: "
            f"run_id={persist_stats.get('run_id')}, "
            f"cluster_meta={persist_stats.get('cluster_meta_count', 0)}, "
            f"cluster_rules={persist_stats.get('cluster_rule_count', 0)}, "
            f"耗时={duration:.2f}秒"
        )
        logger.info("[CONTROL_KB_TASK] ==========================================")
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(f"[CONTROL_KB_TASK] 刷新失败: {e}", exc_info=True)
        logger.error(f"[CONTROL_KB_TASK] 失败耗时={duration:.2f}秒")
        logger.info("[CONTROL_KB_TASK] ==========================================")
