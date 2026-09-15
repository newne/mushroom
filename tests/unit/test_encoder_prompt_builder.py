from vision.encoder_llm.prompt_builder import (
    apply_prompt_length_guard,
    build_compact_v15_prompt,
    build_minimal_fallback_prompt,
)


def test_build_compact_v15_prompt_contains_required_schema_keys():
    prompt = build_compact_v15_prompt()

    assert "growth_stage_description" in prompt
    assert "chinese_description" in prompt
    assert "image_quality_score" in prompt
    assert "Primordia Stage" in prompt


def test_build_minimal_fallback_prompt_is_concise_json_instruction():
    prompt = build_minimal_fallback_prompt()

    assert "EXACTLY one JSON object" in prompt
    assert "Post-harvest Stage" in prompt
    assert len(prompt) < 400


def test_apply_prompt_length_guard_keeps_short_prompt():
    assert apply_prompt_length_guard("short prompt") == "short prompt"
    assert apply_prompt_length_guard(None) == ""


def test_apply_prompt_length_guard_compacts_long_prompt_and_notifies():
    compacted_lengths = []

    prompt = apply_prompt_length_guard(
        "x" * 2601,
        on_compacted=compacted_lengths.append,
    )

    assert prompt == build_compact_v15_prompt()
    assert compacted_lengths == [2601]