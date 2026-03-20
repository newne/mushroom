import mlflow

from src.global_const.global_const import settings
from utils import log_task_event

from .config import config
from .dataset import DatasetLoader
from .runner import Runner
from .scorers import score_json_format, score_schema_conformity


def _optimize_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_OPTIMIZE",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


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
        _optimize_log(
            "VISION_OFFLINE_OPTIMIZE_START",
            "开始执行 prompt 优化",
            prompt_name=self.prompt_name,
            iterations=iterations,
            sample_size=sample_size,
            status="running",
        )

        try:
            import mlflow.openai as mlflow_openai

            mlflow_openai.autolog()
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_AUTOLOG_READY",
                "MLflow OpenAI autolog 已启用",
                status="success",
            )
        except Exception as e:
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_AUTOLOG_FAILED",
                "MLflow OpenAI autolog 启用失败",
                level="WARNING",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

        def _render_user_message(user_template: str) -> str:
            text = str(user_template)
            if "{% if image_input %}" in text:
                text = (
                    text.replace("{% if image_input %}", "")
                    .replace("{% else %}", "")
                    .replace("{% endif %}", "")
                )
                text = text.replace("{{ image_input }}", "[image attached]")
            return text.strip()

        try:
            prompt_obj = mlflow.genai.load_prompt(f"prompts:/{self.prompt_name}/3")
            template_messages = prompt_obj.template
            base_system = next(
                (
                    m.get("content")
                    for m in template_messages
                    if m.get("role") == "system"
                ),
                "",
            )
            base_user = next(
                (
                    m.get("content")
                    for m in template_messages
                    if m.get("role") == "user"
                ),
                "Please analyze the image.",
            )
            current_system = str(base_system)
            current_user_template = str(base_user)
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_PROMPT_READY",
                "已从 registry 加载优化基线 prompt",
                prompt_name=self.prompt_name,
                status="success",
            )
        except Exception as e:
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_PROMPT_FALLBACK",
                "加载优化基线 prompt 失败，回退默认 prompt",
                level="WARNING",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
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
                _optimize_log(
                    "VISION_OFFLINE_OPTIMIZE_ITERATION_START",
                    "开始执行 prompt 优化迭代",
                    iteration=i + 1,
                    total_iterations=iterations,
                    status="running",
                )

                # 1. Evaluate
                prompt_messages = [
                    {"role": "system", "content": current_system},
                    {
                        "role": "user",
                        "content": _render_user_message(current_user_template),
                    },
                ]
                eval_df["prompt_messages"] = [
                    prompt_messages for _ in range(len(eval_df))
                ]
                eval_df["prompt_uri"] = f"prompts:/{self.prompt_name}/3"
                eval_df["temperature"] = infer_temperature
                eval_df["max_tokens"] = infer_max_tokens

                predictions, usages = self.runner.predict_with_usage(eval_df)
                eval_df["prediction"] = predictions

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

                token_totals = [
                    u.get("total_tokens")
                    for u in usages
                    if isinstance(u, dict) and u.get("total_tokens") is not None
                ]
                if token_totals:
                    mlflow.log_metric(
                        "avg_total_tokens", sum(token_totals) / len(token_totals)
                    )
                length_finishes = [
                    u
                    for u in usages
                    if isinstance(u, dict) and u.get("finish_reason") == "length"
                ]
                if length_finishes:
                    mlflow.log_metric(
                        "finish_reason_length_count", float(len(length_finishes))
                    )

                _optimize_log(
                    "VISION_OFFLINE_OPTIMIZE_ITERATION_SCORE",
                    "prompt 优化迭代得分",
                    iteration=i + 1,
                    total_score=round(total_score, 4),
                    avg_json=round(avg_json, 4),
                    avg_schema=round(avg_schema, 4),
                    status="success",
                )

                if total_score > best_score:
                    best_score = total_score
                    best_system = current_system

                if total_score == 1.0:
                    _optimize_log(
                        "VISION_OFFLINE_OPTIMIZE_PERFECT_SCORE",
                        "已达到满分，提前结束优化",
                        iteration=i + 1,
                        status="success",
                    )
                    break

                # 3. Generate new prompt (Optimization Step)
                if avg_json < 0.8:
                    infer_temperature = max(0.1, infer_temperature - 0.1)
                if any(
                    u.get("finish_reason") == "length"
                    for u in usages
                    if isinstance(u, dict)
                ):
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

                    _optimize_log(
                        "VISION_OFFLINE_OPTIMIZE_NEW_PROMPT",
                        "已生成新的 prompt 候选",
                        iteration=i + 1,
                        prompt_preview=new_template[:100],
                        status="success",
                    )
                    current_system = new_template
                except Exception as e:
                    _optimize_log(
                        "VISION_OFFLINE_OPTIMIZE_GENERATE_FAILED",
                        "生成新 prompt 失败",
                        level="ERROR",
                        iteration=i + 1,
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    break

        _optimize_log(
            "VISION_OFFLINE_OPTIMIZE_FINISH",
            "prompt 优化完成",
            best_score=best_score,
            best_prompt_preview=best_system[:120],
            status="success",
        )

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
            if best_system == str(base_system) and current_user_template == str(
                base_user
            ):
                return best_prompt_messages
            base_prompt = getattr(
                settings.data_source_url, "prompt_mushroom_description", None
            )
            if not base_prompt:
                raise ValueError(
                    "prompt_mushroom_description is not configured in settings.toml"
                )
            registered = mlflow.genai.register_prompt(
                name=self.prompt_name,
                template=best_prompt_messages,
                commit_message="offline optimize (chat prompt, local dataset)",
                tags={"source": "offline.optimize", "base": base_prompt},
            )
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_PROMPT_REGISTERED",
                "已注册新的 prompt 版本",
                prompt_uri=f"prompts:/{registered.name}/{registered.version}",
                status="success",
            )
        except Exception as e:
            _optimize_log(
                "VISION_OFFLINE_OPTIMIZE_REGISTER_FAILED",
                "通过 registry API 注册 prompt 版本失败",
                level="WARNING",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

        return best_prompt_messages


if __name__ == "__main__":
    opt = Optimizer()
    opt.optimize()
