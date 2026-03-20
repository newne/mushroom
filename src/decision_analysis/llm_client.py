"""
LLM Client Module

This module handles communication with the LLaMA API for generating
decision recommendations based on rendered prompts.
"""

import json
import re
from typing import Any, Dict, Optional

import requests
from dynaconf import Dynaconf

from utils import log_task_event


def _llm_log(event: str, message: str, level: str = "INFO", **context: Any) -> None:
    """输出统一的决策分析 LLM 辅助事件日志。"""
    log_task_event(
        "DECISION_ANALYSIS",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


class LLMClient:
    """
    LLaMA API client for decision generation

    Handles API communication, response parsing, and error handling
    for LLM-based decision generation.
    """

    def __init__(self, settings: Dynaconf):
        """
        Initialize LLM client

        Args:
            settings: Dynaconf configuration object

        Requirements: 7.1
        """
        self.settings = settings

        # Extract LLaMA configuration
        self.llama_host = settings.llama.llama_host
        self.llama_port = settings.llama.llama_port
        self.model = settings.llama.model
        self.timeout = settings.llama.get("timeout", 600)

        # Build API endpoint
        self.api_url = settings.llama.llama_completions.format(
            self.llama_host, self.llama_port
        )

        _llm_log(
            "DECISION_LLM_CLIENT_INIT",
            "LLM 客户端初始化完成",
            model_name=self.model,
            endpoint=self.api_url,
            status="success",
        )

    def _get_model_extra_body(self) -> Optional[Dict[str, Any]]:
        """Return model-specific request options for compatible backends."""
        model_lower = str(self.model).lower()
        if model_lower == "llama-mushroom-medium" or "qwen3" in model_lower:
            return {
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        return None

    def _get_generation_options(
        self,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build request generation options from llama config and call overrides."""
        llama_settings = self.settings.llama
        options: Dict[str, Any] = {
            "temperature": (
                llama_settings.get("temperature", 0.7)
                if temperature is None
                else temperature
            ),
        }

        resolved_max_tokens = (
            llama_settings.get("max_tokens") if max_tokens is None else max_tokens
        )
        if resolved_max_tokens is not None and resolved_max_tokens > 0:
            options["max_tokens"] = resolved_max_tokens

        for key in (
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "repetition_penalty",
        ):
            value = llama_settings.get(key)
            if value is not None:
                options[key] = value

        return options

    def _sanitize_llm_response_text(self, response_text: str) -> str:
        """Strip reasoning wrappers and markdown fences before JSON parsing."""
        sanitized = (response_text or "").strip().lstrip("\ufeff")
        sanitized = re.sub(r"<think>.*?</think>", "", sanitized, flags=re.DOTALL)
        sanitized = re.sub(r"^```json\s*", "", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"^```\s*", "", sanitized)
        sanitized = re.sub(r"\s*```$", "", sanitized)
        return sanitized.strip()

    def _looks_like_truncated_json(self, response_text: str) -> bool:
        """Heuristically detect responses that likely ended before JSON completion."""
        text = (response_text or "").strip()
        if not text:
            return False
        if text.startswith("{") and not text.endswith("}"):
            return True
        if text.count("{") > text.count("}"):
            return True
        if text.count('"') % 2 == 1:
            return True
        return False

    def _parse_enhanced_candidate(self, candidate_text: str) -> Optional[Dict]:
        """Try parsing one enhanced JSON candidate and normalize its structure."""
        if not candidate_text:
            return None

        try:
            decision = json.loads(candidate_text)
        except json.JSONDecodeError:
            return None

        if self._validate_enhanced_structure(decision):
            return decision
        return self._convert_to_enhanced_format(decision)

    def _repair_truncated_json_tail(self, response_text: str) -> Optional[str]:
        """Trim to the last safe JSON boundary and close any open containers."""
        text = self._sanitize_llm_response_text(response_text)
        start_idx = text.find("{")
        if start_idx == -1:
            return None

        text = text[start_idx:]
        stack: list[str] = []
        in_string = False
        escape = False
        last_boundary_index: Optional[int] = None
        last_boundary_stack: Optional[list[str]] = None

        for index, char in enumerate(text):
            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                stack.append("}")
                continue

            if char == "[":
                stack.append("]")
                continue

            if char in "}]":
                if stack and stack[-1] == char:
                    stack.pop()
                    last_boundary_index = index + 1
                    last_boundary_stack = stack.copy()
                continue

            if char == ",":
                last_boundary_index = index
                last_boundary_stack = stack.copy()

        if last_boundary_index is None or last_boundary_stack is None:
            return None

        repaired = text[:last_boundary_index].rstrip(", \n\r\t")
        if not repaired:
            return None

        repaired += "".join(reversed(last_boundary_stack))
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)

        if repaired == text:
            return None
        return repaired

    def _was_tail_salvaged(self, decision: Dict[str, Any]) -> bool:
        """Return True when enhanced output was recovered from truncated first-pass JSON."""
        if not isinstance(decision, dict):
            return False
        metadata = decision.get("metadata", {})
        return bool(isinstance(metadata, dict) and metadata.get("tail_salvaged"))

    def _was_structure_converted(self, decision: Dict[str, Any]) -> bool:
        """Return True when output only survived by conversion from a weaker structure."""
        if not isinstance(decision, dict):
            return False
        metadata = decision.get("metadata", {})
        return bool(
            isinstance(metadata, dict) and metadata.get("converted_from_regular_format")
        )

    def generate_decision(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict:
        """
        Call LLM to generate decision recommendations

        Args:
            prompt: Rendered decision prompt
            temperature: Temperature parameter for generation
            max_tokens: Maximum tokens to generate (-1 for unlimited)

        Returns:
            Parsed decision dictionary

        Requirements: 7.1, 7.2, 7.3, 7.5
        """
        _llm_log(
            "DECISION_LLM_REQUEST_START",
            "开始生成决策建议",
            model_name=self.model,
            status="running",
        )

        try:
            # Build request payload
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": prompt}],
                "stream": False,
            }
            payload.update(
                self._get_generation_options(
                    temperature=temperature, max_tokens=max_tokens
                )
            )

            extra_body = self._get_model_extra_body()
            if extra_body:
                payload["extra_body"] = extra_body

            _llm_log(
                "DECISION_LLM_REQUEST_PREPARED",
                "已构建决策请求负载",
                level="DEBUG",
                endpoint=self.api_url,
                model_name=self.model,
                temperature=payload.get("temperature"),
                max_tokens=payload.get("max_tokens"),
            )

            # Prepare headers with API key if available
            headers = {"Content-Type": "application/json"}

            # Add API key if configured
            # First check for VL-specific API key, then fall back to general API key
            if hasattr(self.settings, "llama_vl") and hasattr(
                self.settings.llama_vl, "api_key_vl"
            ):
                headers["X-API-Key"] = self.settings.llama_vl.api_key_vl
            elif hasattr(self.settings, "llama") and hasattr(
                self.settings.llama, "api_key_vl"
            ):
                headers["X-API-Key"] = self.settings.llama.api_key_vl
            elif hasattr(self.settings, "llama") and hasattr(
                self.settings.llama, "api_key"
            ):
                headers["X-API-Key"] = self.settings.llama.api_key
            elif hasattr(self.settings, "llama_vl") and hasattr(
                self.settings.llama_vl, "api_key"
            ):
                headers["X-API-Key"] = self.settings.llama_vl.api_key

            # Send POST request with timeout and headers
            response = requests.post(
                self.api_url, json=payload, headers=headers, timeout=self.timeout
            )

            # Check response status
            if response.status_code != 200:
                error_msg = (
                    f"LLM API returned status {response.status_code}: {response.text}"
                )
                _llm_log(
                    "DECISION_LLM_REQUEST_FAILED",
                    "决策 LLM 接口返回非 200 状态",
                    level="ERROR",
                    status="failed",
                    error_code=f"http_{response.status_code}",
                    error_message=error_msg,
                )
                return self._get_fallback_decision(f"API error: {response.status_code}")

            # Parse response JSON
            response_data = response.json()

            # Extract content from response
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                _llm_log(
                    "DECISION_LLM_NO_CHOICES",
                    "决策 LLM 响应中缺少 choices",
                    level="ERROR",
                    status="failed",
                )
                return self._get_fallback_decision("No choices in response")

            content = response_data["choices"][0].get("message", {}).get("content", "")

            # Detailed logging for debugging
            _llm_log(
                "DECISION_LLM_RESPONSE_RECEIVED",
                "已收到决策 LLM 响应",
                total_items=len(content),
                status="success",
            )

            if not content:
                _llm_log(
                    "DECISION_LLM_EMPTY_CONTENT",
                    "决策 LLM 响应内容为空",
                    level="ERROR",
                    status="failed",
                    response_keys=list(response_data.keys()),
                    choice_keys=list(response_data["choices"][0].keys())
                    if "choices" in response_data and len(response_data["choices"]) > 0
                    else None,
                )
                return self._get_fallback_decision("Empty content")

            if len(content) < 50:
                _llm_log(
                    "DECISION_LLM_RESPONSE_SHORT",
                    "决策 LLM 响应较短，可能不完整",
                    level="WARNING",
                    status="partial",
                    total_items=len(content),
                    response_preview=content,
                )
            else:
                _llm_log(
                    "DECISION_LLM_RESPONSE_PREVIEW",
                    "决策 LLM 响应预览",
                    level="DEBUG",
                    total_items=len(content),
                    response_preview=content[:150],
                )

            # Parse the response content
            parsed_decision = self._parse_response(content)

            return parsed_decision

        except requests.exceptions.Timeout:
            _llm_log(
                "DECISION_LLM_TIMEOUT",
                "决策 LLM 请求超时",
                level="ERROR",
                status="failed",
                error_message=f"timeout after {self.timeout} seconds",
            )
            return self._get_fallback_decision("Timeout")

        except requests.exceptions.ConnectionError as e:
            _llm_log(
                "DECISION_LLM_CONNECTION_ERROR",
                "决策 LLM 连接失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_fallback_decision("Connection error")

        except requests.exceptions.RequestException as e:
            _llm_log(
                "DECISION_LLM_REQUEST_EXCEPTION",
                "决策 LLM 请求异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_fallback_decision(f"Request error: {str(e)}")

        except Exception as e:
            _llm_log(
                "DECISION_LLM_UNEXPECTED_ERROR",
                "决策 LLM 调用发生未预期异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_fallback_decision(f"Unexpected error: {str(e)}")

    def _parse_response(self, response_text: str) -> Dict:
        """
        Parse LLM response text

        Extracts structured decision data from LLM JSON response.
        Handles format errors and attempts to correct them.

        Args:
            response_text: LLM response text

        Returns:
            Structured decision dictionary

        Requirements: 7.5
        """
        _llm_log("DECISION_LLM_PARSE_START", "开始解析决策 LLM 响应", status="running")

        # Check for empty response
        if not response_text or not response_text.strip():
            _llm_log(
                "DECISION_LLM_PARSE_EMPTY",
                "决策 LLM 响应为空白文本",
                level="ERROR",
                status="failed",
            )
            return self._get_fallback_decision("Empty response")

        # Strip whitespace and common reasoning wrappers
        response_text = self._sanitize_llm_response_text(response_text)

        # Log response characteristics
        _llm_log(
            "DECISION_LLM_PARSE_PROFILE",
            "决策 LLM 响应解析画像",
            level="DEBUG",
            total_items=len(response_text),
            response_preview=response_text[:50],
        )

        try:
            # Try to parse as JSON directly
            decision = json.loads(response_text)
            _llm_log(
                "DECISION_LLM_PARSE_DIRECT_OK", "直接 JSON 解析成功", status="success"
            )
            return decision

        except json.JSONDecodeError as e:
            _llm_log(
                "DECISION_LLM_PARSE_RETRY",
                "首次 JSON 解析失败，尝试从文本中提取 JSON",
                level="WARNING",
                status="retrying",
                error_message=str(e),
                error_line=e.lineno,
                error_column=e.colno,
            )

            # Try to extract JSON from markdown code blocks
            import re

            # Look for JSON in code blocks (```json ... ``` or ``` ... ```)
            json_block_patterns = [
                r"```json\s*(\{.*?\})\s*```",  # ```json {...} ```
                r"```\s*(\{.*?\})\s*```",  # ``` {...} ```
            ]

            for pattern in json_block_patterns:
                matches = re.findall(pattern, response_text, re.DOTALL)
                if matches:
                    for match in matches:
                        try:
                            decision = json.loads(match)
                            _llm_log(
                                "DECISION_LLM_PARSE_CODEBLOCK_OK",
                                "从 Markdown 代码块提取 JSON 成功",
                                status="success",
                            )
                            return decision
                        except json.JSONDecodeError:
                            continue

            # Try to find JSON object in the text using bracket matching
            json_objects = self._extract_json_objects(response_text)

            if json_objects:
                # Try the longest match first (likely to be the complete object)
                for obj_text in sorted(json_objects, key=len, reverse=True):
                    try:
                        decision = json.loads(obj_text)
                        _llm_log(
                            "DECISION_LLM_PARSE_BRACKET_OK",
                            "通过括号匹配提取 JSON 成功",
                            status="success",
                        )
                        return decision
                    except json.JSONDecodeError:
                        continue

            # If all parsing attempts fail, log the response and return fallback
            _llm_log(
                "DECISION_LLM_PARSE_FAILED",
                "多轮尝试后仍无法解析决策 LLM 响应",
                level="ERROR",
                status="failed",
                total_items=len(response_text),
                response_preview=response_text[:500],
            )
            return self._get_fallback_decision("JSON parse error")

        except Exception as e:
            _llm_log(
                "DECISION_LLM_PARSE_EXCEPTION",
                "解析决策 LLM 响应时发生未预期异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_fallback_decision(f"Parse error: {str(e)}")

    def _extract_json_objects(self, text: str) -> list:
        """
        Extract JSON objects from text using bracket matching

        This method is more robust than regex for nested JSON structures.

        Args:
            text: Text containing JSON objects

        Returns:
            List of JSON object strings
        """
        objects = []
        depth = 0
        start = None
        in_string = False
        escape = False

        for i, char in enumerate(text):
            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    obj_text = text[start : i + 1]
                    objects.append(obj_text)
                    start = None

        return objects

    def _get_fallback_decision(self, error_reason: str) -> Dict:
        """
        Generate fallback decision when LLM call fails

        Returns a conservative rule-based decision to maintain system stability.

        Args:
            error_reason: Reason for fallback

        Returns:
            Dict: Fallback decision with conservative parameters

        Requirements: 9.2
        """
        _llm_log(
            "DECISION_LLM_FALLBACK",
            "启用常规回退决策策略",
            level="WARNING",
            status="fallback",
            error_message=error_reason,
        )

        # Return a conservative fallback decision
        fallback = {
            "status": "fallback",
            "error_reason": error_reason,
            "strategy": {
                "core_objective": "维持当前环境稳定（LLM服务不可用，使用保守策略）",
                "priority_ranking": ["温度控制", "湿度控制", "CO2控制"],
                "key_risk_points": [
                    f"LLM服务不可用: {error_reason}",
                    "使用基于规则的默认策略",
                    "建议人工审核当前参数",
                ],
            },
            "device_recommendations": {
                "air_cooler": {
                    "tem_set": None,  # Keep current
                    "tem_diff_set": None,
                    "cyc_on_off": None,
                    "cyc_on_time": None,
                    "cyc_off_time": None,
                    "ar_on_off": None,
                    "hum_on_off": None,
                    "rationale": [
                        "LLM服务不可用，保持当前冷风机设定",
                        "建议人工检查温度是否在合理范围内",
                    ],
                },
                "fresh_air_fan": {
                    "model": None,
                    "control": None,
                    "co2_on": None,
                    "co2_off": None,
                    "on": None,
                    "off": None,
                    "rationale": [
                        "LLM服务不可用，保持当前新风机设定",
                        "建议人工检查CO2浓度是否在合理范围内",
                    ],
                },
                "humidifier": {
                    "model": None,
                    "on": None,
                    "off": None,
                    "left_right_strategy": "保持当前设定",
                    "rationale": [
                        "LLM服务不可用，保持当前加湿器设定",
                        "建议人工检查湿度是否在合理范围内",
                    ],
                },
                "grow_light": {
                    "model": None,
                    "on_mset": None,
                    "off_mset": None,
                    "on_off_1": None,
                    "choose_1": None,
                    "on_off_2": None,
                    "choose_2": None,
                    "on_off_3": None,
                    "choose_3": None,
                    "on_off_4": None,
                    "choose_4": None,
                    "rationale": [
                        "LLM服务不可用，保持当前补光灯设定",
                        "建议人工检查光照是否符合生长阶段需求",
                    ],
                },
            },
            "monitoring_points": {
                "key_time_periods": ["全天候监控（LLM服务不可用期间）"],
                "warning_thresholds": {
                    "temperature": "根据当前生长阶段设定",
                    "humidity": "根据当前生长阶段设定",
                    "co2": "根据当前生长阶段设定",
                },
                "emergency_measures": [
                    "如环境参数异常，立即人工介入",
                    "尽快恢复LLM服务或使用人工决策",
                ],
            },
            "metadata": {
                "warnings": [
                    f"LLM调用失败: {error_reason}",
                    "使用降级策略，所有设备参数保持当前值",
                    "强烈建议人工审核和介入",
                ],
                "llm_available": False,
            },
        }

        return fallback

    def generate_enhanced_decision(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict:
        """
        Call LLM to generate enhanced decision recommendations with structured output

        This enhanced version is optimized for generating structured parameter
        adjustments with detailed risk assessments and priority levels.

        Args:
            prompt: Rendered enhanced decision prompt
            temperature: Temperature parameter for generation (lower for more structured output)
            max_tokens: Maximum tokens to generate (higher for enhanced format)

        Returns:
            Parsed enhanced decision dictionary

        Requirements: Enhanced decision analysis with structured parameter adjustments
        """
        _llm_log(
            "DECISION_LLM_ENHANCED_START",
            "开始生成增强版结构化决策",
            model_name=self.model,
            status="running",
        )

        try:
            # Build request payload with enhanced parameters
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是一个专业的蘑菇种植环境控制专家。"
                            "必须仅输出一个完整、合法、可解析的JSON对象。"
                            "禁止输出<think>、解释文字、Markdown代码块或对象外任何内容。"
                            "字段值保持简洁，避免冗长描述。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "response_format": self._get_enhanced_json_schema_response_format(),
            }
            payload.update(
                self._get_generation_options(
                    temperature=temperature, max_tokens=max_tokens
                )
            )

            extra_body = self._get_model_extra_body()
            if extra_body:
                payload["extra_body"] = extra_body

            _llm_log(
                "DECISION_LLM_ENHANCED_PREPARED",
                "已构建增强版结构化决策请求",
                level="DEBUG",
                endpoint=self.api_url,
                model_name=self.model,
                temperature=payload.get("temperature"),
                max_tokens=payload.get("max_tokens"),
            )

            # Prepare headers with API key if available
            headers = {"Content-Type": "application/json"}

            # Add API key if configured
            # First check for VL-specific API key, then fall back to general API key
            if hasattr(self.settings, "llama_vl") and hasattr(
                self.settings.llama_vl, "api_key_vl"
            ):
                headers["X-API-Key"] = self.settings.llama_vl.api_key_vl
            elif hasattr(self.settings, "llama") and hasattr(
                self.settings.llama, "api_key_vl"
            ):
                headers["X-API-Key"] = self.settings.llama.api_key_vl
            elif hasattr(self.settings, "llama") and hasattr(
                self.settings.llama, "api_key"
            ):
                headers["X-API-Key"] = self.settings.llama.api_key
            elif hasattr(self.settings, "llama_vl") and hasattr(
                self.settings.llama_vl, "api_key"
            ):
                headers["X-API-Key"] = self.settings.llama_vl.api_key

            # Send POST request with timeout and headers
            response = requests.post(
                self.api_url, json=payload, headers=headers, timeout=self.timeout
            )

            if (
                response.status_code == 400
                and "response_format" in response.text.lower()
            ):
                _llm_log(
                    "DECISION_LLM_ENHANCED_SCHEMA_REJECTED",
                    "后端拒绝 response_format，改为无 schema 约束重试",
                    level="WARNING",
                    status="retrying",
                )
                payload.pop("response_format", None)
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )

            # Retry once for llama.cpp-style context overflow
            if response.status_code == 400 and self._is_context_overflow_error(
                response.text
            ):
                _llm_log(
                    "DECISION_LLM_ENHANCED_CONTEXT_OVERFLOW",
                    "增强版请求触发上下文溢出，改用缩短提示词重试",
                    level="WARNING",
                    status="retrying",
                )
                shortened_prompt = self._shorten_prompt_for_context(prompt)
                payload["messages"][1]["content"] = shortened_prompt
                if payload.get("max_tokens") is not None:
                    payload["max_tokens"] = min(payload["max_tokens"], 1024)
                response = requests.post(
                    self.api_url, json=payload, headers=headers, timeout=self.timeout
                )

            # Check response status
            if response.status_code != 200:
                error_msg = (
                    f"Enhanced LLM API returned status {response.status_code}: "
                    f"{response.text}"
                )
                _llm_log(
                    "DECISION_LLM_ENHANCED_FAILED",
                    "增强版 LLM 接口返回非 200 状态",
                    level="ERROR",
                    status="failed",
                    error_code=f"http_{response.status_code}",
                    error_message=error_msg,
                )
                return self._get_enhanced_fallback_decision(
                    f"API error: {response.status_code}"
                )

            # Parse response JSON
            response_data = response.json()

            # Extract content from response
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                _llm_log(
                    "DECISION_LLM_ENHANCED_NO_CHOICES",
                    "增强版 LLM 响应中缺少 choices",
                    level="ERROR",
                    status="failed",
                )
                return self._get_enhanced_fallback_decision("No choices in response")

            content = response_data["choices"][0].get("message", {}).get("content", "")
            finish_reason = response_data["choices"][0].get("finish_reason")

            # Detailed logging for debugging
            _llm_log(
                "DECISION_LLM_ENHANCED_RESPONSE",
                "已收到增强版 LLM 响应",
                total_items=len(content),
                status="success",
            )

            if not content:
                _llm_log(
                    "DECISION_LLM_ENHANCED_EMPTY",
                    "增强版 LLM 响应内容为空",
                    level="ERROR",
                    status="failed",
                )
                return self._get_enhanced_fallback_decision("Empty content")

            if len(content) < 100:
                _llm_log(
                    "DECISION_LLM_ENHANCED_SHORT",
                    "增强版 LLM 响应较短，可能不完整",
                    level="WARNING",
                    status="partial",
                    total_items=len(content),
                    response_preview=content,
                )
            else:
                _llm_log(
                    "DECISION_LLM_ENHANCED_PREVIEW",
                    "增强版 LLM 响应预览",
                    level="DEBUG",
                    total_items=len(content),
                    response_preview=content[:200],
                )
            if finish_reason:
                _llm_log(
                    "DECISION_LLM_ENHANCED_FINISH_REASON",
                    "增强版 LLM 返回 finish_reason",
                    level="DEBUG",
                    finish_reason=finish_reason,
                )

            # Parse the enhanced response content
            parsed_decision = self._parse_enhanced_response(content)

            truncated_or_incomplete = str(
                finish_reason
            ).lower() == "length" or self._looks_like_truncated_json(content)
            should_retry_strict = self._is_parse_fallback(parsed_decision) or (
                truncated_or_incomplete
                and self._was_structure_converted(parsed_decision)
                and not self._was_tail_salvaged(parsed_decision)
            )

            if truncated_or_incomplete and self._was_tail_salvaged(parsed_decision):
                _llm_log(
                    "DECISION_LLM_ENHANCED_TAIL_SALVAGED",
                    "增强版首轮响应已通过尾部修复恢复，跳过严格重试",
                    status="success",
                )

            if should_retry_strict:
                _llm_log(
                    "DECISION_LLM_ENHANCED_STRICT_RETRY",
                    "增强版响应触发解析回退，使用严格 JSON 约束重试一次",
                    level="WARNING",
                    status="retrying",
                )
                retry_prompt = self._shorten_prompt_for_context(prompt)
                strict_retry_max_tokens = min(
                    max(
                        max_tokens or self.settings.llama.get("max_tokens", 3072),
                        2048,
                    ),
                    2560,
                )
                retry_payload = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一个专业的蘑菇种植环境控制专家。"
                                "必须仅输出完整合法JSON，不允许任何解释文本。"
                                "JSON必须包含 strategy、device_recommendations、monitoring_points 三个顶层键。"
                                "禁止输出<think>、Markdown代码块或对象外文本。"
                                "保持字段值简短，每个 rationale 最多2条。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": retry_prompt,
                        },
                    ],
                    "stream": False,
                    "response_format": self._get_enhanced_json_schema_response_format(),
                }
                retry_payload.update(
                    self._get_generation_options(
                        temperature=0.05, max_tokens=strict_retry_max_tokens
                    )
                )
                if extra_body:
                    retry_payload["extra_body"] = extra_body
                retry_response = requests.post(
                    self.api_url,
                    json=retry_payload,
                    headers=headers,
                    timeout=self.timeout,
                )

                if (
                    retry_response.status_code == 400
                    and "response_format" in retry_response.text.lower()
                ):
                    retry_payload.pop("response_format", None)
                    retry_response = requests.post(
                        self.api_url,
                        json=retry_payload,
                        headers=headers,
                        timeout=self.timeout,
                    )

                if (
                    retry_response.status_code == 400
                    and self._is_context_overflow_error(retry_response.text)
                ):
                    retry_payload["messages"][1]["content"] = (
                        self._shorten_prompt_for_context(prompt)
                    )
                    if retry_payload.get("max_tokens") is not None:
                        retry_payload["max_tokens"] = min(
                            retry_payload["max_tokens"], 1024
                        )
                    retry_response = requests.post(
                        self.api_url,
                        json=retry_payload,
                        headers=headers,
                        timeout=self.timeout,
                    )

                if retry_response.status_code == 200:
                    retry_data = retry_response.json()
                    retry_finish_reason = retry_data.get("choices", [{}])[0].get(
                        "finish_reason"
                    )
                    retry_content = (
                        retry_data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    if retry_finish_reason:
                        _llm_log(
                            "DECISION_LLM_ENHANCED_RETRY_FINISH_REASON",
                            "增强版严格重试返回 finish_reason",
                            level="DEBUG",
                            finish_reason=retry_finish_reason,
                        )
                    retry_parsed_decision = self._parse_enhanced_response(retry_content)
                    if not self._is_parse_fallback(retry_parsed_decision):
                        parsed_decision = retry_parsed_decision
                        _llm_log(
                            "DECISION_LLM_ENHANCED_RETRY_OK",
                            "增强版严格 JSON 重试成功",
                            status="success",
                        )

            return parsed_decision

        except requests.exceptions.Timeout:
            _llm_log(
                "DECISION_LLM_ENHANCED_TIMEOUT",
                "增强版 LLM 请求超时",
                level="ERROR",
                status="failed",
                error_message=f"timeout after {self.timeout} seconds",
            )
            return self._get_enhanced_fallback_decision("Timeout")

        except requests.exceptions.ConnectionError as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_CONNECTION_ERROR",
                "增强版 LLM 连接失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_enhanced_fallback_decision("Connection error")

        except requests.exceptions.RequestException as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_REQUEST_EXCEPTION",
                "增强版 LLM 请求异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_enhanced_fallback_decision(f"Request error: {str(e)}")

        except Exception as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_UNEXPECTED_ERROR",
                "增强版 LLM 调用发生未预期异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_enhanced_fallback_decision(f"Unexpected error: {str(e)}")

    def _is_context_overflow_error(self, error_text: str) -> bool:
        """Check whether API error text indicates context/window overflow."""
        text = (error_text or "").lower()
        return (
            "n_keep" in text
            and "n_ctx" in text
            or "context length" in text
            or "context size has been exceeded" in text
            or "maximum context" in text
            or "prompt too long" in text
            or "token limit" in text
        )

    def _is_parse_fallback(self, decision: Dict) -> bool:
        """Return True when fallback is caused by parse-related failures."""
        if not isinstance(decision, dict):
            return False
        if decision.get("status") != "fallback":
            return False
        reason = str(decision.get("error_reason", "")).lower()
        return "parse" in reason or "json" in reason

    def _shorten_prompt_for_context(self, prompt: str) -> str:
        """Shorten prompt conservatively to reduce context overflow risk."""
        max_chars = int(self.settings.llama.get("enhanced_prompt_max_chars", 3000))
        if len(prompt) <= max_chars:
            return prompt

        head_ratio = 0.7
        head_len = int(max_chars * head_ratio)
        tail_len = max_chars - head_len
        head = prompt[:head_len]
        tail = prompt[-tail_len:] if tail_len > 0 else ""

        return (
            f"{head}\n\n"
            "[提示词已因上下文窗口限制自动压缩，保留开头与结尾关键信息]\n\n"
            f"{tail}"
        )

    def _get_enhanced_json_schema_response_format(self) -> Dict:
        """Return minimal JSON schema constraint for enhanced decision output."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "enhanced_decision_output",
                "schema": {
                    "type": "object",
                    "required": [
                        "strategy",
                        "device_recommendations",
                        "monitoring_points",
                    ],
                    "properties": {
                        "strategy": {"type": "object"},
                        "device_recommendations": {"type": "object"},
                        "monitoring_points": {"type": "object"},
                    },
                    "additionalProperties": True,
                },
            },
        }

    def _parse_enhanced_response(self, response_text: str) -> Dict:
        """
        Parse enhanced LLM response text with structured parameter adjustments

        This enhanced parser handles the more complex structured output format
        with parameter adjustments, risk assessments, and priority levels.

        Args:
            response_text: Enhanced LLM response text

        Returns:
            Structured enhanced decision dictionary

        Requirements: Enhanced decision analysis parsing
        """
        _llm_log(
            "DECISION_LLM_ENHANCED_PARSE_START",
            "开始解析增强版 LLM 响应",
            status="running",
        )

        # Check for empty response
        if not response_text or not response_text.strip():
            _llm_log(
                "DECISION_LLM_ENHANCED_PARSE_EMPTY",
                "增强版 LLM 响应为空白文本",
                level="ERROR",
                status="failed",
            )
            return self._get_enhanced_fallback_decision("Empty response")

        # Strip whitespace and common reasoning wrappers
        response_text = self._sanitize_llm_response_text(response_text)

        # Log response characteristics
        _llm_log(
            "DECISION_LLM_ENHANCED_PARSE_PROFILE",
            "增强版 LLM 响应解析画像",
            level="DEBUG",
            total_items=len(response_text),
            response_preview=response_text[:50],
        )

        try:
            # Try to parse as JSON directly
            decision = json.loads(response_text)
            _llm_log(
                "DECISION_LLM_ENHANCED_PARSE_DIRECT_OK",
                "直接 JSON 解析增强版响应成功",
                status="success",
            )
            if self._validate_enhanced_structure(decision):
                return decision
            _llm_log(
                "DECISION_LLM_ENHANCED_STRUCTURE_CONVERT",
                "增强版响应结构校验失败，尝试转换格式",
                level="WARNING",
                status="retrying",
            )
            return self._convert_to_enhanced_format(decision)

        except json.JSONDecodeError as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_PARSE_RETRY",
                "增强版 JSON 首次解析失败，尝试从文本中提取 JSON",
                level="WARNING",
                status="retrying",
                error_message=str(e),
                error_line=e.lineno,
                error_column=e.colno,
            )

            # Try to fix common JSON issues before parsing
            fixed_response = self._fix_common_json_issues(response_text)
            if fixed_response != response_text:
                decision = self._parse_enhanced_candidate(fixed_response)
                if decision is not None:
                    _llm_log(
                        "DECISION_LLM_ENHANCED_PARSE_FIXED_OK",
                        "修复常见 JSON 问题后解析增强版响应成功",
                        status="success",
                    )
                    return decision
                else:
                    _llm_log(
                        "DECISION_LLM_ENHANCED_PARSE_FIXED_STILL_BAD",
                        "修复常见 JSON 问题后仍无法解析，继续尝试其他方法",
                        level="DEBUG",
                    )

            repaired_response = self._repair_truncated_json_tail(response_text)
            if repaired_response:
                decision = self._parse_enhanced_candidate(repaired_response)
                if decision is not None:
                    _llm_log(
                        "DECISION_LLM_ENHANCED_TAIL_REPAIRED",
                        "通过尾部修复与自动闭合容器成功抢救增强版 JSON",
                        level="WARNING",
                        status="partial",
                    )
                    metadata = decision.setdefault("metadata", {})
                    if isinstance(metadata, dict):
                        warnings = metadata.setdefault("warnings", [])
                        if isinstance(warnings, list):
                            warnings.append("LLM首轮输出尾部截断，已自动抢救解析")
                        metadata["tail_salvaged"] = True
                    return decision

            # Try to extract JSON from markdown code blocks (same as regular parsing)
            json_block_patterns = [
                r"```json\s*(\{.*?\})\s*```",
                r"```\s*(\{.*?\})\s*```",
            ]

            for pattern in json_block_patterns:
                matches = re.findall(pattern, response_text, re.DOTALL)
                if matches:
                    for match in matches:
                        decision = self._parse_enhanced_candidate(match)
                        if decision is not None:
                            _llm_log(
                                "DECISION_LLM_ENHANCED_PARSE_CODEBLOCK_OK",
                                "从 Markdown 代码块提取增强版 JSON 成功",
                                status="success",
                            )
                            return decision

            # Try bracket matching extraction
            json_objects = self._extract_json_objects(response_text)

            if json_objects:
                for obj_text in sorted(json_objects, key=len, reverse=True):
                    decision = self._parse_enhanced_candidate(obj_text)
                    if decision is not None:
                        _llm_log(
                            "DECISION_LLM_ENHANCED_PARSE_BRACKET_OK",
                            "通过括号匹配提取增强版 JSON 成功",
                            status="success",
                        )
                        return decision

            # If all parsing attempts fail
            _llm_log(
                "DECISION_LLM_ENHANCED_PARSE_FAILED",
                "多轮尝试后仍无法解析增强版 LLM 响应",
                level="ERROR",
                status="failed",
                total_items=len(response_text),
                response_preview=response_text[:500],
            )
            return self._get_enhanced_fallback_decision("Enhanced JSON parse error")

        except Exception as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_PARSE_EXCEPTION",
                "解析增强版 LLM 响应时发生未预期异常",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_enhanced_fallback_decision(
                f"Enhanced parse error: {str(e)}"
            )

    def _fix_common_json_issues(self, json_text: str) -> str:
        """
        Fix common JSON formatting issues that LLMs often make

        Args:
            json_text: Raw JSON text from LLM

        Returns:
            Fixed JSON text
        """
        # Remove any trailing commas before closing braces/brackets
        json_text = re.sub(r",(\s*[}\]])", r"\1", json_text)

        # Fix unescaped quotes in strings (basic attempt)
        # This is a simple fix - more complex cases might still fail
        json_text = re.sub(r'(?<!\\)"(?=.*".*:)', r'\\"', json_text)

        # Remove any text before the first { or after the last }
        start_idx = json_text.find("{")
        end_idx = json_text.rfind("}")

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_text = json_text[start_idx : end_idx + 1]

        # Try to fix incomplete JSON by adding missing closing braces
        open_braces = json_text.count("{")
        close_braces = json_text.count("}")

        if open_braces > close_braces:
            json_text += "}" * (open_braces - close_braces)

        return json_text

    def _validate_enhanced_structure(self, decision: Dict) -> bool:
        """
        Validate if the decision has the enhanced structure format

        Args:
            decision: Parsed decision dictionary

        Returns:
            True if structure is enhanced format, False otherwise
        """
        try:
            # Check if device recommendations have the enhanced parameter structure
            device_recs = decision.get("device_recommendations", {})

            for device_type in [
                "air_cooler",
                "fresh_air_fan",
                "humidifier",
                "grow_light",
            ]:
                device_params = device_recs.get(device_type, {})

                # Check if any parameter has the enhanced structure
                for param_name, param_value in device_params.items():
                    if param_name == "rationale" or param_name == "left_right_strategy":
                        continue

                    if isinstance(param_value, dict):
                        # Check for enhanced parameter structure
                        required_keys = ["current_value", "recommended_value", "action"]
                        if all(key in param_value for key in required_keys):
                            return True

            return False

        except Exception as e:
            _llm_log(
                "DECISION_LLM_ENHANCED_VALIDATE_ERROR",
                "校验增强版结构时发生异常",
                level="WARNING",
                status="partial",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return False

    def _convert_to_enhanced_format(self, decision: Dict) -> Dict:
        """
        Convert regular decision format to enhanced format

        Args:
            decision: Regular decision dictionary

        Returns:
            Enhanced decision dictionary
        """
        _llm_log(
            "DECISION_LLM_CONVERT_TO_ENHANCED_START",
            "开始将普通决策结果转换为增强版格式",
            status="running",
        )

        try:

            def _to_param_struct(
                current_value=None,
                recommended_value=None,
                reason: str = "转换自常规格式",
                priority: str = "medium",
            ) -> Dict:
                action = "maintain"
                if recommended_value is not None and recommended_value != current_value:
                    action = "adjust"
                return {
                    "current_value": current_value,
                    "recommended_value": recommended_value,
                    "action": action,
                    "change_reason": reason,
                    "priority": priority,
                    "urgency": "routine",
                    "risk_assessment": {
                        "adjustment_risk": "low",
                        "no_action_risk": "low",
                        "impact_scope": "参数调整",
                    },
                }

            enhanced_decision = {
                "status": decision.get("status", "success"),
                "strategy": decision.get("strategy", {}),
                "device_recommendations": decision.get("device_recommendations", {}),
                "monitoring_points": decision.get("monitoring_points", {}),
                "metadata": decision.get("metadata", {}),
            }

            metadata = enhanced_decision.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                enhanced_decision["metadata"] = metadata
            metadata["converted_from_regular_format"] = True

            # 兼容部分模型返回的扁平格式: adjustment_recommendations
            if not enhanced_decision.get("device_recommendations") and isinstance(
                decision.get("adjustment_recommendations"), list
            ):
                devices_map: Dict[str, Dict] = {}
                for item in decision.get("adjustment_recommendations", []):
                    if not isinstance(item, dict):
                        continue

                    device = str(item.get("device", "unknown")).strip() or "unknown"
                    parameter = (
                        str(item.get("parameter", "unknown")).strip() or "unknown"
                    )
                    current_value = item.get("current_value")
                    recommended_value = item.get("recommended_value")
                    reason = str(
                        item.get("reason", "转换自 adjustment_recommendations")
                    )

                    if device not in devices_map:
                        devices_map[device] = {}
                    devices_map[device][parameter] = _to_param_struct(
                        current_value=current_value,
                        recommended_value=recommended_value,
                        reason=reason,
                        priority="high" if item.get("priority") == "high" else "medium",
                    )

                enhanced_decision["device_recommendations"] = devices_map

            # 兜底 strategy
            if not isinstance(
                enhanced_decision.get("strategy"), dict
            ) or not enhanced_decision.get("strategy"):
                summary_text = str(
                    decision.get("summary")
                    or decision.get("overall_strategy")
                    or "基于当前数据执行保守参数调整"
                )
                enhanced_decision["strategy"] = {
                    "core_objective": summary_text,
                    "priority_ranking": ["温度控制", "湿度控制", "CO2控制"],
                    "key_risk_points": ["模型输出非标准结构，已自动转换"],
                }

            # 兜底 monitoring_points
            if not isinstance(
                enhanced_decision.get("monitoring_points"), dict
            ) or not enhanced_decision.get("monitoring_points"):
                enhanced_decision["monitoring_points"] = {
                    "key_time_periods": ["未来2小时", "未来6小时"],
                    "warning_thresholds": {},
                    "emergency_measures": ["若环境参数偏离阈值，立即人工复核"],
                }

            # 归一化 device_recommendations 参数结构
            device_recs = enhanced_decision.get("device_recommendations")
            if not isinstance(device_recs, dict):
                device_recs = {}

            normalized_device_recs: Dict[str, Dict] = {}
            for device_type in [
                "air_cooler",
                "fresh_air_fan",
                "humidifier",
                "grow_light",
            ]:
                device_params = device_recs.get(device_type, {})
                if not isinstance(device_params, dict):
                    device_params = {}

                enhanced_params = {}
                for param_name, param_value in device_params.items():
                    if param_name in ["rationale", "left_right_strategy"]:
                        enhanced_params[param_name] = param_value
                        continue

                    if isinstance(param_value, dict) and all(
                        k in param_value
                        for k in ["current_value", "recommended_value", "action"]
                    ):
                        enhanced_params[param_name] = param_value
                    elif isinstance(param_value, dict):
                        enhanced_params[param_name] = _to_param_struct(
                            current_value=param_value.get("current_value"),
                            recommended_value=param_value.get(
                                "recommended_value", param_value.get("value")
                            ),
                            reason=str(
                                param_value.get("change_reason", "转换自部分结构化格式")
                            ),
                        )
                    else:
                        enhanced_params[param_name] = _to_param_struct(
                            current_value=param_value,
                            recommended_value=param_value,
                            reason="转换自常规格式",
                            priority="low" if param_value is None else "medium",
                        )

                normalized_device_recs[device_type] = enhanced_params

            # 保留非标准设备键，避免信息丢失
            for device_type, params in device_recs.items():
                if device_type in normalized_device_recs:
                    continue
                if not isinstance(params, dict):
                    continue
                converted = {}
                for param_name, param_value in params.items():
                    if param_name in ["rationale", "left_right_strategy"]:
                        converted[param_name] = param_value
                    elif isinstance(param_value, dict) and all(
                        k in param_value
                        for k in ["current_value", "recommended_value", "action"]
                    ):
                        converted[param_name] = param_value
                    else:
                        converted[param_name] = _to_param_struct(
                            current_value=None,
                            recommended_value=param_value
                            if not isinstance(param_value, dict)
                            else param_value.get("recommended_value"),
                            reason="转换自非标准设备结构",
                        )
                normalized_device_recs[device_type] = converted

            enhanced_decision["device_recommendations"] = normalized_device_recs

            _llm_log(
                "DECISION_LLM_CONVERT_TO_ENHANCED_OK",
                "普通决策结果已成功转换为增强版格式",
                status="success",
            )
            return enhanced_decision

        except Exception as e:
            _llm_log(
                "DECISION_LLM_CONVERT_TO_ENHANCED_FAILED",
                "普通决策结果转换为增强版格式失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self._get_enhanced_fallback_decision("Conversion error")

    def _get_enhanced_fallback_decision(self, error_reason: str) -> Dict:
        """
        Generate enhanced fallback decision when LLM call fails

        Returns a conservative rule-based decision with enhanced structure format.

        Args:
            error_reason: Reason for fallback

        Returns:
            Dict: Enhanced fallback decision with structured parameter adjustments

        Requirements: Enhanced fallback decision
        """
        _llm_log(
            "DECISION_LLM_ENHANCED_FALLBACK",
            "启用增强版回退决策策略",
            level="WARNING",
            status="fallback",
            error_message=error_reason,
        )

        # Create default parameter adjustment structure
        def create_maintain_param(value=0, reason="LLM服务不可用，保持当前设定"):
            return {
                "current_value": value,
                "recommended_value": value,
                "action": "maintain",
                "change_reason": reason,
                "priority": "low",
                "urgency": "routine",
                "risk_assessment": {
                    "adjustment_risk": "low",
                    "no_action_risk": "medium",
                    "impact_scope": "系统稳定性",
                },
            }

        # Return enhanced fallback decision
        fallback = {
            "status": "fallback",
            "error_reason": error_reason,
            "strategy": {
                "core_objective": "维持当前环境稳定（LLM服务不可用，使用保守策略）",
                "priority_ranking": ["温度控制", "湿度控制", "CO2控制"],
                "key_risk_points": [
                    f"LLM服务不可用: {error_reason}",
                    "使用基于规则的默认策略",
                    "建议人工审核当前参数",
                ],
            },
            "device_recommendations": {
                "air_cooler": {
                    "tem_set": create_maintain_param(18.0, "LLM不可用，保持温度设定"),
                    "tem_diff_set": create_maintain_param(
                        2.0, "LLM不可用，保持温差设定"
                    ),
                    "cyc_on_off": create_maintain_param(0, "LLM不可用，保持循环开关"),
                    "cyc_on_time": create_maintain_param(10, "LLM不可用，保持循环时间"),
                    "cyc_off_time": create_maintain_param(
                        10, "LLM不可用，保持循环时间"
                    ),
                    "ar_on_off": create_maintain_param(0, "LLM不可用，保持新风联动"),
                    "hum_on_off": create_maintain_param(0, "LLM不可用，保持加湿联动"),
                    "rationale": [
                        "LLM服务不可用，保持当前冷风机设定",
                        "建议人工检查温度是否在合理范围内",
                    ],
                },
                "fresh_air_fan": {
                    "model": create_maintain_param(1, "LLM不可用，保持CO2控制模式"),
                    "control": create_maintain_param(1, "LLM不可用，保持开启状态"),
                    "co2_on": create_maintain_param(1000, "LLM不可用，保持CO2开启阈值"),
                    "co2_off": create_maintain_param(800, "LLM不可用，保持CO2关闭阈值"),
                    "on": create_maintain_param(10, "LLM不可用，保持开启时间"),
                    "off": create_maintain_param(10, "LLM不可用，保持关闭时间"),
                    "rationale": [
                        "LLM服务不可用，保持当前新风机设定",
                        "建议人工检查CO2浓度是否在合理范围内",
                    ],
                },
                "humidifier": {
                    "model": create_maintain_param(1, "LLM不可用，保持湿度控制模式"),
                    "on": create_maintain_param(85, "LLM不可用，保持湿度开启阈值"),
                    "off": create_maintain_param(90, "LLM不可用，保持湿度关闭阈值"),
                    "left_right_strategy": "保持当前设定",
                    "rationale": [
                        "LLM服务不可用，保持当前加湿器设定",
                        "建议人工检查湿度是否在合理范围内",
                    ],
                },
                "grow_light": {
                    "model": create_maintain_param(1, "LLM不可用，保持自动模式"),
                    "on_mset": create_maintain_param(60, "LLM不可用，保持开启时长"),
                    "off_mset": create_maintain_param(60, "LLM不可用，保持关闭时长"),
                    "on_off_1": create_maintain_param(1, "LLM不可用，保持第1路开启"),
                    "choose_1": create_maintain_param(1, "LLM不可用，保持白光"),
                    "on_off_2": create_maintain_param(1, "LLM不可用，保持第2路开启"),
                    "choose_2": create_maintain_param(1, "LLM不可用，保持白光"),
                    "on_off_3": create_maintain_param(1, "LLM不可用，保持第3路开启"),
                    "choose_3": create_maintain_param(1, "LLM不可用，保持白光"),
                    "on_off_4": create_maintain_param(1, "LLM不可用，保持第4路开启"),
                    "choose_4": create_maintain_param(1, "LLM不可用，保持白光"),
                    "rationale": [
                        "LLM服务不可用，保持当前补光灯设定",
                        "建议人工检查光照是否符合生长阶段需求",
                    ],
                },
            },
            "monitoring_points": {
                "key_time_periods": ["全天候监控（LLM服务不可用期间）"],
                "warning_thresholds": {
                    "temperature": "根据当前生长阶段设定",
                    "humidity": "根据当前生长阶段设定",
                    "co2": "根据当前生长阶段设定",
                },
                "emergency_measures": [
                    "如环境参数异常，立即人工介入",
                    "尽快恢复LLM服务或使用人工决策",
                ],
            },
            "metadata": {
                "warnings": [
                    f"LLM调用失败: {error_reason}",
                    "使用增强型降级策略，所有设备参数保持当前值",
                    "强烈建议人工审核和介入",
                ],
                "llm_available": False,
                "enhanced_format": True,
            },
        }

        return fallback
