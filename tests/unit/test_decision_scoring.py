from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from decision_analysis.data_models import SimilarCase
from decision_analysis.scoring.device_alignment import (
    calculate_stage_alignment_confidence,
    infer_device_type,
    normalize_device_config,
    priority_weight,
    score_against_reference,
    to_float,
)
from decision_analysis.scoring.device_mapping import map_parameter_to_point_alias
from decision_analysis.scoring.image_consistency import calculate_image_consistency_fallback


def test_normalize_device_config_accepts_dict_and_json():
    assert normalize_device_config({"temp_set": 18}) == {"temp_set": 18}
    assert normalize_device_config('{"temp_set": 18}') == {"temp_set": 18}
    assert normalize_device_config("not-json") == {}
    assert normalize_device_config(None) == {}


def test_to_float_handles_bool_and_invalid_values():
    assert to_float(True) == 1.0
    assert to_float("18.5") == 18.5
    assert to_float("bad") is None
    assert to_float(None) is None


def test_infer_device_type_supports_alias_suffixes():
    assert infer_device_type("air_cooler_611") == "air_cooler"
    assert infer_device_type("fresh_air_fan") == "fresh_air_fan"
    assert infer_device_type("unknown") is None
    assert infer_device_type(None) is None


def test_priority_weight_defaults_unknown_priority_to_low():
    assert priority_weight("low") == 1.0
    assert priority_weight("HIGH") == 2.0
    assert priority_weight("critical") == 3.0
    assert priority_weight("unexpected") == 1.0


def test_score_against_reference_handles_numeric_and_enum_values():
    assert score_against_reference(10, [10, 11, 9], None) == 1.0
    assert score_against_reference(2, [1, 1, 2, 1], None) == 0.0
    assert score_against_reference(1, [1, 1, 2, 1], None) == 1.0
    assert score_against_reference(100, [], None) == 0.0


def test_map_parameter_to_point_alias_requires_supported_point():
    assert (
        map_parameter_to_point_alias("air_cooler", "tem_set", {"temp_set"})
        == "temp_set"
    )
    assert map_parameter_to_point_alias("air_cooler", "tem_set", set()) is None
    assert map_parameter_to_point_alias("air_cooler", "missing", {"temp_set"}) is None


def test_calculate_stage_alignment_confidence_scores_matching_reference():
    recommendations = SimpleNamespace(
        devices={
            "air_cooler_611": SimpleNamespace(
                parameters={
                    "temp_set": SimpleNamespace(
                        recommended_value=18,
                        priority="high",
                    )
                }
            )
        }
    )
    similar_cases = [
        SimilarCase(
            similarity_score=90.0,
            confidence_level="high",
            room_id="611",
            growth_day=12,
            collection_time=datetime(2026, 1, 1, 10, 0, 0),
            temperature=18.0,
            humidity=90.0,
            co2=800.0,
            air_cooler_params={"temp_set": 18},
            fresh_air_params={},
            humidifier_params={},
            grow_light_params={},
        )
    ]

    confidence, details = calculate_stage_alignment_confidence(
        recommendations,
        similar_cases,
        setpoint_thresholds={"air_cooler": {"temp_set": 1}},
    )

    assert confidence == 1.0
    assert details == {
        "evaluated_params": 1,
        "matched_params": 1,
        "reference_cases": 1,
    }


def test_calculate_image_consistency_fallback_returns_average_similarity():
    embedding_df = pd.DataFrame(
        [
            {"embedding": [1.0, 0.0]},
            {"embedding": [1.0, 0.0]},
            {"embedding": [0.0, 1.0]},
        ]
    )

    score = calculate_image_consistency_fallback(embedding_df)

    assert round(score, 4) == 0.3333


def test_calculate_image_consistency_fallback_handles_single_image():
    embedding_df = pd.DataFrame([{"embedding": [1.0, 0.0]}])

    assert calculate_image_consistency_fallback(embedding_df) == 1.0