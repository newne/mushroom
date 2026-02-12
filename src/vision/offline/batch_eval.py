import mlflow
import pandas as pd
from .runner import Runner
from .dataset import DatasetLoader
from .scorers import score_json_format_metric, score_schema_conformity_metric, morphology_quality
from .config import config

def run_batch_eval(prompt_name="growth_stage_describe", alias="prod", limit=20):
    """
    Runs batch evaluation using MLflow Evaluate.
    """
    print(f"Running batch evaluation for prompt: {prompt_name}@{alias}")
    
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
        print(f"Loaded prompt {prompt_obj.uri} from registry.")
        prompt_uri = prompt_obj.uri
    except Exception as e:
        print(f"Warning: Failed to load prompt from registry ({e}). Using default.")
        prompt_messages = [{"role": "system", "content": "Describe the mushroom growth stage in JSON format."}]
    
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
            targets=None, # Unsupervised or we don't have ground truth text easily
            # model_type="text", # Disabled to avoid missing dependency (tiktoken) errors
            extra_metrics=[
                score_json_format_metric,
                score_schema_conformity_metric,
                # morphology_quality # Requires LLM judge setup
            ],
            evaluator_config={
                "col_mapping": {
                    "inputs": "image_path"
                }
            }
        )
        
        print("Evaluation results:")
        print(results.metrics)
        
        # Log artifacts
        mlflow.log_dict(results.metrics, "metrics.json")
        
    return results

if __name__ == "__main__":
    run_batch_eval()
