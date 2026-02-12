import json
import time
from typing import Optional

import mlflow
from loguru import logger

from global_const.global_const import settings


def _parse_prompt_uri(prompt_uri: str) -> Optional[tuple[str, str]]:
    if not isinstance(prompt_uri, str) or not prompt_uri.startswith("prompts:/"):
        return None
    parts = prompt_uri[len("prompts:/") :].strip("/").split("/")
    if len(parts) < 2:
        return None
    return parts[0], str(parts[1])


def main(prompt_uri: str = "prompts:/growth_stage_describe/4", max_results: int = 200):
    host = getattr(getattr(settings, "mlflow", None), "host", None)
    port = getattr(getattr(settings, "mlflow", None), "port", None)
    if host and port:
        mlflow.set_tracking_uri(f"http://{host}:{port}")

    exp = mlflow.get_experiment_by_name("Mushroom_Text_Quality_Task")
    if exp is None:
        raise RuntimeError("未找到实验: Mushroom_Text_Quality_Task")
    exp_id = str(exp.experiment_id)

    parsed = _parse_prompt_uri(prompt_uri)
    if parsed is None:
        raise RuntimeError(f"prompt_uri 非 prompts:/ 格式: {prompt_uri}")
    name, version = parsed
    expected = json.dumps([{"name": name, "version": str(version)}], ensure_ascii=False)

    start = time.time()
    traces = mlflow.search_traces(
        return_type="list",
        include_spans=False,
        max_results=max_results,
        locations=[exp_id],
    )
    matched = 0
    total = len(traces)
    for t in traces:
        tags = t.info.tags or {}
        if tags.get("mlflow.linkedPrompts") == expected:
            matched += 1

    elapsed = time.time() - start
    logger.info(
        f"experiment_id={exp_id} total_traces={total} matched_prompt_traces={matched} prompt_uri={prompt_uri} elapsed_s={elapsed:.3f}"
    )

    if matched == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
