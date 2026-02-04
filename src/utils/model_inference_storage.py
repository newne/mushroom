"""Deprecated module.

Model inference results are no longer stored in model_inference_results.
Decision analysis persistence now uses decision_analysis_static_config and
decision_analysis_dynamic_result.
"""

raise RuntimeError(
    "model_inference_storage has been removed. Use decision_analysis_* tables instead."
)
