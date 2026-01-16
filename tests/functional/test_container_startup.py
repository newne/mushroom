#!/usr/bin/env python3
"""
容器启动测试脚本
用于验证容器环境中的各个组件是否正常工作
"""
import os
import sys
from pathlib import Path

def test_environment():
    """测试环境配置"""
    print("=== 环境测试 ===")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"Python路径: {sys.executable}")
    print(f"PYTHONPATH: {os.environ.get('PYTHONPATH', 'Not set')}")
    print(f"TZ: {os.environ.get('TZ', 'Not set')}")
    
    # 检查关键目录
    dirs_to_check = ['/app', '/models', '/app/configs', '/app/Logs']
    for dir_path in dirs_to_check:
        if os.path.exists(dir_path):
            print(f"✓ 目录存在: {dir_path}")
        else:
            print(f"✗ 目录不存在: {dir_path}")

def test_imports():
    """测试关键模块导入"""
    print("\n=== 模块导入测试 ===")
    
    modules_to_test = [
        'scheduling.optimized_scheduler',
        'utils.exception_listener',
        'utils.loguru_setting',
        'global_const.global_const',
        'utils.create_table'
    ]
    
    for module_name in modules_to_test:
        try:
            __import__(module_name)
            print(f"✓ 模块导入成功: {module_name}")
        except Exception as e:
            print(f"✗ 模块导入失败: {module_name} - {e}")

def test_database_config():
    """测试数据库配置"""
    print("\n=== 数据库配置测试 ===")
    
    try:
        from global_const.global_const import settings, pgsql_engine
        print(f"✓ 数据库配置加载成功")
        print(f"  数据库URL: {settings.get('database', {}).get('url', 'Not configured')}")
        
        # 测试数据库连接
        with pgsql_engine.connect() as conn:
            result = conn.execute("SELECT 1")
            print("✓ 数据库连接测试成功")
    except Exception as e:
        print(f"✗ 数据库配置/连接失败: {e}")

def test_model_path():
    """测试模型路径"""
    print("\n=== 模型路径测试 ===")
    
    model_paths = [
        '/models/clip-vit-base-patch32',
        Path(__file__).parent.parent / 'models' / 'clip-vit-base-patch32'
    ]
    
    for model_path in model_paths:
        if Path(model_path).exists():
            print(f"✓ 模型路径存在: {model_path}")
            # 检查模型文件
            config_file = Path(model_path) / 'config.json'
            if config_file.exists():
                print(f"  ✓ 配置文件存在: {config_file}")
            else:
                print(f"  ✗ 配置文件不存在: {config_file}")
        else:
            print(f"✗ 模型路径不存在: {model_path}")

if __name__ == '__main__':
    print("容器启动环境测试")
    print("=" * 50)
    
    test_environment()
    test_imports()
    test_database_config()
    test_model_path()
    
    print("\n测试完成!")