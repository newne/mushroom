import json

import mlflow

from src.global_const.global_const import settings
from src.vision.offline.config import Config
from src.vision.offline.config import config as default_config
from src.vision.offline.dataset import DatasetLoader
from src.vision.offline.runner import Runner
from src.vision.offline.scorers import (
    score_json_format_metric,
)
from utils import log_task_event


def _comparison_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_COMPARISON_EVAL",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


def run_comparison_eval(limit=20):
    _comparison_log(
        "VISION_OFFLINE_COMPARISON_START",
        "开始执行离线对比评估",
        limit=limit,
        status="running",
    )

    # 1. Load Data
    loader = DatasetLoader()
    eval_df = loader.get_evaluation_set(size=limit)

    # 2. Setup MLflow
    mlflow.set_tracking_uri(default_config.mlflow_tracking_uri)
    mlflow.set_experiment("Mushroom_Offline_Evaluation")

    # 3. Load Correct Prompt (3 fields)
    try:
        # Load from registry as per user instruction
        prompt_uri = getattr(
            settings.data_source_url, "prompt_mushroom_description", None
        )
        if not prompt_uri:
            raise ValueError(
                "prompt_mushroom_description is not configured in settings.toml"
            )
        prompt_obj = mlflow.genai.load_prompt(prompt_uri)
        template = prompt_obj.template
        _comparison_log(
            "VISION_OFFLINE_COMPARISON_PROMPT_READY",
            "对比评估 prompt 加载完成",
            prompt_uri=prompt_uri,
            status="success",
        )
    except Exception as e:
        _comparison_log(
            "VISION_OFFLINE_COMPARISON_PROMPT_FAILED",
            "对比评估 prompt 加载失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return

    eval_df["prompt_template"] = template

    # 4. Define Scorer for 3-field Schema
    # The default score_schema_conformity checks for 6 fields. We need a new one or modified one.
    # Since we can't easily modify the imported function's closure, let's define a specific one here.

    from mlflow.metrics import MetricValue, make_metric

    def extract_json(text: str) -> str:
        text = str(text).strip()
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return text[start:end]
        except ValueError:
            return text

    def score_schema_conformity_3fields(eval_df, builtin_metrics):
        required_keys = [
            "growth_stage_description",
            "chinese_description",
            "image_quality_score",
        ]
        scores = []
        justifications = []

        for output in eval_df["prediction"]:
            try:
                clean_output = extract_json(output)
                data = json.loads(clean_output)

                missing = [k for k in required_keys if k not in data]
                if not missing:
                    scores.append(1)
                    justifications.append("Schema conforms")
                else:
                    scores.append(0)
                    justifications.append(f"Missing keys: {missing}")
            except Exception as e:
                scores.append(0)
                justifications.append(f"Parse error: {str(e)}")

        return MetricValue(scores=scores, justifications=justifications)

    score_schema_3fields_metric = make_metric(
        eval_fn=score_schema_conformity_3fields,
        greater_is_better=True,
        name="score_schema_conformity_3fields",
    )

    # 5. Initialize Runner with 4B Model Profile
    # The user said "settings.data_source_url.prompt_mushroom_description configuration can access 4b model"
    # But checking settings.toml, [development.llama] uses "qwen3-vl-4b".
    # And prompt_mushroom_description is just a URL string.
    # I will assume the user meant I should use the profile that has the 4b model.
    # I'll modify Config to allow switching profiles or just instantiate a new Config("development.llama")

    _comparison_log(
        "VISION_OFFLINE_COMPARISON_RUNNER_INIT",
        "开始初始化 4B 模型 Runner",
        profile="development.llama",
        status="running",
    )

    # We need to hack the global config or pass config to Runner.
    # Runner uses `from .config import config`.
    # To avoid changing Runner significantly, we can patch the global config object attributes.

    config_4b = Config("development.llama")

    # Patch the global config instance used by Runner
    default_config._config = config_4b._config
    # Force re-read of properties
    # Config properties (base_url, model, api_key) read from self._config, so this should work.

    _comparison_log(
        "VISION_OFFLINE_COMPARISON_CONFIG",
        "对比评估 Runner 配置已更新",
        model=default_config.model,
        base_url=default_config.base_url,
        status="success",
    )

    runner = Runner()

    def predict_wrapper(inputs):
        return runner.predict(inputs)

    # 6. Run Evaluation
    with mlflow.start_run(run_name="eval_4b_model_v1_prompt"):
        mlflow.log_param("model", default_config.model)
        mlflow.log_param("prompt_source", prompt_uri)

        results = mlflow.evaluate(
            model=predict_wrapper,
            data=eval_df,
            extra_metrics=[score_json_format_metric, score_schema_3fields_metric],
            evaluator_config={"col_mapping": {"inputs": "image_path"}},
        )

        _comparison_log(
            "VISION_OFFLINE_COMPARISON_FINISH",
            "离线对比评估完成",
            metrics=str(results.metrics),
            status="success",
        )

        # Log results to a file for user to see
        mlflow.log_dict(results.metrics, "metrics_4b.json")


if __name__ == "__main__":
    run_comparison_eval()
