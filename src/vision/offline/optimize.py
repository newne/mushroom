import mlflow
import os
import pandas as pd
from typing import Optional
from mlflow.entities import Run
from .runner import Runner
from .dataset import DatasetLoader
from .scorers import score_json_format, score_schema_conformity, morphology_quality
from .config import config

class Optimizer:
    def __init__(self, prompt_name: str = "growth_stage_describe"):
        self.prompt_name = prompt_name
        self.runner = Runner()
        self.loader = DatasetLoader()
        
        # Setup MLflow
        # Use remote tracking URI from config
        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        mlflow.set_experiment("Mushroom_Prompt_Optimization")
        
        self.client = mlflow.MlflowClient()

    def optimize(self, iterations: int = 3, sample_size: int = 5):
        """
        Implements a basic GEPA (Generative Prompt Optimization) loop.
        1. Evaluate current prompt
        2. Analyze errors
        3. Generate improved prompt
        4. Repeat
        """
        print(f"Starting optimization for prompt: {self.prompt_name}")
        
        try:
            import mlflow.openai as mlflow_openai
            mlflow_openai.autolog()
            print("MLflow OpenAI Autologging enabled.")
        except Exception as e:
            print(f"Failed to enable MLflow OpenAI Autologging: {e}")

        def _render_user_message(user_template: str) -> str:
            text = str(user_template)
            if "{% if image_input %}" in text:
                text = text.replace("{% if image_input %}", "").replace("{% else %}", "").replace("{% endif %}", "")
                text = text.replace("{{ image_input }}", "[image attached]")
            return text.strip()

        try:
            prompt_obj = mlflow.genai.load_prompt(f"prompts:/{self.prompt_name}/3")
            template_messages = prompt_obj.template
            base_system = next((m.get("content") for m in template_messages if m.get("role") == "system"), "")
            base_user = next((m.get("content") for m in template_messages if m.get("role") == "user"), "Please analyze the image.")
            current_system = str(base_system)
            current_user_template = str(base_user)
            print(f"Loaded prompt {self.prompt_name}/3 from registry.")
        except Exception as e:
            print(f"Warning: Failed to load prompt from registry ({e}). Using default.")
            current_system = "Analyze the provided image and output a strict JSON object with keys growth_stage_description, chinese_description, image_quality_score. No extra text."
            current_user_template = "Please analyze the image provided and generate the JSON output.\n\nImage input: [image attached]\n"
            base_system = current_system
            base_user = current_user_template

        # Load data
        eval_df = self.loader.get_evaluation_set(size=sample_size)
        
        best_system = current_system
        best_score = -1.0
        
        infer_temperature = 0.4
        infer_max_tokens = 512

        for i in range(iterations):
            with mlflow.start_run(run_name=f"optimization_iter_{i}"):
                print(f"--- Iteration {i+1}/{iterations} ---")
                
                # 1. Evaluate
                prompt_messages = [
                    {"role": "system", "content": current_system},
                    {"role": "user", "content": _render_user_message(current_user_template)},
                ]
                eval_df["prompt_messages"] = [prompt_messages for _ in range(len(eval_df))]
                eval_df["prompt_uri"] = f"prompts:/{self.prompt_name}/3"
                eval_df["temperature"] = infer_temperature
                eval_df["max_tokens"] = infer_max_tokens

                predictions, usages = self.runner.predict_with_usage(eval_df)
                eval_df['prediction'] = predictions
                
                # 2. Score
                # We use our custom scorers
                json_metric = score_json_format(eval_df, None)
                schema_metric = score_schema_conformity(eval_df, None)
                
                json_scores = json_metric.scores
                schema_scores = schema_metric.scores
                
                avg_json = sum(json_scores) / len(json_scores)
                avg_schema = sum(schema_scores) / len(schema_scores)
                
                total_score = (avg_json + avg_schema) / 2
                
                mlflow.log_param("system_prompt", current_system)
                mlflow.log_param("user_prompt_template", current_user_template)
                mlflow.log_param("infer_temperature", infer_temperature)
                mlflow.log_param("infer_max_tokens", infer_max_tokens)
                mlflow.log_metric("score_json", avg_json)
                mlflow.log_metric("score_schema", avg_schema)
                mlflow.log_metric("total_score", total_score)

                token_totals = [u.get("total_tokens") for u in usages if isinstance(u, dict) and u.get("total_tokens") is not None]
                if token_totals:
                    mlflow.log_metric("avg_total_tokens", sum(token_totals) / len(token_totals))
                length_finishes = [u for u in usages if isinstance(u, dict) and u.get("finish_reason") == "length"]
                if length_finishes:
                    mlflow.log_metric("finish_reason_length_count", float(len(length_finishes)))
                
                print(f"Score: {total_score:.4f} (JSON: {avg_json:.2f}, Schema: {avg_schema:.2f})")
                
                if total_score > best_score:
                    best_score = total_score
                    best_system = current_system
                
                if total_score == 1.0:
                    print("Perfect score achieved.")
                    break
                
                # 3. Generate new prompt (Optimization Step)
                if avg_json < 0.8:
                    infer_temperature = max(0.1, infer_temperature - 0.1)
                if any(u.get("finish_reason") == "length" for u in usages if isinstance(u, dict)):
                    infer_max_tokens = min(2048, infer_max_tokens + 256)
                
                meta_prompt = f"""
                You are an expert Prompt Engineer.
                
                Original Prompt:
                "{current_system}"
                
                The task is to describe mushroom images in JSON format.
                The output JSON MUST contain these exact keys:
                - "growth_stage_description": string
                - "chinese_description": string
                - "image_quality_score": integer
                
                Evaluation metrics for the Original Prompt:
                - JSON Validity Score: {avg_json:.2f}
                - Schema Conformity Score: {avg_schema:.2f}
                
                The Original Prompt failed to produce valid JSON or missed required keys.
                
                Please write a REVISED prompt that is strictly focused on enforcing the JSON structure and keys.
                The keys 'growth_stage_description', 'chinese_description', and 'image_quality_score' are mandatory.
                Do not include 'classification_results', 'detection_results', or 'ocr_results'.
                Do not include any conversational filler. Just the prompt.
                """
                
                try:
                    response = self.runner.client.chat.completions.create(
                        model=config.model,
                        messages=[{"role": "user", "content": meta_prompt}],
                        temperature=0.7,
                    )
                    new_template = response.choices[0].message.content.strip()
                    # Cleanup if model returns quotes or markdown code blocks
                    if new_template.startswith('"') and new_template.endswith('"'):
                        new_template = new_template[1:-1]
                    if new_template.startswith("```"):
                        lines = new_template.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines[-1].startswith("```"):
                            lines = lines[:-1]
                        new_template = "\n".join(lines).strip()
                        
                    print(f"Generated new prompt: {new_template[:100]}...")
                    current_system = new_template
                except Exception as e:
                    print(f"Error generating new prompt: {e}")
                    break

        print("Optimization complete.")
        print(f"Best Score: {best_score}")
        print(f"Best System Prompt: {best_system}")
        
        best_prompt_messages = [
            {"role": "system", "content": best_system},
            {"role": "user", "content": current_user_template},
        ]

        with open("best_prompt.txt", "w") as f:
            f.write(best_system)

        with open("best_prompt_messages.json", "w") as f:
            import json
            json.dump(best_prompt_messages, f, ensure_ascii=False, indent=2)
        
        try:
            if best_system == str(base_system) and current_user_template == str(base_user):
                return best_prompt_messages
            registered = mlflow.genai.register_prompt(
                name=self.prompt_name,
                template=best_prompt_messages,
                commit_message="offline optimize (chat prompt, local dataset)",
                tags={"source": "offline.optimize", "base": "prompts:/growth_stage_describe/3"},
            )
            print(f"Registered new prompt version: prompts:/{registered.name}/{registered.version}")
        except Exception as e:
            print(f"Failed to register prompt version via registry API: {e}")

        return best_prompt_messages

if __name__ == "__main__":
    opt = Optimizer()
    opt.optimize()
