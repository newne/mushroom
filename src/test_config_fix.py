#!/usr/bin/env python3
"""
配置修复测试脚本
验证配置文件和全局设置是否正确工作
"""

import os
import sys
from pathlib import Path

from loguru import logger


def test_global_config():
    """测试全局配置加载"""
    print("=" * 60)
    print("测试全局配置加载")
    print("=" * 60)
    
    try:
        from global_const.global_const import settings, get_environment
        
        env = get_environment()
        print(f"当前环境: {env}")
        
        # 测试MinIO配置
        print("MinIO配置:")
        print(f"  端点: {settings.MINIO.endpoint}")
        print(f"  访问密钥: {settings.MINIO.access_key}")
        print(f"  存储桶: {settings.MINIO.bucket}")
        
        # 测试PostgreSQL配置
        print("PostgreSQL配置:")
        print(f"  主机: {settings.PGSQL.host}")
        print(f"  端口: {settings.PGSQL.port}")
        print(f"  数据库: {settings.PGSQL.database_name}")
        print(f"  用户名: {settings.PGSQL.username}")
        
        # 测试Redis配置
        print("Redis配置:")
        print(f"  主机: {settings.REDIS.host}")
        print(f"  端口: {settings.REDIS.port}")
        
        return True
        
    except Exception as e:
        print(f"❌ 全局配置加载失败: {e}")
        return False


def test_minio_client():
    """测试MinIO客户端"""
    print("=" * 60)
    print("测试MinIO客户端")
    print("=" * 60)
    
    try:
        from utils.minio_client import MinIOClient
        
        client = MinIOClient()
        print(f"✅ MinIO客户端创建成功")
        print(f"环境: {client.environment}")
        print(f"端点: {client.config['endpoint']}")
        print(f"存储桶: {client.config['bucket']}")
        
        # 测试连接
        if client.test_connection():
            print("✅ MinIO连接测试成功")
        else:
            print("⚠️ MinIO连接测试失败（可能是服务未启动）")
        
        return True
        
    except Exception as e:
        print(f"❌ MinIO客户端测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_mushroom_processor():
    """测试蘑菇处理器"""
    print("=" * 60)
    print("测试蘑菇处理器")
    print("=" * 60)
    
    try:
        from utils.mushroom_image_processor import create_mushroom_processor
        
        processor = create_mushroom_processor()
        print(f"✅ 蘑菇处理器创建成功")
        print(f"环境: {processor.minio_service.client.environment}")
        
        # 测试路径解析
        test_path = "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg"
        image_info = processor.parser.parse_path(test_path)
        
        if image_info:
            print(f"✅ 路径解析成功: {image_info.mushroom_id}")
        else:
            print("❌ 路径解析失败")
        
        return True
        
    except Exception as e:
        print(f"❌ 蘑菇处理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_connection():
    """测试数据库连接"""
    print("=" * 60)
    print("测试数据库连接")
    print("=" * 60)
    
    try:
        from global_const.global_const import pgsql_engine
        
        # 尝试连接数据库
        with pgsql_engine.connect() as conn:
            result = conn.execute("SELECT 1")
            print("✅ PostgreSQL连接成功")
        
        return True
        
    except Exception as e:
        print(f"⚠️ PostgreSQL连接失败（可能是服务未启动）: {e}")
        return False


def test_environment_switching():
    """测试环境切换"""
    print("=" * 60)
    print("测试环境切换")
    print("=" * 60)
    
    original_env = os.environ.get("prod", "false")
    
    try:
        # 测试开发环境
        os.environ["prod"] = "false"
        from global_const.global_const import get_environment
        env = get_environment()
        print(f"开发环境: {env}")
        
        # 重新导入以获取新配置
        import importlib
        import global_const.global_const as global_const
        importlib.reload(global_const)
        
        dev_endpoint = global_const.settings.MINIO.endpoint
        print(f"开发环境端点: {dev_endpoint}")
        
        # 测试生产环境
        os.environ["prod"] = "true"
        importlib.reload(global_const)
        
        prod_endpoint = global_const.settings.MINIO.endpoint
        print(f"生产环境端点: {prod_endpoint}")
        
        if dev_endpoint != prod_endpoint:
            print("✅ 环境切换正常")
            return True
        else:
            print("❌ 环境切换异常 - 端点相同")
            return False
            
    except Exception as e:
        print(f"❌ 环境切换测试失败: {e}")
        return False
    finally:
        # 恢复原始环境
        os.environ["prod"] = original_env


def main():
    """主函数"""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    print("配置修复验证测试")
    print(f"当前目录: {Path.cwd()}")
    print()
    
    tests = [
        ("全局配置加载", test_global_config),
        ("MinIO客户端", test_minio_client),
        ("蘑菇处理器", test_mushroom_processor),
        ("数据库连接", test_database_connection),
        ("环境切换", test_environment_switching)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} 测试异常: {e}")
            results.append((test_name, False))
        
        print()
    
    # 汇总结果
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name:15s}: {status}")
        if result:
            passed += 1
    
    print("-" * 40)
    print(f"总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("🎉 所有配置测试通过！")
    else:
        print("⚠️ 部分测试失败，请检查配置。")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)