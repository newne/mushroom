"""
LLM Client Module

This module handles communication with the LLaMA API for generating
decision recommendations based on rendered prompts.
"""

import json
from typing import Dict, Optional

import requests
from dynaconf import Dynaconf
from loguru import logger


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
        
        logger.info(
            f"[LLMClient] Initialized with model: {self.model}, "
            f"endpoint: {self.api_url}"
        )
    
    def generate_decision(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = -1
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
        logger.info("[LLMClient] Generating decision with LLM")
        
        try:
            # Build request payload
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": prompt
                    }
                ],
                "temperature": temperature,
                "stream": False
            }
            
            # Add max_tokens if specified
            if max_tokens > 0:
                payload["max_tokens"] = max_tokens
            
            logger.debug(
                f"[LLMClient] Sending request to {self.api_url} "
                f"with model={self.model}, temperature={temperature}"
            )
            
            # Send POST request with timeout
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=self.timeout
            )
            
            # Check response status
            if response.status_code != 200:
                error_msg = (
                    f"LLM API returned status {response.status_code}: "
                    f"{response.text}"
                )
                logger.error(f"[LLMClient] {error_msg}")
                return self._get_fallback_decision(
                    f"API error: {response.status_code}"
                )
            
            # Parse response JSON
            response_data = response.json()
            
            # Extract content from response
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                logger.error("[LLMClient] No choices in LLM response")
                return self._get_fallback_decision("No choices in response")
            
            content = response_data["choices"][0].get("message", {}).get("content", "")
            
            # Detailed logging for debugging
            logger.info(f"[LLMClient] Response content length: {len(content)} chars")
            
            if not content:
                logger.error("[LLMClient] Empty content in LLM response")
                logger.error(f"[LLMClient] Full response structure: {list(response_data.keys())}")
                if "choices" in response_data and len(response_data["choices"]) > 0:
                    logger.error(f"[LLMClient] Choice structure: {list(response_data['choices'][0].keys())}")
                return self._get_fallback_decision("Empty content")
            
            if len(content) < 50:
                logger.warning(f"[LLMClient] Very short response (may be incomplete): {content}")
            else:
                logger.info(f"[LLMClient] Response preview: {content[:150]}...")
            
            logger.info(
                f"[LLMClient] Received response from LLM "
                f"(length: {len(content)} chars)"
            )
            
            # Parse the response content
            parsed_decision = self._parse_response(content)
            
            return parsed_decision
            
        except requests.exceptions.Timeout:
            logger.error(
                f"[LLMClient] Request timeout after {self.timeout} seconds"
            )
            return self._get_fallback_decision("Timeout")
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[LLMClient] Connection error: {e}")
            return self._get_fallback_decision("Connection error")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[LLMClient] Request error: {e}")
            return self._get_fallback_decision(f"Request error: {str(e)}")
            
        except Exception as e:
            logger.error(f"[LLMClient] Unexpected error: {e}", exc_info=True)
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
        logger.info("[LLMClient] Parsing LLM response")
        
        # Check for empty response
        if not response_text or not response_text.strip():
            logger.error("[LLMClient] Empty or whitespace-only response")
            return self._get_fallback_decision("Empty response")
        
        # Strip whitespace
        response_text = response_text.strip()
        
        # Log response characteristics
        logger.debug(
            f"[LLMClient] Response length: {len(response_text)} chars, "
            f"starts with: {response_text[:50]}"
        )
        
        try:
            # Try to parse as JSON directly
            decision = json.loads(response_text)
            logger.info("[LLMClient] Successfully parsed JSON response (direct)")
            return decision
            
        except json.JSONDecodeError as e:
            logger.warning(
                f"[LLMClient] Initial JSON parse failed: {e}. "
                f"Error at line {e.lineno}, column {e.colno}. "
                "Attempting to extract JSON from text..."
            )
            
            # Try to extract JSON from markdown code blocks
            import re
            
            # Look for JSON in code blocks (```json ... ``` or ``` ... ```)
            json_block_patterns = [
                r'```json\s*(\{.*?\})\s*```',  # ```json {...} ```
                r'```\s*(\{.*?\})\s*```',       # ``` {...} ```
            ]
            
            for pattern in json_block_patterns:
                matches = re.findall(pattern, response_text, re.DOTALL)
                if matches:
                    for match in matches:
                        try:
                            decision = json.loads(match)
                            logger.info(
                                "[LLMClient] Successfully extracted JSON from markdown code block"
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
                        logger.info(
                            "[LLMClient] Successfully extracted JSON using bracket matching"
                        )
                        return decision
                    except json.JSONDecodeError:
                        continue
            
            # If all parsing attempts fail, log the response and return fallback
            logger.error(
                f"[LLMClient] Failed to parse response after all attempts. "
                f"Response length: {len(response_text)}, "
                f"preview: {response_text[:500]}..."
            )
            return self._get_fallback_decision("JSON parse error")
            
        except Exception as e:
            logger.error(
                f"[LLMClient] Unexpected error during parsing: {e}",
                exc_info=True
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
        
        for i, char in enumerate(text):
            if char == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    obj_text = text[start:i+1]
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
        logger.warning(
            f"[LLMClient] Using fallback decision strategy. Reason: {error_reason}"
        )
        
        # Return a conservative fallback decision
        fallback = {
            "status": "fallback",
            "error_reason": error_reason,
            "strategy": {
                "core_objective": "维持当前环境稳定（LLM服务不可用，使用保守策略）",
                "priority_ranking": [
                    "温度控制",
                    "湿度控制",
                    "CO2控制"
                ],
                "key_risk_points": [
                    f"LLM服务不可用: {error_reason}",
                    "使用基于规则的默认策略",
                    "建议人工审核当前参数"
                ]
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
                        "建议人工检查温度是否在合理范围内"
                    ]
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
                        "建议人工检查CO2浓度是否在合理范围内"
                    ]
                },
                "humidifier": {
                    "model": None,
                    "on": None,
                    "off": None,
                    "left_right_strategy": "保持当前设定",
                    "rationale": [
                        "LLM服务不可用，保持当前加湿器设定",
                        "建议人工检查湿度是否在合理范围内"
                    ]
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
                        "建议人工检查光照是否符合生长阶段需求"
                    ]
                }
            },
            "monitoring_points": {
                "key_time_periods": [
                    "全天候监控（LLM服务不可用期间）"
                ],
                "warning_thresholds": {
                    "temperature": "根据当前生长阶段设定",
                    "humidity": "根据当前生长阶段设定",
                    "co2": "根据当前生长阶段设定"
                },
                "emergency_measures": [
                    "如环境参数异常，立即人工介入",
                    "尽快恢复LLM服务或使用人工决策"
                ]
            },
            "metadata": {
                "warnings": [
                    f"LLM调用失败: {error_reason}",
                    "使用降级策略，所有设备参数保持当前值",
                    "强烈建议人工审核和介入"
                ],
                "llm_available": False
            }
        }
        
        return fallback
