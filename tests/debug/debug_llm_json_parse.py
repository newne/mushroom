#!/usr/bin/env python3
"""
Debug script to reproduce and fix LLM JSON parsing error
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dynaconf import Dynaconf
from loguru import logger
import requests
import json

# Configure logger
logger.remove()
logger.add(sys.stdout, level="DEBUG")

def test_llm_response():
    """Test LLM API and check response format"""
    
    # Load settings
    settings = Dynaconf(
        settings_files=['src/configs/settings.toml', 'src/configs/.secrets.toml']
    )
    
    # Extract LLM configuration
    llama_host = settings.llama.llama_host
    llama_port = settings.llama.llama_port
    model = settings.llama.model
    api_url = settings.llama.llama_completions.format(llama_host, llama_port)
    
    # Create a simple test prompt
    test_prompt = """你是一个蘑菇种植专家。请以JSON格式返回一个简单的决策建议。

要求：
1. 必须返回纯JSON格式，不要包含任何markdown标记
2. JSON结构如下：
{
    "strategy": {
        "core_objective": "测试目标",
        "priority_ranking": ["优先级1", "优先级2"],
        "key_risk_points": ["风险点1"]
    },
    "device_recommendations": {
        "air_cooler": {
            "tem_set": 16.0,
            "rationale": ["理由1"]
        }
    },
    "monitoring_points": {
        "key_time_periods": ["时段1"],
        "warning_thresholds": {},
        "emergency_measures": ["措施1"]
    }
}

请直接返回JSON，不要添加任何解释文字或markdown代码块标记。"""
    
    logger.info("=" * 80)
    logger.info("测试1: 发送简单测试提示词")
    logger.info("=" * 80)
    
    try:
        # Make direct API call to see raw response
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": test_prompt
                }
            ],
            "temperature": 0.7,
            "stream": False
        }
        
        logger.info(f"发送请求到: {api_url}")
        response = requests.post(
            api_url,
            json=payload,
            timeout=60
        )
        
        logger.info(f"响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            response_data = response.json()
            
            # Check response structure
            logger.info(f"响应键: {response_data.keys()}")
            
            if "choices" in response_data and len(response_data["choices"]) > 0:
                content = response_data["choices"][0].get("message", {}).get("content", "")
                
                logger.info("=" * 80)
                logger.info("LLM原始响应内容:")
                logger.info("=" * 80)
                logger.info(content)
                logger.info("=" * 80)
                
                # Check if content is empty
                if not content:
                    logger.error("❌ 响应内容为空！")
                    return
                
                # Check content type
                content_stripped = content.strip()
                logger.info(f"内容长度: {len(content_stripped)} 字符")
                logger.info(f"内容前100字符: {content_stripped[:100]}")
                
                # Try to parse
                import json
                try:
                    parsed = json.loads(content_stripped)
                    logger.success("✅ JSON解析成功！")
                    logger.info(f"解析后的键: {parsed.keys()}")
                except json.JSONDecodeError as e:
                    logger.error(f"❌ JSON解析失败: {e}")
                    logger.error(f"错误位置: line {e.lineno}, column {e.colno}")
                    
                    # Check for common issues
                    if content_stripped.startswith("```"):
                        logger.warning("⚠️ 响应包含markdown代码块标记")
                    if not content_stripped.startswith("{"):
                        logger.warning(f"⚠️ 响应不是以 '{{' 开头，而是: {content_stripped[:50]}")
                    
            else:
                logger.error("❌ 响应中没有choices字段或choices为空")
                logger.info(f"完整响应: {response_data}")
        else:
            logger.error(f"❌ API返回错误状态码: {response.status_code}")
            logger.error(f"错误内容: {response.text}")
            
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}", exc_info=True)


if __name__ == "__main__":
    test_llm_response()
