"""
MLflow 日志记录模块
"""

from pathlib import Path
from typing import Any, Dict, Optional

import mlflow
from PIL import Image

from utils import log_task_event

from .config import config


def _mlflow_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_MLFLOW_LOGGER",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


class MLflowLogger:
    """MLflow日志记录器封装"""

    def __init__(self, experiment_name: Optional[str] = None):
        self.mlflow_config = config.mlflow_config
        self.host = self.mlflow_config.get("host", "localhost")
        self.port = self.mlflow_config.get("port", "5000")
        self.tracking_uri = f"http://{self.host}:{self.port}"
        self.disabled = False

        # 优先使用传入的名称，其次使用配置中的名称，最后使用默认值
        if experiment_name is None:
            experiment_name = self.mlflow_config.get(
                "experiment_name", "offline_image_analysis"
            )
        self.experiment_name = experiment_name

        try:
            mlflow.set_tracking_uri(self.tracking_uri)
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_REMOTE_URI_READY",
                "MLflow tracking URI 已设置",
                tracking_uri=self.tracking_uri,
                status="success",
            )

            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                mlflow.create_experiment(experiment_name)
            mlflow.set_experiment(experiment_name)
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_REMOTE_EXPERIMENT_READY",
                "MLflow experiment 已设置",
                experiment_name=experiment_name,
                status="success",
            )

        except Exception as e:
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_REMOTE_FAILED",
                "连接远程 MLflow 失败，准备回退本地 MLflow",
                level="WARNING",
                tracking_uri=self.tracking_uri,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

            try:
                Path("mlruns").mkdir(exist_ok=True)
                self.tracking_uri = "file://" + str(Path("mlruns").absolute())
                mlflow.set_tracking_uri(self.tracking_uri)

                experiment = mlflow.get_experiment_by_name(experiment_name)
                if experiment is None:
                    mlflow.create_experiment(experiment_name)
                mlflow.set_experiment(experiment_name)
                _mlflow_log(
                    "VISION_OFFLINE_MLFLOW_LOCAL_READY",
                    "本地 MLflow experiment 已设置",
                    tracking_uri=self.tracking_uri,
                    experiment_name=experiment_name,
                    status="success",
                )

            except Exception as local_e:
                _mlflow_log(
                    "VISION_OFFLINE_MLFLOW_LOCAL_FAILED",
                    "初始化本地 MLflow 失败",
                    level="ERROR",
                    tracking_uri=self.tracking_uri,
                    status="failed",
                    error_type=type(local_e).__name__,
                    error_message=str(local_e),
                )
                self.disabled = True

    def log_analysis_result(
        self,
        image: Image.Image,
        image_name: str,
        chinese_desc: str,
        english_desc: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        记录分析结果到MLflow
        """
        if self.disabled:
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_DISABLED",
                "MLflow 日志功能已禁用，跳过记录",
                level="WARNING",
                image_name=image_name,
                status="skipped",
            )
            return
        try:
            with mlflow.start_run(run_name=f"analyze_{image_name}"):
                if metadata:
                    mlflow.log_params(metadata)

                mlflow.log_text(chinese_desc, "chinese_description.txt")
                mlflow.log_text(english_desc, "english_description.txt")

                mlflow.log_image(image, image_name)

                _mlflow_log(
                    "VISION_OFFLINE_MLFLOW_LOGGED_REMOTE",
                    "已记录分析结果到远程 MLflow",
                    image_name=image_name,
                    status="success",
                )

        except Exception as e:
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_REMOTE_LOG_FAILED",
                "远程 MLflow 记录失败，尝试回退本地 MLflow",
                level="WARNING",
                image_name=image_name,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            if not self.tracking_uri.startswith("file://") and self._switch_to_local():
                try:
                    with mlflow.start_run(run_name=f"analyze_{image_name}"):
                        if metadata:
                            mlflow.log_params(metadata)
                        mlflow.log_text(chinese_desc, "chinese_description.txt")
                        mlflow.log_text(english_desc, "english_description.txt")
                        mlflow.log_image(image, image_name)
                    _mlflow_log(
                        "VISION_OFFLINE_MLFLOW_LOGGED_LOCAL",
                        "已记录分析结果到本地 MLflow 回退存储",
                        image_name=image_name,
                        status="success",
                    )
                    return
                except Exception as retry_error:
                    _mlflow_log(
                        "VISION_OFFLINE_MLFLOW_LOCAL_LOG_FAILED",
                        "记录分析结果到本地 MLflow 失败",
                        level="ERROR",
                        image_name=image_name,
                        status="failed",
                        error_type=type(retry_error).__name__,
                        error_message=str(retry_error),
                    )
                    self.disabled = True
                    return

            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_LOG_FAILED",
                "记录分析结果到 MLflow 失败",
                level="ERROR",
                image_name=image_name,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def _is_credentials_error(self, error: Exception) -> bool:
        message = str(error).lower()
        return (
            "unable to locate credentials" in message
            or "nocredential" in message
            or "no credentials" in message
            or "invalidaccesskeyid" in message
            or "accessdenied" in message
        )

    def _switch_to_local(self) -> bool:
        try:
            Path("mlruns").mkdir(exist_ok=True)
            self.tracking_uri = "file://" + str(Path("mlruns").absolute())
            mlflow.set_tracking_uri(self.tracking_uri)
            experiment = mlflow.get_experiment_by_name(self.experiment_name)
            if experiment is None:
                mlflow.create_experiment(self.experiment_name)
            mlflow.set_experiment(self.experiment_name)
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_SWITCH_LOCAL_READY",
                "已切换到本地 MLflow",
                tracking_uri=self.tracking_uri,
                experiment_name=self.experiment_name,
                status="success",
            )
            return True
        except Exception as e:
            _mlflow_log(
                "VISION_OFFLINE_MLFLOW_SWITCH_LOCAL_FAILED",
                "切换本地 MLflow 失败",
                level="ERROR",
                tracking_uri=self.tracking_uri,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.disabled = True
            return False
