import mlflow
from mlflow.tracking import MlflowClient

from utils import log_task_event

from .config import config


def _promote_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_PROMOTE",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


def promote_model(model_name: str, version: str, stage: str = "Staging"):
    """
    Promotes a model version to a specific stage.
    """
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)

    client = MlflowClient()

    _promote_log(
        "VISION_OFFLINE_PROMOTE_START",
        "开始执行模型晋升",
        model_name=model_name,
        version=version,
        stage=stage,
        status="running",
    )

    try:
        client.transition_model_version_stage(
            name=model_name,
            version=version,
            stage=stage,
            archive_existing_versions=True,
        )
        _promote_log(
            "VISION_OFFLINE_PROMOTE_FINISH",
            "模型晋升成功",
            model_name=model_name,
            version=version,
            stage=stage,
            status="success",
        )
    except Exception as e:
        _promote_log(
            "VISION_OFFLINE_PROMOTE_FAILED",
            "模型晋升失败",
            level="ERROR",
            model_name=model_name,
            version=version,
            stage=stage,
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )


def check_and_promote(run_id: str, min_score: float = 0.8):
    """
    Checks metrics of a run and promotes if it meets criteria.
    This is a simplified logic. Usually we compare against a baseline.
    """
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)

    client = MlflowClient()
    run = client.get_run(run_id)
    metrics = run.data.metrics

    # Define success criteria
    score_json = metrics.get("score_json_format/mean", 0)  # MLflow metric name format
    score_schema = metrics.get("score_schema_conformity/mean", 0)

    # Normalize metric names (sometimes they are just score_json)
    if "score_json" in metrics:
        score_json = metrics["score_json"]
    if "score_schema" in metrics:
        score_schema = metrics["score_schema"]

    avg_score = (score_json + score_schema) / 2

    _promote_log(
        "VISION_OFFLINE_PROMOTE_SCORE_CHECK",
        "完成运行分数检查",
        run_id=run_id,
        avg_score=avg_score,
        min_score=min_score,
        status="success",
    )

    if avg_score >= min_score:
        _promote_log(
            "VISION_OFFLINE_PROMOTE_SCORE_PASSED",
            "运行分数满足晋升条件",
            run_id=run_id,
            avg_score=avg_score,
            min_score=min_score,
            status="success",
        )
        # In a real scenario, we would register the model from this run first
        # mlflow.register_model(f"runs:/{run_id}/model", "MushroomAnalyzer")
        pass
    else:
        _promote_log(
            "VISION_OFFLINE_PROMOTE_SCORE_REJECTED",
            "运行分数未达到晋升阈值，不执行晋升",
            level="WARNING",
            run_id=run_id,
            avg_score=avg_score,
            min_score=min_score,
            status="skipped",
        )


if __name__ == "__main__":
    # Example usage
    # check_and_promote("some_run_id")
    pass
