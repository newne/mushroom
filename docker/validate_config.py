#!/usr/bin/env python3
"""
配置验证脚本 - 用于诊断容器内配置加载问题
"""
import os
import sys
from pathlib import Path

def validate_config():
    """验证配置加载"""
    print("=" * 60)
    print("配置验证脚本")
    print("=" * 60)
    
    # 1. 检查环境变量
    print("\n1. 环境变量检查:")
    print(f"   prod: {os.environ.get('prod', 'NOT_SET')}")
    print(f"   PYTHONPATH: {os.environ.get('PYTHONPATH', 'NOT_SET')}")
    print(f"   REDIS_HOST: {os.environ.get('REDIS_HOST', 'NOT_SET')}")
    print(f"   REDIS_PORT: {os.environ.get('REDIS_PORT', 'NOT_SET')}")
    
    # 2. 检查文件系统
    print("\n2. 文件系统检查:")
    config_dir = Path("/app/configs")
    print(f"   配置目录存在: {config_dir.exists()}")
    if config_dir.exists():
        print(f"   配置目录权限: {oct(config_dir.stat().st_mode)[-3:]}")
        for config_file in ["settings.toml", ".secrets.toml"]:
            file_path = config_dir / config_file
            print(f"   {config_file}: 存在={file_path.exists()}, 大小={file_path.stat().st_size if file_path.exists() else 'N/A'}")
    
    # 3. 检查Python路径
    print("\n3. Python路径检查:")
    print(f"   当前工作目录: {os.getcwd()}")
    print(f"   Python路径: {sys.path[:3]}...")  # 只显示前3个
    
    # 4. 尝试导入Dynaconf
    print("\n4. Dynaconf导入测试:")
    try:
        from dynaconf import Dynaconf
        print("   ✓ Dynaconf导入成功")
    except ImportError as e:
        print(f"   ✗ Dynaconf导入失败: {e}")
        return False
    
    # 5. 尝试加载配置
    print("\n5. 配置加载测试:")
    try:
        # 设置路径
        sys.path.insert(0, '/app/src')
        BASE_DIR = Path('/app/src')
        config_dir_path = BASE_DIR / "configs"
        
        # 确定环境
        env = "production" if os.environ.get("prod", "false").lower() == "true" else "development"
        print(f"   检测到环境: {env}")
        
        # 创建配置对象
        settings = Dynaconf(
            root_path=str(BASE_DIR),
            envvar_prefix="wuhan_load_scheduling",
            environments=True,
            env=env,
            merge_enabled=True,
            settings_files=[
                str(config_dir_path / "settings.toml"),
                str(config_dir_path / ".secrets.toml"),
            ],
        )
        
        print("   ✓ 配置对象创建成功")
        print(f"   当前环境: {settings.current_env}")
        
        # 6. 测试Redis配置访问
        print("\n6. Redis配置访问测试:")
        try:
            redis_host = settings.redis.host
            redis_port = settings.redis.port
            redis_password = settings.redis.password
            print(f"   ✓ Redis配置访问成功")
            print(f"   Host: {redis_host}")
            print(f"   Port: {redis_port}")
            print(f"   Password: {'***' if redis_password else 'None'}")
        except AttributeError as e:
            print(f"   ✗ Redis配置访问失败: {e}")
            
            # 尝试调试配置内容
            print("\n   调试信息:")
            config_dict = settings.as_dict()
            print(f"   配置键: {list(config_dict.keys())}")
            if 'redis' in config_dict:
                print(f"   redis配置: {config_dict['redis']}")
            else:
                print("   ✗ 配置中没有找到redis键")
            return False
        
        # 7. 测试Redis连接
        print("\n7. Redis连接测试:")
        try:
            import redis
            pool = redis.ConnectionPool(
                host=settings.redis.host,
                port=settings.redis.port,
                password=settings.redis.password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            conn = redis.Redis(connection_pool=pool)
            # 测试连接
            conn.ping()
            print("   ✓ Redis连接测试成功")
        except Exception as e:
            print(f"   ⚠ Redis连接测试失败: {e}")
            print("   (这可能是正常的，如果Redis服务未启动)")
        
        print("\n" + "=" * 60)
        print("✓ 配置验证完成 - 所有基础配置正常")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"   ✗ 配置加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = validate_config()
    sys.exit(0 if success else 1)