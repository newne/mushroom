"""
CLIP推理任务模块

负责蘑菇图像的CLIP推理处理相关的定时任务。
"""

import time
from datetime import datetime, timedelta

from global_const.const_config import (
    CLIP_INFERENCE_BATCH_SIZE,
    CLIP_INFERENCE_HOUR_LOOKBACK,
    CLIP_INFERENCE_MAX_RETRIES,
    CLIP_INFERENCE_RETRY_DELAY,
    MUSHROOM_ROOM_IDS,
)
from utils import build_task_run_id, log_task_event
from utils.task_common import (
    TaskResult,
    create_task_result,
    ensure_database_connection,
    execute_task_with_retry,
    log_task_summary,
)

VISION_CONNECTION_ERROR_KEYWORDS = (
    "timeout",
    "connection",
    "connect",
    "database",
    "server",
    "redis",
    "minio",
)


def safe_hourly_clip_inference() -> None:
    """兼容入口：保留旧函数名（不再用于调度）"""
    from vision.executor import safe_hourly_clip_inference as _safe_hourly

    _safe_hourly()


def safe_hourly_text_quality_inference(reprocess: bool = False) -> TaskResult:
    """每小时文本编码与图像质量评估任务。"""
    task_run_id = build_task_run_id("TEXT_QUALITY_TASK")
    ensure_database_connection(
        "[TEXT_QUALITY_TASK] 数据库不可达，任务终止（按配置不启用容错）"
    )

    max_retries = CLIP_INFERENCE_MAX_RETRIES
    retry_delay = CLIP_INFERENCE_RETRY_DELAY

    def _run_text_quality_attempt(attempt: int, total_attempts: int) -> TaskResult:
        mlflow_run = None
        try:
            task_start = time.time()
            end_time = datetime.now()
            start_time_filter = end_time - timedelta(hours=CLIP_INFERENCE_HOUR_LOOKBACK)
            log_task_event(
                "TEXT_QUALITY_TASK",
                "TASK_START",
                "开始执行每小时文本/质量任务",
                task_type="vision",
                task_run_id=task_run_id,
                attempt=attempt,
                max_retries=total_attempts,
                status="running",
                reprocess=bool(reprocess),
                trigger_time=end_time.isoformat(),
                window_start=start_time_filter.isoformat(),
                lookback_hours=CLIP_INFERENCE_HOUR_LOOKBACK,
            )

            try:
                import mlflow
                from mlflow.tracking import MlflowClient

                from global_const.global_const import settings

                tracking_uri = None
                try:
                    host = getattr(getattr(settings, "mlflow", None), "host", None)
                    port = getattr(getattr(settings, "mlflow", None), "port", None)
                    if host and port:
                        tracking_uri = f"http://{host}:{port}"
                except Exception:
                    tracking_uri = None

                if tracking_uri:
                    mlflow.set_tracking_uri(tracking_uri)

                experiment_name = "Mushroom_Text_Quality_Task"
                client = MlflowClient()
                experiment = client.get_experiment_by_name(experiment_name)
                if experiment is None:
                    client.create_experiment(experiment_name)
                elif getattr(experiment, "lifecycle_stage", "active") == "deleted":
                    client.restore_experiment(experiment.experiment_id)
                    log_task_event(
                        "TEXT_QUALITY_TASK",
                        "MLFLOW_EXPERIMENT_RESTORED",
                        "检测到已删除实验，已自动恢复",
                        level="WARNING",
                        task_type="vision",
                        task_run_id=task_run_id,
                        status="running",
                        experiment_name=experiment_name,
                    )

                mlflow.set_experiment(experiment_name)
                mlflow_run = mlflow.start_run(
                    run_name=f"text_quality_{end_time.strftime('%Y%m%d_%H%M%S')}"
                )
                try:
                    mlflow.log_param("lookback_hours", CLIP_INFERENCE_HOUR_LOOKBACK)
                    mlflow.log_param("batch_size", CLIP_INFERENCE_BATCH_SIZE)
                    mlflow.log_param("reprocess", bool(reprocess))
                    prompt_src = getattr(
                        settings.data_source_url, "prompt_mushroom_description", None
                    )
                    if prompt_src:
                        mlflow.log_param("prompt_source", str(prompt_src))
                except Exception:
                    pass
            except Exception as exc:
                log_task_event(
                    "TEXT_QUALITY_TASK",
                    "MLFLOW_INIT_FAILED",
                    "MLflow 初始化失败，将继续无 Traces 运行",
                    level="WARNING",
                    task_type="vision",
                    task_run_id=task_run_id,
                    status="degraded",
                    error_type=type(exc).__name__,
                    error_code="mlflow_init_failed",
                    error_message=str(exc),
                )

            import_start = time.time()
            from vision.mushroom_image_encoder import create_mushroom_encoder

            log_task_event(
                "TEXT_QUALITY_TASK",
                "ENCODER_IMPORT_FINISH",
                "编码器模块导入完成",
                task_type="vision",
                task_run_id=task_run_id,
                duration_ms=round((time.time() - import_start) * 1000, 2),
            )

            encoder_start = time.time()
            encoder = create_mushroom_encoder(load_clip=False)
            log_task_event(
                "TEXT_QUALITY_TASK",
                "ENCODER_INIT_FINISH",
                "图像编码器初始化完成",
                task_type="vision",
                task_run_id=task_run_id,
                duration_ms=round((time.time() - encoder_start) * 1000, 2),
            )
            minio_rooms = set(encoder.minio_client.list_rooms())
            env_to_minio: dict[str, list[str]] = {}
            for minio_id, env_id in encoder.room_id_mapping.items():
                env_to_minio.setdefault(env_id, []).append(minio_id)

            total_stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

            for room_id in MUSHROOM_ROOM_IDS:
                try:
                    room_start = time.time()
                    log_task_event(
                        "TEXT_QUALITY_TASK",
                        "ROOM_START",
                        "开始处理库房文本/质量任务",
                        task_type="vision",
                        task_run_id=task_run_id,
                        room_id=room_id,
                        status="running",
                    )
                    candidate_ids = env_to_minio.get(room_id, [room_id])
                    minio_room_id = next(
                        (cid for cid in candidate_ids if cid in minio_rooms), room_id
                    )
                    if minio_room_id != room_id:
                        log_task_event(
                            "TEXT_QUALITY_TASK",
                            "ROOM_MAPPING_RESOLVED",
                            "库房号映射已解析",
                            task_type="vision",
                            task_run_id=task_run_id,
                            room_id=room_id,
                            mapped_room_id=minio_room_id,
                        )
                    stats = encoder.batch_process_text_quality(
                        mushroom_id=minio_room_id,
                        start_time=start_time_filter,
                        end_time=end_time,
                        batch_size=CLIP_INFERENCE_BATCH_SIZE,
                        reprocess=reprocess,
                        link_mushroom_embedding=False,
                    )
                    for key in total_stats:
                        total_stats[key] += stats.get(key, 0)
                    log_task_event(
                        "TEXT_QUALITY_TASK",
                        "ROOM_FINISH",
                        "库房文本/质量任务处理完成",
                        task_type="vision",
                        task_run_id=task_run_id,
                        room_id=room_id,
                        status="success",
                        duration_ms=round((time.time() - room_start) * 1000, 2),
                        room_stats=stats,
                    )
                except Exception as exc:
                    total_stats["failed"] += 1
                    log_task_event(
                        "TEXT_QUALITY_TASK",
                        "ROOM_FAILURE",
                        "库房文本/质量任务处理失败",
                        level="ERROR",
                        task_type="vision",
                        task_run_id=task_run_id,
                        room_id=room_id,
                        status="failed",
                        error_type=type(exc).__name__,
                        error_code="text_quality_room_failed",
                        error_message=str(exc),
                    )

            task_duration = time.time() - task_start
            log_task_event(
                "TEXT_QUALITY_TASK",
                "TASK_FINISH",
                "文本/质量任务执行完成",
                task_type="vision",
                task_run_id=task_run_id,
                status="success" if total_stats["failed"] == 0 else "failed",
                total_items=int(total_stats["total"]),
                successful_items=int(total_stats["success"]),
                failed_items=int(total_stats["failed"]),
                skipped_items=int(total_stats["skipped"]),
                duration_ms=round(task_duration * 1000, 2),
            )
            try:
                if mlflow_run is not None:
                    import mlflow

                    mlflow.log_metric("total", float(total_stats["total"]))
                    mlflow.log_metric("success", float(total_stats["success"]))
                    mlflow.log_metric("failed", float(total_stats["failed"]))
                    mlflow.log_metric("skipped", float(total_stats["skipped"]))
                    mlflow.end_run(status="FINISHED")
            except Exception as exc:
                log_task_event(
                    "TEXT_QUALITY_TASK",
                    "MLFLOW_FLUSH_FAILED",
                    "MLflow 指标或 Traces flush 失败",
                    level="WARNING",
                    task_type="vision",
                    task_run_id=task_run_id,
                    status="degraded",
                    error_type=type(exc).__name__,
                    error_code="mlflow_flush_failed",
                    error_message=str(exc),
                )

            return create_task_result(
                success=total_stats["failed"] == 0,
                total_items=int(total_stats["total"]),
                successful_items=int(total_stats["success"]),
                failed_items=int(total_stats["failed"]),
                processing_time=task_duration,
                additional_data={
                    "task_run_id": task_run_id,
                    "skipped_items": int(total_stats["skipped"]),
                    "reprocess": bool(reprocess),
                    "lookback_hours": CLIP_INFERENCE_HOUR_LOOKBACK,
                },
            )
        except Exception as exc:
            error_msg = str(exc)
            try:
                if mlflow_run is not None:
                    import mlflow

                    mlflow.end_run(status="FAILED")
            except Exception:
                pass
            raise RuntimeError(error_msg)

    def _on_text_quality_retry(
        _error_msg: str, _attempt: int, _total_attempts: int
    ) -> None:
        log_task_event(
            "TEXT_QUALITY_TASK",
            "TASK_RETRY",
            "检测到连接类错误，准备重试",
            level="WARNING",
            task_type="vision",
            task_run_id=task_run_id,
            status="retrying",
            retry_delay_sec=retry_delay,
        )

    def _on_text_quality_non_retryable(
        error_msg: str, _attempt: int, _total_attempts: int
    ) -> TaskResult:
        log_task_event(
            "TEXT_QUALITY_TASK",
            "TASK_NON_RETRYABLE",
            "任务失败且不再重试",
            level="ERROR",
            task_type="vision",
            task_run_id=task_run_id,
            status="failed",
            error_code="text_quality_non_retryable",
            error_message=error_msg,
        )
        return create_task_result(
            success=False,
            error_items=[error_msg],
            additional_data={"task_run_id": task_run_id, "reprocess": bool(reprocess)},
        )

    result = execute_task_with_retry(
        task_name="TEXT_QUALITY_TASK",
        task_func=_run_text_quality_attempt,
        max_retries=max_retries,
        retry_delay=retry_delay,
        task_context={"task_type": "vision", "task_run_id": task_run_id},
        on_retry=_on_text_quality_retry,
        on_non_retryable=_on_text_quality_non_retryable,
        on_exhausted=_on_text_quality_non_retryable,
        connection_error_keywords=VISION_CONNECTION_ERROR_KEYWORDS,
    )
    log_task_summary("TEXT_QUALITY_TASK", result)
    return result


def safe_daily_top_quality_clip_inference() -> TaskResult:
    """每天凌晨执行Top质量图像编码任务"""
    task_run_id = build_task_run_id("TOP_QUALITY_TASK")
    ensure_database_connection(
        "[TOP_QUALITY_TASK] 数据库不可达，任务终止（按配置不启用容错）"
    )

    max_retries = CLIP_INFERENCE_MAX_RETRIES
    retry_delay = CLIP_INFERENCE_RETRY_DELAY

    def _run_top_quality_attempt(attempt: int, total_attempts: int) -> TaskResult:
        start_time = time.time()
        target_date = (datetime.now() - timedelta(days=1)).date()
        log_task_event(
            "TOP_QUALITY_TASK",
            "TASK_START",
            "开始执行 Top 质量图像任务",
            task_type="vision",
            task_run_id=task_run_id,
            attempt=attempt,
            max_retries=total_attempts,
            target_date=target_date.isoformat(),
            status="running",
        )

        from vision.mushroom_image_encoder import create_mushroom_encoder

        encoder = create_mushroom_encoder()

        stats = encoder.process_top_quality_embeddings_for_date(
            target_date=target_date,
            top_k=5,
            batch_size=CLIP_INFERENCE_BATCH_SIZE,
        )

        total = stats.get("total", 0)
        success = stats.get("success", 0)
        failed = stats.get("failed", 0)
        skipped = stats.get("skipped", 0)
        success_rate = (success / total) * 100 if total > 0 else 0
        duration = time.time() - start_time

        log_task_event(
            "TOP_QUALITY_TASK",
            "TASK_FINISH",
            "Top 质量图像任务执行完成",
            task_type="vision",
            task_run_id=task_run_id,
            status="success" if failed == 0 else "failed",
            total_items=int(total),
            successful_items=int(success),
            failed_items=int(failed),
            skipped_items=int(skipped),
            success_rate=round(success_rate, 2),
            duration_ms=round(duration * 1000, 2),
        )

        return create_task_result(
            success=failed == 0,
            total_items=int(total),
            successful_items=int(success),
            failed_items=int(failed),
            processing_time=duration,
            additional_data={
                "task_run_id": task_run_id,
                "skipped_items": int(skipped),
                "success_rate": success_rate,
                "target_date": target_date.isoformat(),
            },
        )

    def _on_top_quality_retry(
        _error_msg: str, _attempt: int, _total_attempts: int
    ) -> None:
        log_task_event(
            "TOP_QUALITY_TASK",
            "TASK_RETRY",
            "检测到连接类错误，准备重试",
            level="WARNING",
            task_type="vision",
            task_run_id=task_run_id,
            status="retrying",
            retry_delay_sec=retry_delay,
        )

    def _on_top_quality_non_retryable(
        error_msg: str, _attempt: int, _total_attempts: int
    ) -> TaskResult:
        log_task_event(
            "TOP_QUALITY_TASK",
            "TASK_NON_RETRYABLE",
            "任务失败且不再重试",
            level="ERROR",
            task_type="vision",
            task_run_id=task_run_id,
            status="failed",
            error_code="top_quality_non_retryable",
            error_message=error_msg,
        )
        return create_task_result(
            success=False,
            error_items=[error_msg],
            additional_data={"task_run_id": task_run_id},
        )

    result = execute_task_with_retry(
        task_name="TOP_QUALITY_TASK",
        task_func=_run_top_quality_attempt,
        max_retries=max_retries,
        retry_delay=retry_delay,
        task_context={"task_type": "vision", "task_run_id": task_run_id},
        on_retry=_on_top_quality_retry,
        on_non_retryable=_on_top_quality_non_retryable,
        on_exhausted=_on_top_quality_non_retryable,
        connection_error_keywords=VISION_CONNECTION_ERROR_KEYWORDS,
    )
    log_task_summary("TOP_QUALITY_TASK", result)
    return result


__all__ = [
    "safe_hourly_clip_inference",
    "safe_hourly_text_quality_inference",
    "safe_daily_top_quality_clip_inference",
]
if __name__ == "__main__":
    safe_hourly_text_quality_inference()
