import json
from collections import Counter
from statistics import mean, pstdev
from typing import Any, Dict

from decision_analysis.data_models import EnhancedDeviceRecommendations, SimilarCase


DEVICE_TYPES = ("air_cooler", "fresh_air_fan", "humidifier", "grow_light")


def normalize_device_config(raw_config: Any) -> Dict:
    if raw_config is None:
        return {}
    if isinstance(raw_config, dict):
        return raw_config
    if isinstance(raw_config, str):
        try:
            parsed = json.loads(raw_config)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        return float(value)
    except Exception:
        return None


def infer_device_type(device_key: str) -> str | None:
    if not isinstance(device_key, str):
        return None
    for device_type in DEVICE_TYPES:
        if device_key == device_type or device_key.startswith(f"{device_type}_"):
            return device_type
    return None


def priority_weight(priority: str) -> float:
    mapping = {
        "low": 1.0,
        "medium": 1.5,
        "high": 2.0,
        "critical": 3.0,
    }
    return mapping.get(str(priority).lower(), 1.0)


def score_against_reference(
    value: float, values: list[float], threshold: float | None
) -> float:
    if not values:
        return 0.0

    try:
        unique_vals = {int(v) if float(v).is_integer() else v for v in values}
        is_enum = all(float(v).is_integer() for v in values) and len(unique_vals) <= 5
        if is_enum:
            mode_val = Counter(int(v) for v in values).most_common(1)[0][0]
            return 1.0 if int(round(value)) == mode_val else 0.0

        avg = mean(values)
        std = pstdev(values) if len(values) > 1 else 0.0
        base_tol = float(threshold) if threshold not in (None, 0) else 0.0
        rel_tol = abs(avg) * 0.05
        tol = max(std * 2.0, base_tol, rel_tol, 1.0)
        if tol <= 0:
            return 0.0
        score = 1.0 - abs(value - avg) / tol
        return max(0.0, min(score, 1.0))
    except Exception:
        return 0.0


def calculate_stage_alignment_confidence(
    device_recommendations: EnhancedDeviceRecommendations,
    similar_cases: list[SimilarCase],
    setpoint_thresholds: dict | None = None,
) -> tuple[float, Dict]:
    if not similar_cases or not device_recommendations:
        return 0.0, {"reason": "insufficient_reference_data"}

    if not getattr(device_recommendations, "devices", None):
        return 0.0, {"reason": "no_device_recommendations"}

    ref_values: Dict[str, Dict[str, list[float]]] = {}
    for case in similar_cases:
        for device_type, raw_config in [
            ("air_cooler", case.air_cooler_params),
            ("fresh_air_fan", case.fresh_air_params),
            ("humidifier", case.humidifier_params),
            ("grow_light", case.grow_light_params),
        ]:
            config = normalize_device_config(raw_config)
            if not config:
                continue
            for point_alias, value in config.items():
                num_value = to_float(value)
                if num_value is None:
                    continue
                ref_values.setdefault(device_type, {}).setdefault(point_alias, []).append(
                    num_value
                )

    if not ref_values:
        return 0.0, {"reason": "no_reference_values"}

    total_weight = 0.0
    total_score = 0.0
    matched_params = 0
    evaluated_params = 0
    thresholds = setpoint_thresholds or {}

    for device_key, device_rec in device_recommendations.devices.items():
        device_type = infer_device_type(device_key)
        if not device_type or device_type not in ref_values:
            continue

        for point_alias, param in device_rec.parameters.items():
            if not hasattr(param, "recommended_value"):
                continue
            evaluated_params += 1
            rec_value = to_float(param.recommended_value)
            if rec_value is None:
                continue

            values = ref_values.get(device_type, {}).get(point_alias, [])
            if not values:
                continue

            threshold = (
                thresholds.get(device_type, {}).get(point_alias)
                if isinstance(thresholds, dict)
                else None
            )
            score = score_against_reference(rec_value, values, threshold)
            weight = priority_weight(getattr(param, "priority", "low"))
            total_score += score * weight
            total_weight += weight
            matched_params += 1

    if total_weight == 0.0:
        return 0.0, {
            "reason": "no_comparable_parameters",
            "evaluated_params": evaluated_params,
            "matched_params": matched_params,
            "reference_cases": len(similar_cases),
        }

    confidence = total_score / total_weight
    return round(max(0.0, min(confidence, 1.0)), 4), {
        "evaluated_params": evaluated_params,
        "matched_params": matched_params,
        "reference_cases": len(similar_cases),
    }