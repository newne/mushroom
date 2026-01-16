"""
测试从API动态获取蘑菇描述提示词的功能
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger
from global_const.global_const import settings
from utils.get_data import GetData


def test_get_mushroom_prompt():
    """测试获取蘑菇提示词功能"""
    
    logger.info("=" * 80)
    logger.info("开始测试从API获取蘑菇描述提示词")
    logger.info("=" * 80)
    
    # 初始化GetData实例
    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )
    
    # 测试获取提示词
    logger.info("\n[测试1] 首次获取提示词（从API）")
    prompt = get_data.get_mushroom_prompt()
    
    if prompt:
        logger.success(f"✓ 成功获取提示词")
        logger.info(f"提示词长度: {len(prompt)} 字符")
        logger.info(f"提示词前200字符: {prompt[:200]}...")
    else:
        logger.error("✗ 获取提示词失败")
        return False
    
    # 测试缓存机制
    logger.info("\n[测试2] 再次获取提示词（应使用缓存）")
    prompt2 = get_data.get_mushroom_prompt()
    
    if prompt2 == prompt:
        logger.success("✓ 缓存机制工作正常")
    else:
        logger.warning("⚠ 缓存机制可能存在问题")
    
    # 显示配置信息
    logger.info("\n[配置信息]")
    logger.info(f"API URL: {settings.data_source_url.prompt_mushroom_description.format(host=settings.host.host)}")
    logger.info(f"Authorization Token: {settings.prompt.backend_token[:20]}...")
    
    logger.info("\n" + "=" * 80)
    logger.success("测试完成")
    logger.info("=" * 80)
    
    return True


if __name__ == "__main__":
    try:
        success = test_get_mushroom_prompt()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception(f"测试过程中发生异常: {e}")
        sys.exit(1)
