import json
import mlflow
from typing import Any, Dict
from mlflow.metrics import MetricValue, make_metric
from mlflow.metrics.genai import make_genai_metric, EvaluationExample

# Custom Scorers

import re

def extract_json(text: str) -> str:
    """Extracts JSON substring from text using regex."""
    text = str(text).strip()
    # Match ```json ... ``` or ``` ... ``` or just { ... }
    # Try to find the first { and last }
    try:
        start = text.index('{')
        end = text.rindex('}') + 1
        return text[start:end]
    except ValueError:
        return text

def score_json_format(eval_df, builtin_metrics):
    """
    Checks if the output is valid JSON.
    """
    scores = []
    justifications = []
    
    for output in eval_df["prediction"]:
        try:
            clean_output = extract_json(output)
            json.loads(clean_output)
            scores.append(1)
            justifications.append("Valid JSON")
        except (json.JSONDecodeError, TypeError) as e:
            scores.append(0)
            justifications.append(f"Invalid JSON: {str(e)}")
            if len(scores) <= 5: # Print first few errors
                print(f"DEBUG: Invalid JSON Output: {output[:200]}... Error: {e}")
            
    return MetricValue(scores=scores, justifications=justifications)

def score_schema_conformity(eval_df, builtin_metrics):
    """
    Checks if the output conforms to the expected schema.
    """
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

# Wrap functions as EvaluationMetric objects
score_json_format_metric = make_metric(
    eval_fn=score_json_format, 
    greater_is_better=True, 
    name="score_json_format"
)

score_schema_conformity_metric = make_metric(
    eval_fn=score_schema_conformity, 
    greater_is_better=True, 
    name="score_schema_conformity"
)

# LLM-based Judge for Morphology Quality
# We define a GenAI metric for this
morphology_quality = make_genai_metric(
    name="morphology_quality",
    definition=(
        "Morphology Quality measures how accurately and professionally the model describes "
        "the biological characteristics of the mushroom (cap, gill, stem, veil, ring). "
        "High scores indicate precise terminology and detailed observation."
    ),
    grading_prompt=(
        "Score the following mushroom description on a scale of 1-5 based on biological accuracy and detail.\n"
        "Output: {prediction}\n"
        "Criteria:\n"
        "1: Irrelevant or hallucinated.\n"
        "3: Basic description, some missed details.\n"
        "5: Professional, detailed, correct terminology.\n"
    ),
    examples=[
        EvaluationExample(
            input="Describe this mushroom",
            output='{"classification_results": "Agaricus", "description": "White cap, pink gills."}',
            score=3,
            justification="Correct but basic."
        )
    ],
    model="openai:/qwen3-vl-2b-instruct", # Use local model as judge if supported or default
    parameters={"temperature": 0.0}
)
