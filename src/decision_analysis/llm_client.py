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
            
            # Prepare headers with API key if available
            headers = {
                "Content-Type": "application/json"
            }
            
            # Add API key if configured
            # First check for VL-specific API key, then fall back to general API key
            if hasattr(self.settings, 'llama_vl') and hasattr(self.settings.llama_vl, 'api_key_vl'):
                headers["X-API-Key"] = self.settings.llama_vl.api_key_vl
            elif hasattr(self.settings, 'llama') and hasattr(self.settings.llama, 'api_key_vl'):
                headers["X-API-Key"] = self.settings.llama.api_key_vl
            elif hasattr(self.settings, 'llama') and hasattr(self.settings.llama, 'api_key'):
                headers["X-API-Key"] = self.settings.llama.api_key
            elif hasattr(self.settings, 'llama_vl') and hasattr(self.settings.llama_vl, 'api_key'):
                headers["X-API-Key"] = self.settings.llama_vl.api_key
            
            # Send POST request with timeout and headers
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
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
    
    def generate_enhanced_decision(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 3072
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
        logger.info("[LLMClient] Generating enhanced decision with structured output")
        
        try:
            # Build request payload with enhanced parameters
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个专业的蘑菇种植环境控制专家。请严格按照要求的JSON格式输出结构化的参数调整建议。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": temperature,
                "stream": False,
                "max_tokens": max_tokens
            }
            
            logger.debug(
                f"[LLMClient] Sending enhanced request to {self.api_url} "
                f"with model={self.model}, temperature={temperature}, max_tokens={max_tokens}"
            )
            
            # Prepare headers with API key if available
            headers = {
                "Content-Type": "application/json"
            }
            
            # Add API key if configured
            # First check for VL-specific API key, then fall back to general API key
            if hasattr(self.settings, 'llama_vl') and hasattr(self.settings.llama_vl, 'api_key_vl'):
                headers["X-API-Key"] = self.settings.llama_vl.api_key_vl
            elif hasattr(self.settings, 'llama') and hasattr(self.settings.llama, 'api_key_vl'):
                headers["X-API-Key"] = self.settings.llama.api_key_vl
            elif hasattr(self.settings, 'llama') and hasattr(self.settings.llama, 'api_key'):
                headers["X-API-Key"] = self.settings.llama.api_key
            elif hasattr(self.settings, 'llama_vl') and hasattr(self.settings.llama_vl, 'api_key'):
                headers["X-API-Key"] = self.settings.llama_vl.api_key
            
            # Send POST request with timeout and headers
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            # Check response status
            if response.status_code != 200:
                error_msg = (
                    f"Enhanced LLM API returned status {response.status_code}: "
                    f"{response.text}"
                )
                logger.error(f"[LLMClient] {error_msg}")
                return self._get_enhanced_fallback_decision(
                    f"API error: {response.status_code}"
                )
            
            # Parse response JSON
            response_data = response.json()
            
            # Extract content from response
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                logger.error("[LLMClient] No choices in enhanced LLM response")
                return self._get_enhanced_fallback_decision("No choices in response")
            
            content = response_data["choices"][0].get("message", {}).get("content", "")
            
            # Detailed logging for debugging
            logger.info(f"[LLMClient] Enhanced response content length: {len(content)} chars")
            
            if not content:
                logger.error("[LLMClient] Empty content in enhanced LLM response")
                return self._get_enhanced_fallback_decision("Empty content")
            
            if len(content) < 100:
                logger.warning(f"[LLMClient] Very short enhanced response (may be incomplete): {content}")
            else:
                logger.info(f"[LLMClient] Enhanced response preview: {content[:200]}...")
            
            logger.info(
                f"[LLMClient] Received enhanced response from LLM "
                f"(length: {len(content)} chars)"
            )
            
            # Parse the enhanced response content
            parsed_decision = self._parse_enhanced_response(content)
            
            return parsed_decision
            
        except requests.exceptions.Timeout:
            logger.error(
                f"[LLMClient] Enhanced request timeout after {self.timeout} seconds"
            )
            return self._get_enhanced_fallback_decision("Timeout")
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[LLMClient] Enhanced connection error: {e}")
            return self._get_enhanced_fallback_decision("Connection error")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[LLMClient] Enhanced request error: {e}")
            return self._get_enhanced_fallback_decision(f"Request error: {str(e)}")
            
        except Exception as e:
            logger.error(f"[LLMClient] Unexpected error in enhanced generation: {e}", exc_info=True)
            return self._get_enhanced_fallback_decision(f"Unexpected error: {str(e)}")
    
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
        logger.info("[LLMClient] Parsing enhanced LLM response")
        
        # Check for empty response
        if not response_text or not response_text.strip():
            logger.error("[LLMClient] Empty or whitespace-only enhanced response")
            return self._get_enhanced_fallback_decision("Empty response")
        
        # Strip whitespace
        response_text = response_text.strip()
        
        # Log response characteristics
        logger.debug(
            f"[LLMClient] Enhanced response length: {len(response_text)} chars, "
            f"starts with: {response_text[:50]}"
        )
        
        try:
            # Try to parse as JSON directly
            decision = json.loads(response_text)
            logger.info("[LLMClient] Successfully parsed enhanced JSON response (direct)")
            
            # Validate enhanced structure
            if self._validate_enhanced_structure(decision):
                return decision
            else:
                logger.warning("[LLMClient] Enhanced response structure validation failed, attempting conversion")
                return self._convert_to_enhanced_format(decision)
            
        except json.JSONDecodeError as e:
            logger.warning(
                f"[LLMClient] Enhanced JSON parse failed: {e}. "
                f"Error at line {e.lineno}, column {e.colno}. "
                "Attempting to extract JSON from text..."
            )
            
            # Try to fix common JSON issues before parsing
            fixed_response = self._fix_common_json_issues(response_text)
            if fixed_response != response_text:
                try:
                    decision = json.loads(fixed_response)
                    logger.info("[LLMClient] Successfully parsed enhanced JSON after fixing common issues")
                    
                    # Validate and convert if needed
                    if self._validate_enhanced_structure(decision):
                        return decision
                    else:
                        return self._convert_to_enhanced_format(decision)
                        
                except json.JSONDecodeError:
                    logger.debug("[LLMClient] Fixed JSON still has parsing errors, trying other methods")
            
            # Try to extract JSON from markdown code blocks (same as regular parsing)
            import re
            
            json_block_patterns = [
                r'```json\s*(\{.*?\})\s*```',
                r'```\s*(\{.*?\})\s*```',
            ]
            
            for pattern in json_block_patterns:
                matches = re.findall(pattern, response_text, re.DOTALL)
                if matches:
                    for match in matches:
                        try:
                            decision = json.loads(match)
                            logger.info(
                                "[LLMClient] Successfully extracted enhanced JSON from markdown code block"
                            )
                            
                            # Validate and convert if needed
                            if self._validate_enhanced_structure(decision):
                                return decision
                            else:
                                return self._convert_to_enhanced_format(decision)
                                
                        except json.JSONDecodeError:
                            continue
            
            # Try bracket matching extraction
            json_objects = self._extract_json_objects(response_text)
            
            if json_objects:
                for obj_text in sorted(json_objects, key=len, reverse=True):
                    try:
                        decision = json.loads(obj_text)
                        logger.info(
                            "[LLMClient] Successfully extracted enhanced JSON using bracket matching"
                        )
                        
                        # Validate and convert if needed
                        if self._validate_enhanced_structure(decision):
                            return decision
                        else:
                            return self._convert_to_enhanced_format(decision)
                            
                    except json.JSONDecodeError:
                        continue
            
            # If all parsing attempts fail
            logger.error(
                f"[LLMClient] Failed to parse enhanced response after all attempts. "
                f"Response length: {len(response_text)}, "
                f"preview: {response_text[:500]}..."
            )
            return self._get_enhanced_fallback_decision("Enhanced JSON parse error")
            
        except Exception as e:
            logger.error(
                f"[LLMClient] Unexpected error during enhanced parsing: {e}",
                exc_info=True
            )
            return self._get_enhanced_fallback_decision(f"Enhanced parse error: {str(e)}")
    
    def _fix_common_json_issues(self, json_text: str) -> str:
        """
        Fix common JSON formatting issues that LLMs often make
        
        Args:
            json_text: Raw JSON text from LLM
            
        Returns:
            Fixed JSON text
        """
        import re
        
        # Remove any trailing commas before closing braces/brackets
        json_text = re.sub(r',(\s*[}\]])', r'\1', json_text)
        
        # Fix unescaped quotes in strings (basic attempt)
        # This is a simple fix - more complex cases might still fail
        json_text = re.sub(r'(?<!\\)"(?=.*".*:)', r'\\"', json_text)
        
        # Remove any text before the first { or after the last }
        start_idx = json_text.find('{')
        end_idx = json_text.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_text = json_text[start_idx:end_idx + 1]
        
        # Try to fix incomplete JSON by adding missing closing braces
        open_braces = json_text.count('{')
        close_braces = json_text.count('}')
        
        if open_braces > close_braces:
            json_text += '}' * (open_braces - close_braces)
        
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
            device_recs = decision.get('device_recommendations', {})
            
            for device_type in ['air_cooler', 'fresh_air_fan', 'humidifier', 'grow_light']:
                device_params = device_recs.get(device_type, {})
                
                # Check if any parameter has the enhanced structure
                for param_name, param_value in device_params.items():
                    if param_name == 'rationale' or param_name == 'left_right_strategy':
                        continue
                    
                    if isinstance(param_value, dict):
                        # Check for enhanced parameter structure
                        required_keys = ['current_value', 'recommended_value', 'action']
                        if all(key in param_value for key in required_keys):
                            return True
            
            return False
            
        except Exception as e:
            logger.warning(f"[LLMClient] Error validating enhanced structure: {e}")
            return False
    
    def _convert_to_enhanced_format(self, decision: Dict) -> Dict:
        """
        Convert regular decision format to enhanced format
        
        Args:
            decision: Regular decision dictionary
            
        Returns:
            Enhanced decision dictionary
        """
        logger.info("[LLMClient] Converting regular decision to enhanced format")
        
        try:
            enhanced_decision = decision.copy()
            device_recs = enhanced_decision.get('device_recommendations', {})
            
            for device_type in ['air_cooler', 'fresh_air_fan', 'humidifier', 'grow_light']:
                device_params = device_recs.get(device_type, {})
                enhanced_params = {}
                
                for param_name, param_value in device_params.items():
                    if param_name in ['rationale', 'left_right_strategy']:
                        enhanced_params[param_name] = param_value
                        continue
                    
                    if not isinstance(param_value, dict):
                        # Convert simple value to enhanced structure
                        enhanced_params[param_name] = {
                            "current_value": param_value,
                            "recommended_value": param_value,
                            "action": "maintain" if param_value is None else "adjust",
                            "change_reason": "转换自常规格式" if param_value is not None else "保持当前值",
                            "priority": "medium" if param_value is not None else "low",
                            "urgency": "routine",
                            "risk_assessment": {
                                "adjustment_risk": "low",
                                "no_action_risk": "low",
                                "impact_scope": "参数调整"
                            }
                        }
                    else:
                        # Already enhanced format
                        enhanced_params[param_name] = param_value
                
                device_recs[device_type] = enhanced_params
            
            logger.info("[LLMClient] Successfully converted to enhanced format")
            return enhanced_decision
            
        except Exception as e:
            logger.error(f"[LLMClient] Error converting to enhanced format: {e}")
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
        logger.warning(
            f"[LLMClient] Using enhanced fallback decision strategy. Reason: {error_reason}"
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
                    "impact_scope": "系统稳定性"
                }
            }
        
        # Return enhanced fallback decision
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
                    "tem_set": create_maintain_param(18.0, "LLM不可用，保持温度设定"),
                    "tem_diff_set": create_maintain_param(2.0, "LLM不可用，保持温差设定"),
                    "cyc_on_off": create_maintain_param(0, "LLM不可用，保持循环开关"),
                    "cyc_on_time": create_maintain_param(10, "LLM不可用，保持循环时间"),
                    "cyc_off_time": create_maintain_param(10, "LLM不可用，保持循环时间"),
                    "ar_on_off": create_maintain_param(0, "LLM不可用，保持新风联动"),
                    "hum_on_off": create_maintain_param(0, "LLM不可用，保持加湿联动"),
                    "rationale": [
                        "LLM服务不可用，保持当前冷风机设定",
                        "建议人工检查温度是否在合理范围内"
                    ]
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
                        "建议人工检查CO2浓度是否在合理范围内"
                    ]
                },
                "humidifier": {
                    "model": create_maintain_param(1, "LLM不可用，保持湿度控制模式"),
                    "on": create_maintain_param(85, "LLM不可用，保持湿度开启阈值"),
                    "off": create_maintain_param(90, "LLM不可用，保持湿度关闭阈值"),
                    "left_right_strategy": "保持当前设定",
                    "rationale": [
                        "LLM服务不可用，保持当前加湿器设定",
                        "建议人工检查湿度是否在合理范围内"
                    ]
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
                    "使用增强型降级策略，所有设备参数保持当前值",
                    "强烈建议人工审核和介入"
                ],
                "llm_available": False,
                "enhanced_format": True
            }
        }
        
        return fallback
