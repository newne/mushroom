
import os
import sys
import datetime
import subprocess
import mlflow
import mlflow.sklearn
from mlflow.models import infer_signature
from typing import Dict, Any, Optional, Union
from loguru import logger
from pathlib import Path

class ExperimentTracker:
    """
    MLflow 实验追踪封装类，实现最佳实践。
    
    Features:
    - 自动管理 Experiment 和 Run
    - 自动记录环境信息 (Git commit, dependencies)
    - 自动记录系统指标
    - 统一的标签管理
    - 模型注册与版本管理
    """
    
    def __init__(
        self, 
        experiment_name: str, 
        tracking_uri: Optional[str] = None,
        artifact_location: Optional[str] = None
    ):
        """
        初始化实验追踪器
        
        Args:
            experiment_name: 实验名称，建议格式 {project}/{module}
            tracking_uri: MLflow Tracking Server URI
            artifact_location: 工件存储位置 (S3/Local)
        """
        self.experiment_name = experiment_name
        self.tracking_uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        
        if not self.tracking_uri:
             # Default to local mlruns with absolute path
            self.tracking_uri = "file://" + str(Path("./mlruns").absolute())
        
        # 设置 Tracking URI
        mlflow.set_tracking_uri(self.tracking_uri)
        logger.info(f"MLflow Tracking URI: {self.tracking_uri}")
        
        # 启用系统指标记录 (CPU, GPU, Memory, Network)
        try:
            mlflow.enable_system_metrics_logging()
        except AttributeError:
            logger.warning("当前 MLflow 版本不支持 enable_system_metrics_logging")
            
        # 设置 Experiment
        try:
            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                mlflow.create_experiment(experiment_name, artifact_location=artifact_location)
                logger.info(f"Created new experiment: {experiment_name}")
            else:
                logger.info(f"Using existing experiment: {experiment_name}")
            mlflow.set_experiment(experiment_name)
        except Exception as e:
            logger.error(f"Failed to setup experiment: {e}")
            # Fallback to local if remote fails
            if not self.tracking_uri.startswith("file://"):
                logger.warning("Falling back to local mlruns")
                local_uri = "file://" + str(Path("./mlruns").absolute())
                mlflow.set_tracking_uri(local_uri)
                mlflow.set_experiment(experiment_name)

    def start_run(
        self, 
        run_name: Optional[str] = None, 
        tags: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        nested: bool = False
    ):
        """
        开始一个新的 Run
        
        Args:
            run_name: 运行名称，若为空则自动生成
            tags: 额外标签
            description: 运行描述
            nested: 是否为嵌套运行
        """
        if not run_name:
            # 自动生成 Run Name: date_user
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            user = os.environ.get("USER", "unknown")
            run_name = f"{timestamp}_{user}"
            
        # 基础标签
        default_tags = {
            "os.user": os.environ.get("USER", "unknown"),
            "git.commit": self._get_git_commit(),
            "source.type": "script"
        }
        if tags:
            default_tags.update(tags)
            
        return mlflow.start_run(
            run_name=run_name,
            tags=default_tags,
            description=description,
            nested=nested
        )
        
    def log_params(self, params: Dict[str, Any]):
        """记录超参数"""
        mlflow.log_params(params)
        
    def log_metrics(self, metrics: Dict[str, float], step: Optional[int] = None):
        """记录指标"""
        mlflow.log_metrics(metrics, step=step)
        
    def log_artifact(self, local_path: str, artifact_path: Optional[str] = None):
        """记录文件工件"""
        mlflow.log_artifact(local_path, artifact_path)
        
    def log_environment(self):
        """记录当前 Python 环境依赖"""
        try:
            result = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True)
            if result.returncode == 0:
                with open("requirements.txt", "w") as f:
                    f.write(result.stdout)
                mlflow.log_artifact("requirements.txt", "environment")
                os.remove("requirements.txt") # 清理临时文件
        except Exception as e:
            logger.warning(f"Failed to log environment: {e}")

    def log_model(
        self, 
        model: Any, 
        artifact_path: str, 
        input_example: Any = None,
        registered_model_name: Optional[str] = None,
        flavor: str = "sklearn"
    ):
        """
        记录并注册模型
        
        Args:
            model: 模型对象
            artifact_path: MLflow 中的存储路径
            input_example: 输入示例（用于生成 Signature）
            registered_model_name: 注册模型名称（若提供则注册到 Model Registry）
            flavor: 模型类型 (sklearn, pytorch, tensorflow, etc.)
        """
        # 生成签名
        signature = None
        if input_example is not None:
            try:
                # 简单预测以获取输出 schema
                if flavor == "sklearn":
                    prediction = model.predict(input_example)
                    signature = infer_signature(input_example, prediction)
            except Exception as e:
                logger.warning(f"Failed to infer signature: {e}")

        # 记录模型
        log_func = getattr(mlflow, flavor).log_model
        model_info = log_func(
            model,
            artifact_path,
            signature=signature,
            input_example=input_example,
            registered_model_name=registered_model_name
        )
        
        logger.info(f"Model logged to {model_info.model_uri}")
        
        # 如果注册了模型，添加阶段说明
        if registered_model_name:
            client = mlflow.MlflowClient()
            latest_version = client.get_latest_versions(registered_model_name, stages=["None"])[0]
            client.set_model_version_tag(
                registered_model_name, 
                latest_version.version, 
                "stage", 
                "staging" # 默认为 Staging
            )
            logger.info(f"Model registered: {registered_model_name} v{latest_version.version}")

    def _get_git_commit(self) -> str:
        """获取当前 Git Commit Hash"""
        try:
            return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("utf-8").strip()
        except Exception:
            return "unknown"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        mlflow.end_run()

