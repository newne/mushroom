"""
测试GetData类的get_mushroom_prompt方法
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger
from global_const.global_const import settings, create_get_data


def test_get_data_prompt():
    """测试GetData的get_mushroom_prompt方法"""
    
    logger.info("=" * 80)
    logger.info("测试GetData.get_mushroom_prompt()方法")
    logger.info("=" * 80)
    
    try:
        # 使用工厂函数创建GetData实例
        logger.info("\n[步骤1] 创建GetData实例")
        get_data = create_get_data()
        logger.success("✓ GetData实例创建成功")
        
        # 检查urls对象
        logger.info("\n[步骤2] 检查urls对象属性")
        logger.info(f"urls类型: {type(get_data.urls)}")
        
        # 尝试访问prompt_mushroom_description
        if hasattr(get_data.urls, 'prompt_mushroom_description'):
            logger.success("✓ urls.prompt_mushroom_description 存在（小写）")
            logger.info(f"  值: {get_data.urls.prompt_mushroom_description}")
        else:
            logger.warning("⚠ urls.prompt_mushroom_description 不存在（小写）")
        
        if hasattr(get_data.urls, 'PROMPT_MUSHROOM_DESCRIPTION'):
            logger.success("✓ urls.PROMPT_MUSHROOM_DESCRIPTION 存在（大写）")
            logger.info(f"  值: {get_data.urls.PROMPT_MUSHROOM_DESCRIPTION}")
        else:
            logger.warning("⚠ urls.PROMPT_MUSHROOM_DESCRIPTION 不存在（大写）")
        
        # 测试获取提示词
        logger.info("\n[步骤3] 调用get_mushroom_prompt()")
        prompt = get_data.get_mushroom_prompt()
        
        if prompt:
            logger.success("✓ 成功获取提示词")
            logger.info(f"提示词长度: {len(prompt)} 字符")
            logger.info(f"提示词前100字符: {prompt[:100]}...")
        else:
            logger.error("✗ 获取提示词失败")
            return False
        
        # 测试缓存
        logger.info("\n[步骤4] 测试缓存机制")
        prompt2 = get_data.get_mushroom_prompt()
        if prompt2 == prompt:
            logger.success("✓ 缓存机制工作正常")
        else:
            logger.warning("⚠ 缓存机制可能存在问题")
        
        logger.info("\n" + "=" * 80)
        logger.success("测试完成")
        logger.info("=" * 80)
        return True
        
    except Exception as e:
        logger.exception(f"测试过程中发生异常: {e}")
        return False


if __name__ == "__main__":
    try:
        success = test_get_data_prompt()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception(f"运行测试时发生异常: {e}")
        sys.exit(1)
