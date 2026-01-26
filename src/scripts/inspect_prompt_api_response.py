"""
检查提示词API的实际响应结构
"""

import sys
import json
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

import requests
from loguru import logger
from global_const.global_const import settings


def inspect_api_response():
    """检查API的实际响应"""
    
    logger.info("=" * 80)
    logger.info("检查提示词API响应结构")
    logger.info("=" * 80)
    
    # 构建URL
    url = settings.data_source_url.prompt_mushroom_description.format(host=settings.host.host)
    logger.info(f"\nAPI URL: {url}")
    
    # 构建请求头
    headers = {
        "Authorization": settings.prompt.backend_token,
        "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    logger.info(f"Authorization: {settings.prompt.backend_token[:20]}...")
    
    try:
        logger.info("\n发送GET请求...")
        response = requests.get(url, headers=headers, timeout=10)
        
        logger.info(f"响应状态码: {response.status_code}")
        logger.info(f"响应头: {dict(response.headers)}")
        
        if response.status_code == 200:
            logger.success("✓ 请求成功")
            
            # 解析JSON
            data = response.json()
            logger.info(f"\n响应数据类型: {type(data)}")
            
            if isinstance(data, dict):
                logger.info(f"响应顶层键: {list(data.keys())}")
                
                # 美化打印JSON
                logger.info("\n完整响应（格式化）:")
                print(json.dumps(data, indent=2, ensure_ascii=False))
                
                # 分析结构
                logger.info("\n结构分析:")
                for key, value in data.items():
                    logger.info(f"  - {key}: {type(value).__name__}")
                    if isinstance(value, dict):
                        logger.info(f"    子键: {list(value.keys())}")
                        for sub_key, sub_value in value.items():
                            logger.info(f"      - {sub_key}: {type(sub_value).__name__}")
                            if isinstance(sub_value, str):
                                logger.info(f"        长度: {len(sub_value)} 字符")
                                if len(sub_value) > 100:
                                    logger.info(f"        前100字符: {sub_value[:100]}...")
                    elif isinstance(value, str):
                        logger.info(f"    长度: {len(value)} 字符")
                        if len(value) > 100:
                            logger.info(f"    前100字符: {value[:100]}...")
            else:
                logger.warning(f"响应不是字典类型: {type(data)}")
                logger.info(f"响应内容: {data}")
        else:
            logger.error(f"✗ 请求失败")
            logger.error(f"响应内容: {response.text}")
            
    except requests.exceptions.Timeout:
        logger.error("✗ 请求超时")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"✗ 连接失败: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"✗ JSON解析失败: {e}")
        logger.error(f"原始响应: {response.text}")
    except Exception as e:
        logger.exception(f"✗ 发生异常: {e}")
    
    logger.info("\n" + "=" * 80)


if __name__ == "__main__":
    inspect_api_response()
