"""决策分析任务执行器。"""

from datetime import datetime, timedelta
from typing import Any, Dict

from global_const.const_config import (
    DECISION_ANALYSIS_MAX_RETRIES,
    DECISION_ANALYSIS_RETRY_DELAY,
    MUSHROOM_ROOM_IDS,
)
from global_const.global_const import ensure_src_path
from scripts.analysis.run_enhanced_decision_analysis import (
    execute_enhanced_decision_analysis,
)
from tasks.base_task import BaseTask
from utils.create_table import store_decision_analysis_results
from utils.task_logging import log_task_event


def _decision_log(
    event: str, message: str, level: str = "INFO", **context: Any
) -> None:
    """输出统一的决策任务事件日志。"""
    log_task_event("DECISION_ANALYSIS", event, message, level=level, **context)


class DecisionAnalysisTask(BaseTask):
    """决策分析任务执行器"""

    def __init__(self):
        """初始化决策分析任务"""
        super().__init__(
            task_name="DECISION_ANALYSIS",
            max_retries=DECISION_ANALYSIS_MAX_RETRIES,
            retry_delay=DECISION_ANALYSIS_RETRY_DELAY,
        )

        self.rooms = MUSHROOM_ROOM_IDS

    def execute_single_room_analysis(self, room_id: str) -> Dict[str, Any]:
        """
        执行单个蘑菇房的决策分析任务（优化版：仅存储动态结果）

        Args:
            room_id: 蘑菇房编号

        Returns:
            Dict[str, Any]: 分析结果
        """
        _decision_log(
            "DECISION_ROOM_START",
            "开始执行单库房决策分析",
            room_id=room_id,
            status="running",
        )

        try:
            ensure_src_path()

            # 执行决策分析（仅用于数据库存储，不生成JSON文件）
            analysis_datetime = datetime.now()
            result = execute_enhanced_decision_analysis(
                room_id=room_id,
                analysis_datetime=analysis_datetime,
                output_file=None,  # 不生成JSON文件
                verbose=False,
                output_format="both",  # 同时生成静态配置与动态结果
            )

            # 记录执行结果
            if result.success:
                _decision_log(
                    "DECISION_ROOM_FINISH",
                    "单库房决策分析完成",
                    room_id=room_id,
                    status="success",
                    successful_items=1,
                    total_items=1,
                    multi_image_count=result.metadata.get("multi_image_count", 0),
                )

                # 存储动态结果到数据库
                storage_result = self._store_dynamic_results(
                    result, room_id, analysis_datetime
                )

                return self._create_success_result(
                    room_id=room_id,
                    analysis_datetime=analysis_datetime.isoformat(),
                    multi_image_count=result.metadata.get("multi_image_count", 0),
                    storage_result=storage_result,
                    warnings=result.warnings if result.warnings else [],
                )
            else:
                # 分析执行但有错误
                error_msg = result.error_message or "未知错误"
                _decision_log(
                    "DECISION_ROOM_FAILED",
                    "单库房决策分析失败",
                    level="ERROR",
                    room_id=room_id,
                    status="failed",
                    error_message=error_msg,
                )

                return {
                    "success": False,
                    "room_id": room_id,
                    "error": error_msg,
                    "analysis_datetime": analysis_datetime.isoformat(),
                }

        except ImportError as e:
            _decision_log(
                "DECISION_IMPORT_FAILED",
                "导入决策分析模块失败",
                level="ERROR",
                room_id=room_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "room_id": room_id, "error": f"导入模块失败: {e}"}

        except Exception as e:
            _decision_log(
                "DECISION_ROOM_EXCEPTION",
                "单库房决策分析异常",
                level="ERROR",
                room_id=room_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"success": False, "room_id": room_id, "error": str(e)}

    def execute_task(self) -> Dict[str, Any]:
        """
        执行批量决策分析任务（所有蘑菇房）

        Returns:
            Dict[str, Any]: 批量分析结果
        """
        _decision_log(
            "DECISION_BATCH_START",
            "开始批量决策分析任务",
            status="running",
            room_ids=list(self.rooms),
            total_items=len(self.rooms),
            output_scope="dynamic_results_only",
        )

        batch_start_time = datetime.now()
        results = {}

        for room_id in self.rooms:
            room_start_time = datetime.now()

            try:
                room_result = self.execute_single_room_analysis(room_id)
                room_result["duration"] = (
                    datetime.now() - room_start_time
                ).total_seconds()
                results[room_id] = room_result

            except Exception as e:
                results[room_id] = {
                    "success": False,
                    "error": str(e),
                    "duration": (datetime.now() - room_start_time).total_seconds(),
                }
                _decision_log(
                    "DECISION_BATCH_ROOM_EXCEPTION",
                    "批量任务中的库房分析异常",
                    level="ERROR",
                    room_id=room_id,
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        # 汇总报告
        batch_duration = (datetime.now() - batch_start_time).total_seconds()
        success_count = sum(1 for r in results.values() if r.get("success", False))
        failed_count = len(results) - success_count

        _decision_log(
            "DECISION_BATCH_FINISH",
            "批量决策分析完成",
            status="success" if failed_count == 0 else "partial",
            total_items=len(self.rooms),
            successful_items=success_count,
            failed_items=failed_count,
            success_rate=round((success_count / len(self.rooms)) * 100, 2)
            if self.rooms
            else 0,
            duration_ms=round(batch_duration * 1000, 2),
        )

        for room_id, result in results.items():
            _decision_log(
                "DECISION_BATCH_ROOM_SUMMARY",
                "库房决策分析结果",
                room_id=room_id,
                status="success" if result.get("success", False) else "failed",
                duration_ms=round(result.get("duration", 0) * 1000, 2),
                error_message=result.get("error"),
            )

        # 数据库存储统计（如果有成功的分析）
        if success_count > 0:
            _decision_log(
                "DECISION_STORAGE_SUMMARY",
                "批量决策动态结果已写入数据库",
                status="success",
                stored_records=success_count,
                total_dynamic_results=success_count,
                storage_scope="dynamic_results_only",
            )

        return self._create_success_result(
            total_rooms=len(self.rooms),
            successful_rooms=success_count,
            failed_rooms=failed_count,
            batch_duration=batch_duration,
            room_results=results,
            storage_summary=f"{success_count}个库房的动态结果已存储",
        )

    def _store_dynamic_results(
        self, result, room_id: str, analysis_datetime: datetime
    ) -> Dict[str, Any]:
        """
        存储动态结果到数据库

        Args:
            result: 分析结果
            room_id: 库房编号
            analysis_datetime: 分析时间

        Returns:
            Dict[str, Any]: 存储结果
        """
        try:
            _decision_log(
                "DECISION_STORAGE_START",
                "开始存储决策动态结果",
                room_id=room_id,
                status="running",
            )

            ensure_src_path()

            # 直接从result中获取数据，无需读取JSON文件
            if hasattr(result, "data") and result.data:
                json_data = result.data
            else:
                _decision_log(
                    "DECISION_STORAGE_SKIPPED",
                    "结果数据为空，跳过数据库存储",
                    level="WARNING",
                    room_id=room_id,
                    status="skipped",
                    skipped_items=1,
                )
                return {"success": False, "message": "结果数据为空"}

            if json_data:
                # 仅存储动态结果到数据库
                storage_result = store_decision_analysis_results(
                    json_data=json_data,
                    room_id=room_id,
                    analysis_time=analysis_datetime,
                )

                _decision_log(
                    "DECISION_STORAGE_FINISH",
                    "动态结果存储完成",
                    room_id=room_id,
                    status="success",
                    batch_id=storage_result.get("batch_id"),
                    stored_records=storage_result.get("dynamic_results_count", 0),
                    total_dynamic_results=storage_result.get(
                        "dynamic_results_count", 0
                    ),
                    total_change_count=storage_result.get("change_count", 0),
                    static_configs_stored=storage_result.get(
                        "static_configs_stored", 0
                    ),
                )

                return storage_result
            else:
                return {"success": False, "message": "无有效数据存储"}

        except Exception as storage_error:
            # 数据库存储失败不影响分析任务的成功状态
            _decision_log(
                "DECISION_STORAGE_FAILED",
                "动态结果存储失败",
                level="ERROR",
                room_id=room_id,
                status="failed",
                error_type=type(storage_error).__name__,
                error_message=str(storage_error),
            )
            return {
                "success": False,
                "error": str(storage_error),
                "message": "数据库存储失败",
            }

    def get_analysis_summary(self, days: int = 7) -> Dict[str, Any]:
        """
        获取决策分析摘要

        Args:
            days: 查询天数

        Returns:
            Dict[str, Any]: 分析摘要
        """
        try:
            from sqlalchemy import text

            from global_const.global_const import pgsql_engine

            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days)

            summary = {
                "query_period": f"{start_date} to {end_date}",
                "total_days": days,
                "rooms_summary": {},
                "overall_stats": {},
            }

            with pgsql_engine.connect() as conn:
                # 查询各库房分析记录数
                for room_id in self.rooms:
                    result = conn.execute(
                        text("""
                        SELECT 
                            COUNT(DISTINCT batch_id) as analysis_batches,
                            COUNT(*) as total_results,
                            COUNT(CASE WHEN action_type = 'adjust' THEN 1 END) as adjust_actions,
                            COUNT(CASE WHEN action_type = 'maintain' THEN 1 END) as maintain_actions,
                            COUNT(CASE WHEN action_type = 'monitor' THEN 1 END) as monitor_actions
                        FROM decision_analysis_dynamic_result 
                        WHERE room_id = :room_id 
                        AND DATE(analysis_time) BETWEEN :start_date AND :end_date
                    """),
                        {
                            "room_id": room_id,
                            "start_date": start_date,
                            "end_date": end_date,
                        },
                    )

                    row = result.fetchone()
                    if row:
                        summary["rooms_summary"][room_id] = {
                            "analysis_batches": row[0],
                            "total_results": row[1],
                            "action_distribution": {
                                "adjust": row[2],
                                "maintain": row[3],
                                "monitor": row[4],
                            },
                        }

                # 查询总体统计
                result = conn.execute(
                    text("""
                    SELECT 
                        COUNT(DISTINCT batch_id) as total_batches,
                        COUNT(DISTINCT room_id) as rooms_with_data,
                        COUNT(*) as total_results,
                        COUNT(CASE WHEN action_type = 'adjust' THEN 1 END) as total_adjust_actions
                    FROM decision_analysis_dynamic_result 
                    WHERE DATE(analysis_time) BETWEEN :start_date AND :end_date
                """),
                    {"start_date": start_date, "end_date": end_date},
                )

                row = result.fetchone()
                if row:
                    summary["overall_stats"] = {
                        "total_batches": row[0],
                        "rooms_with_data": row[1],
                        "total_results": row[2],
                        "total_adjust_actions": row[3],
                        "avg_results_per_batch": row[2] / row[0] if row[0] > 0 else 0,
                    }

            return summary

        except Exception as e:
            _decision_log(
                "DECISION_SUMMARY_FAILED",
                "获取决策分析摘要失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"error": str(e), "query_time": datetime.now().isoformat()}

    def validate_analysis_quality(self, room_id: str, days: int = 1) -> Dict[str, Any]:
        """
        验证决策分析质量

        Args:
            room_id: 库房编号
            days: 验证天数

        Returns:
            Dict[str, Any]: 验证结果
        """
        try:
            from sqlalchemy import text

            from global_const.global_const import pgsql_engine

            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days)

            with pgsql_engine.connect() as conn:
                result = conn.execute(
                    text("""
                    SELECT 
                        COUNT(DISTINCT batch_id) as analysis_batches,
                        COUNT(*) as total_results,
                        COUNT(CASE WHEN confidence_score >= 0.8 THEN 1 END) as high_confidence,
                        COUNT(CASE WHEN confidence_score < 0.5 THEN 1 END) as low_confidence,
                        AVG(confidence_score) as avg_confidence,
                        COUNT(CASE WHEN action_type = 'adjust' THEN 1 END) as adjustment_recommendations
                    FROM decision_analysis_dynamic_result 
                    WHERE room_id = :room_id 
                    AND DATE(analysis_time) BETWEEN :start_date AND :end_date
                """),
                    {
                        "room_id": room_id,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )

                row = result.fetchone()
                if row:
                    total_results = row[1]
                    validation_result = {
                        "room_id": room_id,
                        "validation_period": f"{start_date} to {end_date}",
                        "analysis_batches": row[0],
                        "total_results": total_results,
                        "confidence_distribution": {
                            "high_confidence": row[2],
                            "low_confidence": row[3],
                            "high_confidence_rate": row[2] / total_results
                            if total_results > 0
                            else 0,
                            "low_confidence_rate": row[3] / total_results
                            if total_results > 0
                            else 0,
                        },
                        "avg_confidence": round(row[4], 3) if row[4] else None,
                        "adjustment_rate": row[5] / total_results
                        if total_results > 0
                        else 0,
                    }

                    # 计算质量分数
                    confidence_score = (
                        validation_result["confidence_distribution"][
                            "high_confidence_rate"
                        ]
                        * 60
                    )
                    consistency_score = (
                        1
                        - validation_result["confidence_distribution"][
                            "low_confidence_rate"
                        ]
                    ) * 40
                    validation_result["overall_quality_score"] = (
                        confidence_score + consistency_score
                    )

                    return validation_result
                else:
                    return {
                        "room_id": room_id,
                        "error": "No data found for validation period",
                    }

        except Exception as e:
            _decision_log(
                "DECISION_VALIDATION_FAILED",
                "验证决策分析质量失败",
                level="ERROR",
                room_id=room_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"room_id": room_id, "error": str(e)}


# 创建全局实例
decision_analysis_task = DecisionAnalysisTask()


def safe_decision_analysis_for_room(room_id: str) -> None:
    """
    执行单个蘑菇房的决策分析任务（兼容原接口）

    Args:
        room_id: 蘑菇房编号
    """
    result = decision_analysis_task.execute_single_room_analysis(room_id)

    if not result.get("success", False):
        _decision_log(
            "DECISION_COMPAT_ROOM_FAILED",
            "兼容接口单库房决策分析失败",
            level="ERROR",
            room_id=room_id,
            status="failed",
            error_message=result.get("error", "未知错误"),
        )
    else:
        _decision_log(
            "DECISION_COMPAT_ROOM_FINISH",
            "兼容接口单库房决策分析成功完成",
            room_id=room_id,
            status="success",
        )


def safe_batch_decision_analysis(
    schedule_hour: int = None, schedule_minute: int = None
) -> None:
    """
    批量执行所有蘑菇房的决策分析任务（兼容原接口）

    Args:
        schedule_hour: 计划执行的小时（可选，用于日志记录）
        schedule_minute: 计划执行的分钟（可选，用于日志记录）
    """
    result = decision_analysis_task.run()

    if not result.get("success", False):
        _decision_log(
            "DECISION_COMPAT_BATCH_FAILED",
            "兼容接口批量决策分析失败",
            level="ERROR",
            status="failed",
            task_run_id=result.get("task_run_id"),
            error_message=result.get("error", "未知错误"),
        )
    else:
        _decision_log(
            "DECISION_COMPAT_BATCH_FINISH",
            "兼容接口批量决策分析成功完成",
            status="success",
            task_run_id=result.get("task_run_id"),
            total_items=result.get("total_rooms", 0),
            successful_items=result.get("successful_rooms", 0),
            failed_items=result.get("failed_rooms", 0),
        )


def get_decision_analysis_summary(days: int = 7) -> Dict[str, Any]:
    """
    获取决策分析摘要（兼容原接口）

    Args:
        days: 查询天数

    Returns:
        Dict[str, Any]: 分析摘要
    """
    return decision_analysis_task.get_analysis_summary(days)


def validate_decision_quality(room_id: str, days: int = 1) -> Dict[str, Any]:
    """
    验证决策分析质量

    Args:
        room_id: 库房编号
        days: 验证天数

    Returns:
        Dict[str, Any]: 验证结果
    """
    return decision_analysis_task.validate_analysis_quality(room_id, days)
