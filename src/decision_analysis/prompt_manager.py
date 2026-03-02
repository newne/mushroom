"""决策分析提示词管理模块。

功能：
- 从 MLflow Prompt Registry 加载提示词（优先）
- 必要时从远程 API 拉取提示词
- 失败时回退本地模板文件
- 自动注册到 MLflow（新增版本，不覆盖历史）
- 统一保障知识库占位区结构，避免后续代码拼接
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import requests
from loguru import logger


KB_PLACEHOLDER = "{knowledge_base_section}"
KB_BLOCK_START = "<KB_CONTENT_START>"
KB_BLOCK_END = "<KB_CONTENT_END>"


def normalize_decision_prompt_template(template_text: str) -> str:
    """标准化决策提示词模板，确保包含知识库结构化占位区。"""
    content = str(template_text or "")
    if KB_PLACEHOLDER in content:
        return content

    block = (
        "\n\n## 知识库内容（结构化占位区）\n"
        f"{KB_BLOCK_START}\n"
        f"{KB_PLACEHOLDER}\n"
        f"{KB_BLOCK_END}\n"
        "说明：该区域由系统在渲染阶段动态填充，禁止通过代码字符串拼接方式追加知识库内容。\n"
    )
    return content.rstrip() + block


def extract_user_template(prompt_template_obj: Any) -> str:
    """从 MLflow prompt template 对象中提取 user 模板文本。"""
    if isinstance(prompt_template_obj, str):
        return prompt_template_obj

    if isinstance(prompt_template_obj, list):
        for message in prompt_template_obj:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "user" and isinstance(
                message.get("content"), str
            ):
                return message["content"]

        for message in prompt_template_obj:
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]

    if isinstance(prompt_template_obj, dict):
        content = prompt_template_obj.get("content")
        if isinstance(content, str):
            return content

    return ""


class DecisionPromptRegistryService:
    """决策提示词注册与加载服务。"""

    def __init__(
        self,
        settings: Any,
        urls: Any,
        fallback_template_path: Path,
    ) -> None:
        self.settings = settings
        self.urls = urls
        self.fallback_template_path = Path(fallback_template_path)
        self._cached_template: Optional[str] = None
        self._cached_meta: dict[str, Any] = {}
        self._cached_expires_at = 0.0

    def _cache_ttl_seconds(self) -> int:
        raw = getattr(self.settings, "decision_prompt_cache_ttl_seconds", 1800)
        try:
            value = int(raw)
            return value if value > 0 else 1800
        except Exception:
            return 1800

    def _tracking_uri(self) -> Optional[str]:
        mlflow_cfg = getattr(self.settings, "mlflow", None)
        host = getattr(mlflow_cfg, "host", None)
        port = getattr(mlflow_cfg, "port", None)
        if host and port:
            return f"http://{host}:{port}"
        return None

    def _load_from_mlflow(
        self, prompt_uri: str
    ) -> Optional[tuple[str, dict[str, Any]]]:
        try:
            import mlflow
        except Exception as error:
            logger.warning(f"[DecisionPrompt] MLflow不可用: {error}")
            return None

        tracking_uri = self._tracking_uri()
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        try:
            prompt_obj = mlflow.genai.load_prompt(prompt_uri)
        except Exception as error:
            logger.warning(
                f"[DecisionPrompt] MLflow加载失败: uri={prompt_uri}, error={error}"
            )
            return None

        template_text = extract_user_template(getattr(prompt_obj, "template", ""))
        if not template_text:
            return None

        template_text = normalize_decision_prompt_template(template_text)
        meta = {
            "source": "mlflow",
            "prompt_uri": prompt_uri,
            "prompt_name": getattr(prompt_obj, "name", None),
            "prompt_version": getattr(prompt_obj, "version", None),
        }
        return template_text, meta

    def _fetch_from_api(self, api_url: str) -> Optional[str]:
        if not api_url:
            return None

        headers = {"Accept": "application/json"}
        prompt_cfg = getattr(self.settings, "prompt", None)
        backend_token = getattr(prompt_cfg, "backend_token", None)
        if backend_token:
            headers["Authorization"] = backend_token

        try:
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(
                    f"[DecisionPrompt] API请求失败: status={response.status_code}, url={api_url}"
                )
                return None
            data = response.json()
        except Exception as error:
            logger.warning(
                f"[DecisionPrompt] API读取失败: url={api_url}, error={error}"
            )
            return None

        if not isinstance(data, dict):
            return None

        if isinstance(data.get("content"), str):
            return data["content"]
        if isinstance(data.get("prompt"), str):
            return data["prompt"]

        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ["template", "content", "prompt", "instruction"]:
                if isinstance(nested.get(key), str):
                    return nested[key]
            nested_content = nested.get("content")
            if isinstance(nested_content, dict) and isinstance(
                nested_content.get("template"), str
            ):
                return nested_content["template"]

        return None

    def _register_to_mlflow(self, template_text: str) -> Optional[str]:
        register_on_start = bool(
            getattr(self.settings, "decision_prompt_register_on_start", True)
        )
        if not register_on_start:
            return None

        try:
            import mlflow
        except Exception as error:
            logger.warning(f"[DecisionPrompt] MLflow不可用，跳过注册: {error}")
            return None

        tracking_uri = self._tracking_uri()
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        prompt_name = getattr(
            self.settings,
            "decision_prompt_registry_name",
            "decision_analysis_structured",
        )

        template_messages = [
            {
                "role": "system",
                "content": "你是蘑菇种植调控专家。请严格输出结构化JSON建议。",
            },
            {
                "role": "user",
                "content": normalize_decision_prompt_template(template_text),
            },
        ]

        try:
            registered = mlflow.genai.register_prompt(
                name=str(prompt_name),
                template=template_messages,
                commit_message="safe_batch_decision_analysis prompt sync",
                tags={
                    "source": "decision_analysis.prompt_manager",
                    "task": "safe_batch_decision_analysis",
                    "schema": "kb_placeholder_v1",
                },
            )
            prompt_uri = f"prompts:/{registered.name}/{registered.version}"
            logger.info(f"[DecisionPrompt] 已注册新提示词版本: {prompt_uri}")
            return prompt_uri
        except Exception as error:
            logger.warning(f"[DecisionPrompt] 注册提示词失败: {error}")
            return None

    def resolve(self) -> tuple[str, dict[str, Any]]:
        now = time.time()
        if self._cached_template and now < self._cached_expires_at:
            return self._cached_template, self._cached_meta

        primary_source = getattr(self.urls, "prompt_decision_analysis", None)
        backup_source = getattr(self.urls, "prompt_decision_analysis_bak", None)

        template_text = None
        meta: dict[str, Any] = {"source": "unknown", "prompt_uri": None}

        # 1) MLflow Prompt URI
        if isinstance(primary_source, str) and primary_source.startswith("prompts:/"):
            loaded = self._load_from_mlflow(primary_source)
            if loaded:
                template_text, meta = loaded

        # 2) API fallback
        if template_text is None:
            for candidate in [primary_source, backup_source]:
                if not isinstance(candidate, str) or not candidate:
                    continue
                if candidate.startswith("prompts:/"):
                    continue
                host_cfg = getattr(self.settings, "host", None)
                host = getattr(host_cfg, "host", "") if host_cfg else ""
                port = getattr(host_cfg, "port", "") if host_cfg else ""
                try:
                    url = candidate.format(host=host, port=port)
                except Exception:
                    url = candidate
                fetched = self._fetch_from_api(url)
                if fetched:
                    template_text = normalize_decision_prompt_template(fetched)
                    meta = {"source": "api", "prompt_uri": None, "api_url": url}
                    break

        # 3) Local template fallback
        if template_text is None:
            template_text = normalize_decision_prompt_template(
                self.fallback_template_path.read_text(encoding="utf-8")
            )
            meta = {
                "source": "local",
                "prompt_uri": None,
                "template_path": str(self.fallback_template_path),
            }

        # 4) Register as new MLflow prompt version (non-destructive)
        registered_uri = self._register_to_mlflow(template_text)
        if registered_uri:
            meta["registered_prompt_uri"] = registered_uri

        self._cached_template = template_text
        self._cached_meta = meta
        self._cached_expires_at = now + self._cache_ttl_seconds()
        return template_text, meta


_PROMPT_SERVICE_CACHE: dict[str, DecisionPromptRegistryService] = {}


def resolve_decision_prompt_template(
    settings: Any,
    urls: Any,
    fallback_template_path: Path,
) -> tuple[str, dict[str, Any]]:
    """解析决策提示词模板（带进程内缓存）。"""
    cache_key = str(fallback_template_path)
    service = _PROMPT_SERVICE_CACHE.get(cache_key)
    if service is None:
        service = DecisionPromptRegistryService(
            settings=settings,
            urls=urls,
            fallback_template_path=fallback_template_path,
        )
        _PROMPT_SERVICE_CACHE[cache_key] = service

    return service.resolve()
