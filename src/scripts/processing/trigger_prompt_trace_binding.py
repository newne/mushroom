import base64
import io
import json
import time
from pathlib import Path

import mlflow
import requests
from PIL import Image
from loguru import logger

from global_const.global_const import settings
from utils.get_data import GetData


def _build_messages(prompt, image_data: str):
    messages = []
    last_user_index = None

    if isinstance(prompt, list):
        for msg in prompt:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                messages.append({"role": "system", "content": str(content)})
            elif role == "user":
                messages.append({"role": "user", "content": str(content)})
                last_user_index = len(messages) - 1
            elif role == "assistant":
                messages.append({"role": "assistant", "content": str(content)})
            else:
                messages.append({"role": str(role), "content": str(content)})
                if role == "user":
                    last_user_index = len(messages) - 1
    else:
        messages.append({"role": "system", "content": str(prompt)})
        messages.append({"role": "user", "content": ""})
        last_user_index = 1

    if last_user_index is None:
        messages.append({"role": "user", "content": ""})
        last_user_index = len(messages) - 1

    user_content = messages[last_user_index].get("content", "")
    if isinstance(user_content, list):
        typed_user_content = user_content
    else:
        typed_user_content = [{"type": "text", "text": str(user_content)}] if str(user_content).strip() else []

    typed_user_content.append(
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
    )
    messages[last_user_index]["content"] = typed_user_content
    return messages


def main():
    host = getattr(getattr(settings, "mlflow", None), "host", None)
    port = getattr(getattr(settings, "mlflow", None), "port", None)
    if host and port:
        mlflow.set_tracking_uri(f"http://{host}:{port}")

    exp = mlflow.set_experiment("Mushroom_Text_Quality_Task")
    exp_id = str(exp.experiment_id)

    dataset_dir = Path("data/dataset_v6")
    images = list(dataset_dir.glob("*.jpg"))
    if not images:
        raise RuntimeError("data/dataset_v6 下没有找到 jpg 图片")
    image_path = images[0]

    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port,
    )
    prompt = get_data.get_mushroom_prompt()
    meta = get_data.get_cached_prompt_meta()
    logger.info(f"prompt_meta={meta}")

    with Image.open(image_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")

    llama_cfg = settings.llama_vl if hasattr(settings, "llama_vl") else settings.llama
    host = getattr(llama_cfg, "llama_host", "localhost")
    port = getattr(llama_cfg, "llama_port", "7001")
    url_tmpl = getattr(llama_cfg, "llama_completions", "http://{0}:{1}/v1/chat/completions")
    url = url_tmpl.format(host, port)

    model = getattr(llama_cfg, "model", "qwen3-vl-4b")
    temperature = float(getattr(llama_cfg, "temperature", 0.2))
    max_tokens = int(getattr(llama_cfg, "max_tokens", 512))
    top_p = float(getattr(llama_cfg, "top_p", 0.9))
    timeout = getattr(llama_cfg, "timeout", 600)

    linked_prompts = None
    src = meta.get("source")
    if isinstance(src, str) and src.startswith("prompts:/"):
        parts = src[len("prompts:/") :].strip("/").split("/")
        if len(parts) >= 2:
            linked_prompts = json.dumps([{"name": parts[0], "version": str(parts[1])}], ensure_ascii=False)

    from mlflow.tracing.utils.prompt import TraceTagKey

    with mlflow.start_run(run_name="trigger_prompt_trace_binding") as run:
        mlflow.log_param("prompt_source", str(src))
        mlflow.log_param("image", image_path.name)

        with mlflow.start_span(name="llama_chat_completions", span_type="LLM"):
            trace_id = mlflow.get_active_trace_id()
            if trace_id and linked_prompts is not None:
                mlflow.set_trace_tag(trace_id, TraceTagKey.LINKED_PROMPTS, linked_prompts)

            payload = {
                "model": model,
                "messages": _build_messages(prompt, image_data=image_data),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": False,
            }
            headers = {"Content-Type": "application/json"}
            api_key = getattr(llama_cfg, "api_key_vl", None) or getattr(llama_cfg, "api_key", None)
            if api_key:
                headers["X-API-Key"] = api_key

            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()

        time.sleep(1.0)
        traces = mlflow.search_traces(
            run_id=run.info.run_id,
            return_type="list",
            include_spans=False,
            max_results=20,
            locations=[exp_id],
        )
        linked = [t.info.tags.get("mlflow.linkedPrompts") for t in traces if t.info.tags]
        logger.info(f"run_id={run.info.run_id} trace_count={len(traces)} linkedPrompts={linked[:5]}")
        if linked_prompts is not None:
            if not any(v == linked_prompts for v in linked if v is not None):
                raise RuntimeError("未检测到 linkedPrompts 绑定到该 run 的 traces 中")


if __name__ == "__main__":
    main()
