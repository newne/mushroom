"""
调试提示词API配置
检查配置是否正确加载
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger
from global_const.global_const import settings


def debug_config():
    """调试配置加载情况"""
    
    logger.info("=" * 80)
    logger.info("开始调试提示词API配置")
    logger.info("=" * 80)
    
    # 检查data_source_url
    logger.info("\n[1] 检查 settings.data_source_url")
    if hasattr(settings, 'data_source_url'):
        logger.success("✓ settings.data_source_url 存在")
        
        # 列出所有属性
        logger.info("data_source_url 的所有属性:")
        for attr in dir(settings.data_source_url):
            if not attr.startswith('_'):
                value = getattr(settings.data_source_url, attr, None)
                if isinstance(value, str):
                    logger.info(f"  - {attr}: {value}")
        
        # 检查prompt_mushroom_description
        if hasattr(settings.data_source_url, 'prompt_mushroom_description'):
            logger.success("✓ settings.data_source_url.prompt_mushroom_description 存在")
            logger.info(f"  值: {settings.data_source_url.prompt_mushroom_description}")
        else:
            logger.error("✗ settings.data_source_url.prompt_mushroom_description 不存在")
    else:
        logger.error("✗ settings.data_source_url 不存在")
    
    # 检查prompt配置
    logger.info("\n[2] 检查 settings.prompt")
    if hasattr(settings, 'prompt'):
        logger.success("✓ settings.prompt 存在")
        
        # 检查backend_token
        if hasattr(settings.prompt, 'backend_token'):
            logger.success("✓ settings.prompt.backend_token 存在")
            token = settings.prompt.backend_token
            logger.info(f"  值: {token[:20]}... (长度: {len(token)})")
        else:
            logger.error("✗ settings.prompt.backend_token 不存在")
            
        # 检查其他属性
        if hasattr(settings.prompt, 'username'):
            logger.info(f"  username: {settings.prompt.username}")
        if hasattr(settings.prompt, 'password'):
            logger.info(f"  password: ***")
    else:
        logger.error("✗ settings.prompt 不存在")
    
    # 检查host配置
    logger.info("\n[3] 检查 settings.host")
    if hasattr(settings, 'host'):
        logger.success("✓ settings.host 存在")
        if hasattr(settings.host, 'host'):
            logger.info(f"  host: {settings.host.host}")
        if hasattr(settings.host, 'port'):
            logger.info(f"  port: {settings.host.port}")
    else:
        logger.error("✗ settings.host 不存在")
    
    # 检查llama配置
    logger.info("\n[4] 检查 settings.llama")
    if hasattr(settings, 'llama'):
        logger.success("✓ settings.llama 存在")
        if hasattr(settings.llama, 'mushroom_descripe_prompt'):
            prompt = settings.llama.mushroom_descripe_prompt
            logger.success("✓ settings.llama.mushroom_descripe_prompt 存在")
            logger.info(f"  长度: {len(prompt)} 字符")
            logger.info(f"  前100字符: {prompt[:100]}...")
        else:
            logger.error("✗ settings.llama.mushroom_descripe_prompt 不存在")
    else:
        logger.error("✗ settings.llama 不存在")
    
    # 测试URL格式化
    logger.info("\n[5] 测试URL格式化")
    try:
        if hasattr(settings, 'data_source_url') and hasattr(settings.data_source_url, 'prompt_mushroom_description'):
            url_template = settings.data_source_url.prompt_mushroom_description
            if hasattr(settings, 'host') and hasattr(settings.host, 'host'):
                formatted_url = url_template.format(host=settings.host.host)
                logger.success(f"✓ URL格式化成功: {formatted_url}")
            else:
                logger.warning("⚠ 无法格式化URL，缺少host配置")
        else:
            logger.error("✗ 无法测试URL格式化，缺少配置")
    except Exception as e:
        logger.error(f"✗ URL格式化失败: {e}")
    
    logger.info("\n" + "=" * 80)
    logger.info("配置调试完成")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        debug_config()
    except Exception as e:
        logger.exception(f"调试过程中发生异常: {e}")
        sys.exit(1)
