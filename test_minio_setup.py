#!/usr/bin/env python3
"""
MinIO配置测试脚本
快速验证MinIO配置是否正确
"""

import sys
import os
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.utils.minio_service import create_minio_service
from loguru import logger


def test_minio_configuration():
    """测试MinIO配置"""
    print("=" * 60)
    print("MinIO配置测试")
    print("=" * 60)
    
    try:
        # 创建服务
        service = create_minio_service()
        
        # 显示配置信息
        print(f"当前环境: {service.client.environment}")
        print(f"MinIO端点: {service.client.config['endpoint']}")
        print(f"存储桶: {service.client.config['bucket']}")
        print(f"区域: {service.client.config['region']}")
        print("-" * 40)
        
        # 健康检查
        print("执行健康检查...")
        health = service.health_check()
        
        if health['healthy']:
            print("✅ MinIO服务健康")
            print(f"   连接状态: {'正常' if health['connection'] else '异常'}")
            print(f"   存储桶状态: {'存在' if health['bucket_exists'] else '不存在'}")
            print(f"   图片数量: {health['image_count']}")
        else:
            print("❌ MinIO服务异常")
            for error in health['errors']:
                print(f"   错误: {error}")
        
        print("-" * 40)
        
        # 如果连接正常，获取统计信息
        if health['healthy'] and health['image_count'] > 0:
            print("获取图片统计信息...")
            stats = service.get_image_statistics()
            
            print(f"总图片数: {stats['total_images']}")
            print(f"总大小: {stats['total_size_mb']} MB")
            print(f"平均大小: {stats['average_size_bytes']} bytes")
            
            if stats['extension_stats']:
                print("文件类型分布:")
                for ext, info in stats['extension_stats'].items():
                    print(f"  {ext}: {info['count']} 个文件")
        
        return health['healthy']
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


def test_environment_switching():
    """测试环境切换"""
    print("\n" + "=" * 60)
    print("环境切换测试")
    print("=" * 60)
    
    original_env = os.environ.get("prod", "false")
    
    try:
        # 测试开发环境
        os.environ["prod"] = "false"
        dev_service = create_minio_service()
        print(f"开发环境端点: {dev_service.client.config['endpoint']}")
        
        # 测试生产环境
        os.environ["prod"] = "true"
        prod_service = create_minio_service()
        print(f"生产环境端点: {prod_service.client.config['endpoint']}")
        
        # 验证配置不同
        if dev_service.client.config['endpoint'] != prod_service.client.config['endpoint']:
            print("✅ 环境切换配置正确")
            return True
        else:
            print("❌ 环境切换配置错误 - 端点相同")
            return False
            
    except Exception as e:
        print(f"❌ 环境切换测试失败: {e}")
        return False
    finally:
        # 恢复原始环境
        os.environ["prod"] = original_env


def main():
    """主函数"""
    # 设置日志级别
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    print("MinIO存储服务配置验证")
    print(f"项目路径: {project_root}")
    print()
    
    # 测试当前环境配置
    config_ok = test_minio_configuration()
    
    # 测试环境切换
    switch_ok = test_environment_switching()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    if config_ok and switch_ok:
        print("✅ 所有测试通过！MinIO配置正确。")
        print("\n使用说明:")
        print("1. 开发环境: export prod=false")
        print("2. 生产环境: export prod=true")
        print("3. 运行示例: python examples/minio_example.py")
        print("4. 查看文档: docs/minio_setup_guide.md")
    else:
        print("❌ 部分测试失败，请检查配置。")
        
        if not config_ok:
            print("- MinIO服务连接失败")
        if not switch_ok:
            print("- 环境切换配置错误")


if __name__ == "__main__":
    main()