"""
MLflow 日志记录模块
"""
import mlflow
from typing import Any, Dict, Optional
from pathlib import Path
from PIL import Image
from loguru import logger
from .config import config

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
            experiment_name = self.mlflow_config.get("experiment_name", "offline_image_analysis")
        self.experiment_name = experiment_name
            
        try:
            mlflow.set_tracking_uri(self.tracking_uri)
            logger.info(f"MLflow tracking URI set to: {self.tracking_uri}")
            
            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                mlflow.create_experiment(experiment_name)
            mlflow.set_experiment(experiment_name)
            logger.info(f"MLflow experiment set to: {experiment_name}")
            
        except Exception as e:
            logger.warning(f"Failed to connect to remote MLflow at {self.tracking_uri}: {e}")
            logger.warning("Falling back to local MLflow (./mlruns)")
            
            try:
                Path("mlruns").mkdir(exist_ok=True)
                self.tracking_uri = "file://" + str(Path("mlruns").absolute())
                mlflow.set_tracking_uri(self.tracking_uri)
                
                experiment = mlflow.get_experiment_by_name(experiment_name)
                if experiment is None:
                    mlflow.create_experiment(experiment_name)
                mlflow.set_experiment(experiment_name)
                logger.info(f"Local MLflow experiment set to: {experiment_name}")
                
            except Exception as local_e:
                logger.error(f"Failed to initialize local MLflow: {local_e}")
                self.disabled = True

    def log_analysis_result(
        self, 
        image: Image.Image, 
        image_name: str,
        chinese_desc: str, 
        english_desc: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        记录分析结果到MLflow
        """
        if self.disabled:
            logger.warning(f"MLflow logging is disabled. Skipping log for {image_name}")
            return
        try:
            with mlflow.start_run(run_name=f"analyze_{image_name}"):
                if metadata:
                    mlflow.log_params(metadata)
                
                mlflow.log_text(chinese_desc, "chinese_description.txt")
                mlflow.log_text(english_desc, "english_description.txt")
                
                mlflow.log_image(image, image_name)
                
                logger.info(f"Logged result for {image_name} to MLflow")
                
        except Exception as e:
            logger.warning(f"MLflow remote logging failed for {image_name}: {e}")
            if not self.tracking_uri.startswith("file://") and self._switch_to_local():
                try:
                    with mlflow.start_run(run_name=f"analyze_{image_name}"):
                        if metadata:
                            mlflow.log_params(metadata)
                        mlflow.log_text(chinese_desc, "chinese_description.txt")
                        mlflow.log_text(english_desc, "english_description.txt")
                        mlflow.log_image(image, image_name)
                    logger.info(f"Logged result for {image_name} to local MLflow (fallback)")
                    return
                except Exception as retry_error:
                    logger.error(f"Failed to log to local MLflow for {image_name}: {retry_error}")
                    self.disabled = True
                    return
            
            logger.error(f"Failed to log to MLflow for {image_name}: {e}")

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
            logger.info(f"Local MLflow experiment set to: {self.experiment_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize local MLflow: {e}")
            self.disabled = True
            return False
