import mlflow
from mlflow.tracking import MlflowClient
from .config import config

def promote_model(model_name: str, version: str, stage: str = "Staging"):
    """
    Promotes a model version to a specific stage.
    """
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    
    client = MlflowClient()
    
    print(f"Promoting model {model_name} version {version} to {stage}...")
    
    try:
        client.transition_model_version_stage(
            name=model_name,
            version=version,
            stage=stage,
            archive_existing_versions=True
        )
        print("Promotion successful.")
    except Exception as e:
        print(f"Error promoting model: {e}")

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
    score_json = metrics.get("score_json_format/mean", 0) # MLflow metric name format
    score_schema = metrics.get("score_schema_conformity/mean", 0)
    
    # Normalize metric names (sometimes they are just score_json)
    if "score_json" in metrics: score_json = metrics["score_json"]
    if "score_schema" in metrics: score_schema = metrics["score_schema"]
    
    avg_score = (score_json + score_schema) / 2
    
    print(f"Run {run_id} Score: {avg_score}")
    
    if avg_score >= min_score:
        print("Score meets criteria. Promoting...")
        # In a real scenario, we would register the model from this run first
        # mlflow.register_model(f"runs:/{run_id}/model", "MushroomAnalyzer")
        pass
    else:
        print("Score below threshold. No promotion.")

if __name__ == "__main__":
    # Example usage
    # check_and_promote("some_run_id")
    pass
