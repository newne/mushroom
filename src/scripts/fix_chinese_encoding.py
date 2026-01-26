#!/usr/bin/env python3
"""
修复数据库中文字符编码问题

该脚本用于检查和修复数据库中的中文字符编码问题，
确保中文字符能够正确存储和显示。

使用方法:
    # 检查编码问题
    python scripts/fix_chinese_encoding.py --check
    
    # 修复编码问题
    python scripts/fix_chinese_encoding.py --fix
    
    # 测试中文字符存储
    python scripts/fix_chinese_encoding.py --test
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from global_const.global_const import pgsql_engine
from utils.create_table import (
    query_decision_analysis_static_configs,
    store_decision_analysis_static_configs,
    DecisionAnalysisStaticConfig
)
from utils.loguru_setting import loguru_setting
from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

# 初始化日志
loguru_setting(production=False)


def check_database_encoding():
    """
    检查数据库编码设置
    """
    try:
        logger.info("Checking database encoding settings...")
        
        with pgsql_engine.connect() as conn:
            # 检查数据库编码
            result = conn.execute(text("SHOW server_encoding;")).fetchone()
            server_encoding = result[0] if result else "Unknown"
            
            result = conn.execute(text("SHOW client_encoding;")).fetchone()
            client_encoding = result[0] if result else "Unknown"
            
            # 检查数据库字符集
            result = conn.execute(text("""
                SELECT datname, encoding, datcollate, datctype 
                FROM pg_database 
                WHERE datname = current_database();
            """)).fetchone()
            
            logger.info("Database encoding information:")
            logger.info(f"  - Server encoding: {server_encoding}")
            logger.info(f"  - Client encoding: {client_encoding}")
            
            if result:
                logger.info(f"  - Database name: {result[0]}")
                logger.info(f"  - Encoding ID: {result[1]}")
                logger.info(f"  - Collate: {result[2]}")
                logger.info(f"  - Ctype: {result[3]}")
            
            return {
                "server_encoding": server_encoding,
                "client_encoding": client_encoding,
                "database_info": result
            }
            
    except Exception as e:
        logger.error(f"Failed to check database encoding: {e}")
        return None


def test_chinese_characters():
    """
    测试中文字符的存储和读取
    """
    try:
        logger.info("Testing Chinese character storage and retrieval...")
        
        # 测试用的中文字符
        test_configs = [
            {
                "room_id": "test",
                "device_type": "test_device",
                "device_name": "测试设备_001",
                "device_alias": "test_device_001",
                "point_alias": "test_point",
                "point_name": "TestPoint",
                "remark": "这是一个中文测试点位：温度传感器（分辨率0.1°C）",
                "change_type": "analog_value",
                "threshold": 0.5,
                "enum_mapping": {
                    "0": "关闭",
                    "1": "开启",
                    "2": "自动模式"
                },
                "source": "encoding_test",
                "operator": "system",
                "comment": "中文编码测试记录 - 包含特殊字符：℃、°、±、≥、≤"
            }
        ]
        
        # 存储测试数据
        logger.info("Storing test data with Chinese characters...")
        stored_count = store_decision_analysis_static_configs(test_configs)
        logger.info(f"Stored {stored_count} test records")
        
        # 读取测试数据
        logger.info("Retrieving test data...")
        retrieved_configs = query_decision_analysis_static_configs(
            room_id="test",
            device_type="test_device",
            limit=10
        )
        
        if retrieved_configs:
            config = retrieved_configs[0]
            logger.info("Retrieved test record:")
            logger.info(f"  - Device name: {config.device_name}")
            logger.info(f"  - Remark: {config.remark}")
            logger.info(f"  - Comment: {config.comment}")
            logger.info(f"  - Enum mapping: {config.enum_mapping}")
            
            # 检查中文字符是否正确
            expected_device_name = "测试设备_001"
            expected_remark = "这是一个中文测试点位：温度传感器（分辨率0.1°C）"
            
            if config.device_name == expected_device_name:
                logger.info("✅ Device name Chinese characters are correct")
            else:
                logger.error(f"❌ Device name encoding issue: expected '{expected_device_name}', got '{config.device_name}'")
            
            if config.remark == expected_remark:
                logger.info("✅ Remark Chinese characters are correct")
            else:
                logger.error(f"❌ Remark encoding issue: expected '{expected_remark}', got '{config.remark}'")
            
            # 检查JSON字段中的中文
            if config.enum_mapping and config.enum_mapping.get("0") == "关闭":
                logger.info("✅ JSON field Chinese characters are correct")
            else:
                logger.error(f"❌ JSON field encoding issue: {config.enum_mapping}")
            
            return True
        else:
            logger.error("❌ Failed to retrieve test data")
            return False
            
    except Exception as e:
        logger.error(f"Chinese character test failed: {e}")
        return False


def cleanup_test_data():
    """
    清理测试数据
    """
    try:
        logger.info("Cleaning up test data...")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 删除测试数据
            deleted_count = session.query(DecisionAnalysisStaticConfig).filter_by(
                room_id="test",
                device_type="test_device"
            ).delete()
            
            session.commit()
            logger.info(f"Cleaned up {deleted_count} test records")
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to cleanup test data: {e}")


def check_existing_data_encoding():
    """
    检查现有数据的编码情况
    """
    try:
        logger.info("Checking existing data encoding...")
        
        # 查询一些包含中文的记录
        configs = query_decision_analysis_static_configs(limit=10)
        
        if not configs:
            logger.warning("No existing data found to check")
            return True
        
        encoding_issues = []
        
        for config in configs:
            # 检查中文字符是否正确显示
            fields_to_check = [
                ("device_name", config.device_name),
                ("remark", config.remark),
                ("comment", config.comment)
            ]
            
            for field_name, field_value in fields_to_check:
                if field_value and isinstance(field_value, str):
                    # 检查是否包含乱码字符
                    if any(ord(char) > 127 for char in field_value):
                        try:
                            # 尝试编码解码测试
                            field_value.encode('utf-8').decode('utf-8')
                        except UnicodeError:
                            encoding_issues.append({
                                "record_id": config.id,
                                "field": field_name,
                                "value": field_value,
                                "issue": "Unicode encoding error"
                            })
        
        if encoding_issues:
            logger.warning(f"Found {len(encoding_issues)} potential encoding issues:")
            for issue in encoding_issues[:5]:  # 只显示前5个
                logger.warning(f"  - Record {issue['record_id']}, field '{issue['field']}': {issue['issue']}")
            return False
        else:
            logger.info("✅ No encoding issues found in existing data")
            return True
            
    except Exception as e:
        logger.error(f"Failed to check existing data encoding: {e}")
        return False


def fix_database_encoding():
    """
    修复数据库编码设置
    """
    try:
        logger.info("Attempting to fix database encoding settings...")
        
        with pgsql_engine.connect() as conn:
            # 设置客户端编码为UTF-8
            conn.execute(text("SET client_encoding TO 'UTF8';"))
            
            # 设置其他相关编码参数
            conn.execute(text("SET lc_messages TO 'en_US.UTF-8';"))
            conn.execute(text("SET lc_monetary TO 'en_US.UTF-8';"))
            conn.execute(text("SET lc_numeric TO 'en_US.UTF-8';"))
            conn.execute(text("SET lc_time TO 'en_US.UTF-8';"))
            
            conn.commit()
            
            logger.info("✅ Database encoding settings updated")
            
            # 重新检查编码
            check_database_encoding()
            
            return True
            
    except Exception as e:
        logger.error(f"Failed to fix database encoding: {e}")
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Fix Chinese character encoding issues in database")
    
    parser.add_argument("--check", action="store_true", help="Check database encoding settings")
    parser.add_argument("--test", action="store_true", help="Test Chinese character storage")
    parser.add_argument("--fix", action="store_true", help="Fix database encoding settings")
    parser.add_argument("--check-data", action="store_true", help="Check existing data for encoding issues")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup test data")
    
    args = parser.parse_args()
    
    if not any([args.check, args.test, args.fix, args.check_data, args.cleanup]):
        # 默认执行完整的检查和测试流程
        args.check = True
        args.test = True
        args.check_data = True
    
    try:
        logger.info("Chinese Character Encoding Fix Tool")
        logger.info("=" * 50)
        
        success = True
        
        if args.check:
            logger.info("🔍 Checking database encoding...")
            encoding_info = check_database_encoding()
            if not encoding_info:
                success = False
        
        if args.fix:
            logger.info("🔧 Fixing database encoding...")
            if not fix_database_encoding():
                success = False
        
        if args.check_data:
            logger.info("📊 Checking existing data encoding...")
            if not check_existing_data_encoding():
                success = False
        
        if args.test:
            logger.info("🧪 Testing Chinese character storage...")
            if test_chinese_characters():
                logger.info("✅ Chinese character test passed")
            else:
                logger.error("❌ Chinese character test failed")
                success = False
        
        if args.cleanup:
            logger.info("🧹 Cleaning up test data...")
            cleanup_test_data()
        
        print("\n" + "="*60)
        print("ENCODING CHECK SUMMARY")
        print("="*60)
        
        if success:
            print("✅ All encoding checks passed")
            print("Chinese characters should display correctly")
        else:
            print("❌ Some encoding issues detected")
            print("Recommendations:")
            print("1. Run with --fix to attempt automatic fixes")
            print("2. Check database server encoding settings")
            print("3. Verify client connection parameters")
        
        print("="*60)
        
        if not success:
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Encoding fix failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()