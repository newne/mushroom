import mlflow
import pandas as pd
import json
from src.vision.offline.runner import Runner
from src.vision.offline.dataset import DatasetLoader
from src.vision.offline.scorers import score_json_format_metric, score_schema_conformity_metric
from src.vision.offline.config import Config, config as default_config

def run_comparison_eval(limit=20):
    print("Running comparison evaluation...")
    
    # 1. Load Data
    loader = DatasetLoader()
    eval_df = loader.get_evaluation_set(size=limit)
    
    # 2. Setup MLflow
    mlflow.set_tracking_uri(default_config.mlflow_tracking_uri)
    mlflow.set_experiment("Mushroom_Offline_Evaluation")
    
    # 3. Load Correct Prompt (3 fields)
    try:
        # Load from registry as per user instruction
        prompt_obj = mlflow.genai.load_prompt("prompts:/growth_stage_describe/1")
        template = prompt_obj.template
        print("Loaded prompt growth_stage_describe/1 from registry.")
    except Exception as e:
        print(f"Error loading prompt: {e}")
        return

    eval_df['prompt_template'] = template

    # 4. Define Scorer for 3-field Schema
    # The default score_schema_conformity checks for 6 fields. We need a new one or modified one.
    # Since we can't easily modify the imported function's closure, let's define a specific one here.
    
    from mlflow.metrics import make_metric, MetricValue
    
    def extract_json(text: str) -> str:
        text = str(text).strip()
        try:
            start = text.index('{')
            end = text.rindex('}') + 1
            return text[start:end]
        except ValueError:
            return text

    def score_schema_conformity_3fields(eval_df, builtin_metrics):
        required_keys = ["growth_stage_description", "chinese_description", "image_quality_score"]
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
        name="score_schema_conformity_3fields"
    )

    # 5. Initialize Runner with 4B Model Profile
    # The user said "settings.data_source_url.prompt_mushroom_description configuration can access 4b model"
    # But checking settings.toml, [development.llama] uses "qwen3-vl-4b".
    # And prompt_mushroom_description is just a URL string.
    # I will assume the user meant I should use the profile that has the 4b model.
    # I'll modify Config to allow switching profiles or just instantiate a new Config("development.llama")
    
    print("Initializing Runner with profile: development.llama (4B Model)...")
    
    # We need to hack the global config or pass config to Runner. 
    # Runner uses `from .config import config`. 
    # To avoid changing Runner significantly, we can patch the global config object attributes.
    
    config_4b = Config("development.llama")
    
    # Patch the global config instance used by Runner
    default_config._config = config_4b._config
    # Force re-read of properties
    # Config properties (base_url, model, api_key) read from self._config, so this should work.
    
    print(f"Model: {default_config.model}")
    print(f"Base URL: {default_config.base_url}")
    
    runner = Runner()
    
    def predict_wrapper(inputs):
        return runner.predict(inputs)
    
    # 6. Run Evaluation
    with mlflow.start_run(run_name="eval_4b_model_v1_prompt"):
        mlflow.log_param("model", default_config.model)
        mlflow.log_param("prompt_source", "prompts:/growth_stage_describe/1")
        
        results = mlflow.evaluate(
            model=predict_wrapper,
            data=eval_df,
            extra_metrics=[
                score_json_format_metric,
                score_schema_3fields_metric
            ],
            evaluator_config={
                "col_mapping": {
                    "inputs": "image_path"
                }
            }
        )
        
        print("Evaluation results:")
        print(results.metrics)
        
        # Log results to a file for user to see
        mlflow.log_dict(results.metrics, "metrics_4b.json")

if __name__ == "__main__":
    run_comparison_eval()
