"""
提示词API使用示例
演示如何在不同场景下使用动态获取的提示词
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger
from global_const.global_const import settings
from utils.get_data import GetData


def example_1_basic_usage():
    """示例1: 基本使用"""
    logger.info("=" * 60)
    logger.info("示例1: 基本使用")
    logger.info("=" * 60)
    
    # 创建GetData实例
    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )
    
    # 获取提示词
    prompt = get_data.get_mushroom_prompt()
    
    if prompt:
        logger.success(f"✓ 成功获取提示词")
        logger.info(f"提示词长度: {len(prompt)} 字符")
        logger.info(f"提示词开头: {prompt[:100]}...")
    else:
        logger.error("✗ 获取提示词失败")
    
    return prompt


def example_2_with_fallback():
    """示例2: 带降级处理的使用"""
    logger.info("\n" + "=" * 60)
    logger.info("示例2: 带降级处理的使用")
    logger.info("=" * 60)
    
    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )
    
    # 获取提示词，如果失败则使用默认值
    prompt = get_data.get_mushroom_prompt()
    if not prompt:
        logger.warning("API获取失败，使用配置文件中的默认提示词")
        prompt = settings.llama.mushroom_descripe_prompt
    
    logger.info(f"最终使用的提示词长度: {len(prompt)} 字符")
    return prompt


def example_3_multiple_calls():
    """示例3: 多次调用（测试缓存）"""
    logger.info("\n" + "=" * 60)
    logger.info("示例3: 多次调用（测试缓存）")
    logger.info("=" * 60)
    
    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )
    
    # 第一次调用
    logger.info("第1次调用（从API获取）...")
    prompt1 = get_data.get_mushroom_prompt()
    
    # 第二次调用
    logger.info("第2次调用（应使用缓存）...")
    prompt2 = get_data.get_mushroom_prompt()
    
    # 第三次调用
    logger.info("第3次调用（应使用缓存）...")
    prompt3 = get_data.get_mushroom_prompt()
    
    # 验证缓存
    if prompt1 == prompt2 == prompt3:
        logger.success("✓ 缓存机制工作正常，三次调用返回相同内容")
    else:
        logger.warning("⚠ 缓存机制可能存在问题")
    
    return prompt1


def example_4_in_llama_context():
    """示例4: 在LLaMA调用上下文中使用"""
    logger.info("\n" + "=" * 60)
    logger.info("示例4: 在LLaMA调用上下文中使用")
    logger.info("=" * 60)
    
    get_data = GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )
    
    # 获取提示词
    prompt = get_data.get_mushroom_prompt()
    if not prompt:
        prompt = settings.llama.mushroom_descripe_prompt
    
    # 构建LLaMA请求（示例）
    llama_payload = {
        "model": settings.llama.model,
        "messages": [
            {
                "role": "system",
                "content": prompt  # 使用动态获取的提示词
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
                ]
            }
        ],
        "max_tokens": -1,
        "temperature": 0.7,
        "stream": False
    }
    
    logger.info("✓ 成功构建LLaMA请求payload")
    logger.info(f"System prompt长度: {len(llama_payload['messages'][0]['content'])} 字符")
    
    return llama_payload


def example_5_error_handling():
    """示例5: 错误处理示例"""
    logger.info("\n" + "=" * 60)
    logger.info("示例5: 错误处理示例")
    logger.info("=" * 60)
    
    try:
        get_data = GetData(
            urls=settings.data_source_url,
            host=settings.host.host,
            port=settings.host.port
        )
        
        prompt = get_data.get_mushroom_prompt()
        
        if prompt:
            logger.success("✓ 成功获取提示词")
            # 使用提示词进行后续处理
            process_with_prompt(prompt)
        else:
            logger.warning("⚠ API获取失败，使用默认提示词")
            # 使用默认提示词
            default_prompt = settings.llama.mushroom_descripe_prompt
            process_with_prompt(default_prompt)
            
    except Exception as e:
        logger.error(f"✗ 发生异常: {e}")
        # 异常处理逻辑
        logger.info("使用配置文件中的默认提示词作为后备")
        default_prompt = settings.llama.mushroom_descripe_prompt
        process_with_prompt(default_prompt)


def process_with_prompt(prompt: str):
    """使用提示词进行处理的示例函数"""
    logger.info(f"正在使用提示词进行处理（长度: {len(prompt)} 字符）")
    # 这里是实际的处理逻辑
    pass


def main():
    """运行所有示例"""
    logger.info("开始运行提示词API使用示例\n")
    
    try:
        # 运行各个示例
        example_1_basic_usage()
        example_2_with_fallback()
        example_3_multiple_calls()
        example_4_in_llama_context()
        example_5_error_handling()
        
        logger.info("\n" + "=" * 60)
        logger.success("所有示例运行完成")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception(f"运行示例时发生异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
