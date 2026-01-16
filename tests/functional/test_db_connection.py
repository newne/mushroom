#!/usr/bin/env python3
"""
测试数据库连接 - 用于Docker容器内部测试

用法：
  python scripts/test_db_connection.py
  
环境变量：
  prod=true  - 使用生产环境配置
  prod=false - 使用开发环境配置（默认）
"""

import sys
import os
import time
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))


def test_environment():
    """测试环境配置"""
    print("=" * 80)
    print("环境配置测试")
    print("=" * 80)
    
    prod_env = os.environ.get("prod", "false")
    print(f"环境变量 prod: {prod_env}")
    
    from global_const.global_const import get_environment, settings
    
    env = get_environment()
    print(f"当前环境: {env}")
    print(f"数据库主机: {settings.pgsql.host}")
    print(f"数据库端口: {settings.pgsql.port}")
    print(f"数据库名称: {settings.pgsql.database_name}")
    print(f"数据库用户: {settings.pgsql.username}")
    print()


def test_dns_resolution():
    """测试DNS解析"""
    print("=" * 80)
    print("DNS解析测试")
    print("=" * 80)
    
    from global_const.global_const import settings
    
    host = settings.pgsql.host
    print(f"尝试解析主机名: {host}")
    
    try:
        import socket
        ip = socket.gethostbyname(host)
        print(f"✅ DNS解析成功: {host} -> {ip}")
        return True
    except Exception as e:
        print(f"❌ DNS解析失败: {e}")
        return False


def test_tcp_connection():
    """测试TCP连接"""
    print("\n" + "=" * 80)
    print("TCP连接测试")
    print("=" * 80)
    
    from global_const.global_const import settings
    import socket
    
    host = settings.pgsql.host
    port = settings.pgsql.port
    
    print(f"尝试连接: {host}:{port}")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print(f"✅ TCP连接成功: {host}:{port}")
            return True
        else:
            print(f"❌ TCP连接失败: 错误代码 {result}")
            return False
    except Exception as e:
        print(f"❌ TCP连接异常: {e}")
        return False


def test_database_connection():
    """测试数据库连接"""
    print("\n" + "=" * 80)
    print("数据库连接测试")
    print("=" * 80)
    
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            print(f"尝试连接数据库 ({attempt}/{max_retries})...")
            
            from global_const.global_const import pgsql_engine
            
            # 测试连接
            with pgsql_engine.connect() as conn:
                result = conn.execute(sqlalchemy.text("SELECT version()"))
                version = result.scalar()
                print(f"✅ 数据库连接成功")
                print(f"PostgreSQL版本: {version}")
                return True
                
        except Exception as e:
            error_msg = str(e)
            print(f"❌ 数据库连接失败 ({attempt}/{max_retries}): {error_msg}")
            
            if attempt < max_retries:
                print(f"等待 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            else:
                print(f"已达到最大重试次数")
                return False
    
    return False


def test_connection_pool():
    """测试连接池"""
    print("\n" + "=" * 80)
    print("连接池测试")
    print("=" * 80)
    
    try:
        from global_const.global_const import pgsql_engine
        
        pool = pgsql_engine.pool
        print(f"连接池大小: {pool.size()}")
        print(f"当前连接数: {pool.checkedin()}")
        print(f"已检出连接: {pool.checkedout()}")
        print(f"溢出连接数: {pool.overflow()}")
        
        # 测试获取连接
        print("\n测试获取连接...")
        conn = pgsql_engine.connect()
        print("✅ 成功从连接池获取连接")
        
        # 测试执行查询
        result = conn.execute(sqlalchemy.text("SELECT 1 as test"))
        value = result.scalar()
        print(f"✅ 查询测试成功: {value}")
        
        conn.close()
        print("✅ 连接已归还到连接池")
        
        return True
        
    except Exception as e:
        print(f"❌ 连接池测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("\n🔍 数据库连接诊断工具")
    print("=" * 80)
    
    # 导入必要的模块
    import sqlalchemy
    
    results = []
    
    # 1. 环境配置测试
    test_environment()
    
    # 2. DNS解析测试
    dns_ok = test_dns_resolution()
    results.append(("DNS解析", dns_ok))
    
    # 3. TCP连接测试
    tcp_ok = test_tcp_connection()
    results.append(("TCP连接", tcp_ok))
    
    # 4. 数据库连接测试
    if tcp_ok:
        db_ok = test_database_connection()
        results.append(("数据库连接", db_ok))
        
        # 5. 连接池测试
        if db_ok:
            pool_ok = test_connection_pool()
            results.append(("连接池", pool_ok))
    else:
        print("\n⚠️ TCP连接失败，跳过数据库连接测试")
        results.append(("数据库连接", False))
        results.append(("连接池", False))
    
    # 显示测试结果摘要
    print("\n" + "=" * 80)
    print("📊 测试结果摘要")
    print("=" * 80)
    
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} {test_name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n总计: {passed}/{total} 项测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！数据库连接正常。")
        return 0
    else:
        print("\n❌ 部分测试失败，请检查网络和数据库配置。")
        
        # 提供诊断建议
        print("\n💡 诊断建议:")
        if not dns_ok:
            print("  - DNS解析失败：检查Docker网络配置和服务名")
            print("  - 确认postgres_db服务是否在同一网络中")
            print("  - 尝试使用IP地址代替服务名")
        elif not tcp_ok:
            print("  - TCP连接失败：检查数据库服务是否启动")
            print("  - 检查防火墙和端口配置")
            print("  - 确认数据库健康检查是否通过")
        else:
            print("  - 数据库连接失败：检查认证信息")
            print("  - 检查数据库用户权限")
            print("  - 查看数据库日志获取更多信息")
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
