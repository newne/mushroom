import mlflow

from utils import log_task_event

from .config import config
from .dataset import DatasetLoader
from .runner import Runner
from .scorers import score_json_format_metric, score_schema_conformity_metric


def _offline_eval_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_BATCH_EVAL",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


def run_batch_eval(prompt_name="growth_stage_describe", alias="prod", limit=20):
    """
    Runs batch evaluation using MLflow Evaluate.
    """
    _offline_eval_log(
        "VISION_OFFLINE_BATCH_EVAL_START",
        "开始执行离线批量评估",
        prompt_name=prompt_name,
        alias=alias,
        limit=limit,
        status="running",
    )

    # 1. Load Data
    loader = DatasetLoader()
    eval_df = loader.get_evaluation_set(size=limit)

    # Setup MLflow
    # Use remote tracking URI from config
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    mlflow.set_experiment("Mushroom_Offline_Evaluation")

    try:
        import mlflow.openai as mlflow_openai

        mlflow_openai.autolog()
    except Exception:
        pass

    # 2. Load Prompt
    prompt_uri = f"prompts:/{prompt_name}/{alias}"
    try:
        prompt_obj = mlflow.genai.load_prompt(f"prompts:/{prompt_name}/{alias}")
        prompt_messages = prompt_obj.format(image_input="[image attached]")
        _offline_eval_log(
            "VISION_OFFLINE_BATCH_EVAL_PROMPT_READY",
            "已从 registry 加载 prompt",
            prompt_uri=prompt_obj.uri,
            status="success",
        )
        prompt_uri = prompt_obj.uri
    except Exception as e:
        _offline_eval_log(
            "VISION_OFFLINE_BATCH_EVAL_PROMPT_FALLBACK",
            "加载 registry prompt 失败，回退默认 prompt",
            level="WARNING",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        prompt_messages = [
            {
                "role": "system",
                "content": "Describe the mushroom growth stage in JSON format.",
            }
        ]

    eval_df["prompt_messages"] = [prompt_messages for _ in range(len(eval_df))]
    eval_df["prompt_uri"] = prompt_uri

    # 3. Define Predict Function for MLflow
    # MLflow evaluate expects a model URI or a python function
    runner = Runner()

    def predict_wrapper(inputs):
        # inputs is a DataFrame
        return runner.predict(inputs)

    # 4. Run Evaluation
    with mlflow.start_run(run_name="batch_eval_v6"):
        results = mlflow.evaluate(
            model=predict_wrapper,
            data=eval_df,
            targets=None,  # Unsupervised or we don't have ground truth text easily
            # model_type="text", # Disabled to avoid missing dependency (tiktoken) errors
            extra_metrics=[
                score_json_format_metric,
                score_schema_conformity_metric,
                # morphology_quality # Requires LLM judge setup
            ],
            evaluator_config={"col_mapping": {"inputs": "image_path"}},
        )

        _offline_eval_log(
            "VISION_OFFLINE_BATCH_EVAL_FINISH",
            "离线批量评估完成",
            metrics=str(results.metrics),
            status="success",
        )

        # Log artifacts
        mlflow.log_dict(results.metrics, "metrics.json")

    return results


if __name__ == "__main__":
    run_batch_eval()
