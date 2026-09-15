from types import SimpleNamespace

import pytest

from vision.encoder_llm.llama_gateway import (
    build_llama_completions_url,
    build_llama_headers,
    get_llama_extra_body,
    get_llama_generation_options,
    is_multimodal_model,
    parse_llama_json_content,
)


def test_generation_options_include_optional_sampling_fields():
    config = SimpleNamespace(
        temperature=0.2,
        max_tokens=512,
        top_p=0.8,
        top_k=20,
        repetition_penalty=1.1,
    )

    assert get_llama_generation_options(config) == {
        "temperature": 0.2,
        "max_tokens": 512,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.1,
    }


def test_extra_body_disables_qwen3_thinking():
    assert get_llama_extra_body("qwen/qwen3-vl-2b") == {
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert get_llama_extra_body("plain-model") is None


def test_headers_select_vl_key_for_multimodal_model():
    config = SimpleNamespace(api_key="basic", api_key_vl="vision")

    assert is_multimodal_model("qwen-vl")
    assert build_llama_headers(config, "qwen-vl") == {
        "Content-Type": "application/json",
        "X-API-Key": "vision",
    }


def test_headers_select_basic_key_for_text_model():
    config = SimpleNamespace(api_key="basic", api_key_vl="vision")

    assert build_llama_headers(config, "llama-text") == {
        "Content-Type": "application/json",
        "X-API-Key": "basic",
    }


def test_build_llama_completions_url_uses_template():
    config = SimpleNamespace(
        llama_host="127.0.0.1",
        llama_port="7001",
        llama_completions="http://{0}:{1}/v1/chat/completions",
    )

    assert build_llama_completions_url(config) == "http://127.0.0.1:7001/v1/chat/completions"


def test_parse_llama_json_content_accepts_markdown_and_embedded_json():
    assert parse_llama_json_content('```json\n{"score": 1}\n```') == {"score": 1}
    assert parse_llama_json_content('prefix {"score": 2} suffix') == {"score": 2}


def test_parse_llama_json_content_raises_for_missing_json():
    with pytest.raises(ValueError):
        parse_llama_json_content("no json here")