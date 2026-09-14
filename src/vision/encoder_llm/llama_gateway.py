import json
import re
from typing import Any


def get_llama_generation_options(llama_config: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "temperature": getattr(llama_config, "temperature", 0.7),
        "max_tokens": getattr(llama_config, "max_tokens", 1024),
        "top_p": getattr(llama_config, "top_p", 0.9),
    }

    for key in (
        "top_k",
        "min_p",
        "presence_penalty",
        "repetition_penalty",
    ):
        value = getattr(llama_config, key, None)
        if value is not None:
            options[key] = value

    return options


def get_llama_extra_body(model_name: str) -> dict[str, Any] | None:
    model_lower = str(model_name).lower()
    if model_lower == "llama-mushroom-medium" or "qwen3" in model_lower:
        return {
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    return None


def is_multimodal_model(model_name: str) -> bool:
    model_lower = str(model_name).lower()
    return any(keyword in model_lower for keyword in ("vl", "vision", "qvq"))


def select_llama_api_key(llama_config: Any, model_name: str) -> str | None:
    if is_multimodal_model(model_name):
        return getattr(llama_config, "api_key_vl", None) or getattr(
            llama_config, "api_key", None
        )
    return getattr(llama_config, "api_key", None) or getattr(
        llama_config, "api_key_vl", None
    )


def build_llama_headers(llama_config: Any, model_name: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = select_llama_api_key(llama_config, model_name)
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def build_llama_completions_url(llama_config: Any) -> str:
    host = getattr(llama_config, "llama_host", "localhost")
    port = getattr(llama_config, "llama_port", "7001")
    base_url_template = getattr(
        llama_config,
        "llama_completions",
        "http://{0}:{1}/v1/chat/completions",
    )
    return base_url_template.format(host, port)


def parse_llama_json_content(content: str) -> dict[str, Any]:
    content = str(content)
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            raise
        return json.loads(match.group(0))
