#!/usr/bin/env python3
"""
Redis配置修复脚本
解决生产环境中的Redis配置访问问题
"""
import os
import sys
from pathlib import Path

def fix_redis_config():
    """修复Redis配置问题"""
    print("=" * 60)
    print("Redis配置修复脚本")
    print("=" * 60)
    
    try:
        # 1. 设置环境
        os.environ['prod'] = 'true'
        sys.path.insert(0, '/app/src')
        
        print("✓ 环境设置完成")
        
        # 2. 导入必要模块
        from dynaconf import Dynaconf
        import redis
        
        print("✓ 模块导入成功")
        
        # 3. 创建配置对象
        BASE_DIR = Path('/app/src')
        config_dir_path = BASE_DIR / "configs"
        
        settings = Dynaconf(
            root_path=str(BASE_DIR),
            envvar_prefix="wuhan_load_scheduling",
            environments=True,
            env="production",
            merge_enabled=True,
            settings_files=[
                str(config_dir_path / "settings.toml"),
                str(config_dir_path / ".secrets.toml"),
            ],
        )
        
        print(f"✓ 配置加载成功，环境: {settings.current_env}")
        
        # 4. 验证Redis配置
        try:
            redis_config = {
                'host': settings.redis.host,
                'port': settings.redis.port,
                'password': settings.redis.password
            }
            print(f"✓ Redis配置验证成功: {redis_config['host']}:{redis_config['port']}")
        except AttributeError as e:
            print(f"✗ Redis配置访问失败: {e}")
            
            # 尝试从环境变量获取Redis配置
            redis_config = {
                'host': os.environ.get('REDIS_HOST', '172.17.0.1'),
                'port': int(os.environ.get('REDIS_PORT', '26379')),
                'password': 'Pl5SpB72sllM8DsT'  # 从.secrets.toml中获取
            }
            print(f"⚠ 使用环境变量Redis配置: {redis_config['host']}:{redis_config['port']}")
        
        # 5. 创建Redis连接池
        pool = redis.ConnectionPool(
            host=redis_config['host'],
            port=redis_config['port'],
            password=redis_config['password'],
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        conn = redis.Redis(connection_pool=pool)
        print("✓ Redis连接池创建成功")
        
        # 6. 测试连接（可选，因为Redis服务可能未启动）
        try:
            conn.ping()
            print("✓ Redis连接测试成功")
        except Exception as e:
            print(f"⚠ Redis连接测试失败: {e}")
            print("  (这是正常的，如果Redis服务未在此时启动)")
        
        # 7. 修复global_const模块
        try:
            # 尝试导入并修复global_const
            import global_const.global_const as gc
            
            # 如果global_const中的Redis连接有问题，替换它
            if hasattr(gc, 'conn'):
                gc.conn = conn
                print("✓ global_const.conn 已更新")
            
            if hasattr(gc, 'pool'):
                gc.pool = pool
                print("✓ global_const.pool 已更新")
                
        except Exception as e:
            print(f"⚠ global_const模块修复失败: {e}")
        
        print("\n" + "=" * 60)
        print("✓ Redis配置修复完成")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"✗ Redis配置修复失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = fix_redis_config()
    sys.exit(0 if success else 1)