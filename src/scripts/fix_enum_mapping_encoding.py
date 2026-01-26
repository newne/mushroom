#!/usr/bin/env python3
"""
修复enum_mapping中的Unicode转义字符问题

该脚本用于检查和修复数据库中enum_mapping字段的Unicode转义字符问题，
确保中文字符正确显示而不是显示为\\uXXXX格式。

使用方法:
    # 检查问题
    python scripts/fix_enum_mapping_encoding.py --check
    
    # 修复问题
    python scripts/fix_enum_mapping_encoding.py --fix
    
    # 验证修复结果
    python scripts/fix_enum_mapping_encoding.py --verify
"""

import sys
import argparse
import json
import re
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from global_const.global_const import pgsql_engine
from utils.create_table import (
    query_decision_analysis_static_configs,
    DecisionAnalysisStaticConfig
)
from utils.loguru_setting import loguru_setting
from loguru import logger
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# 初始化日志
loguru_setting(production=False)


def decode_unicode_escapes(text):
    """
    将Unicode转义序列转换为实际的中文字符
    
    Args:
        text: 包含Unicode转义序列的字符串
        
    Returns:
        解码后的字符串
    """
    if not isinstance(text, str):
        return text
    
    # 使用正则表达式找到所有的Unicode转义序列
    def replace_unicode(match):
        unicode_str = match.group(0)
        try:
            # 将\uXXXX转换为实际字符
            return unicode_str.encode().decode('unicode_escape')
        except Exception as e:
            logger.warning(f"Failed to decode {unicode_str}: {e}")
            return unicode_str
    
    # 匹配\\uXXXX格式的Unicode转义序列
    pattern = r'\\\\u[0-9a-fA-F]{4}'
    return re.sub(pattern, replace_unicode, text)


def fix_enum_mapping_dict(enum_dict):
    """
    修复enum_mapping字典中的Unicode转义字符
    
    Args:
        enum_dict: 包含Unicode转义的字典
        
    Returns:
        修复后的字典
    """
    if not isinstance(enum_dict, dict):
        return enum_dict
    
    fixed_dict = {}
    for key, value in enum_dict.items():
        fixed_key = decode_unicode_escapes(str(key))
        fixed_value = decode_unicode_escapes(str(value))
        fixed_dict[fixed_key] = fixed_value
    
    return fixed_dict


def check_unicode_escape_issues():
    """
    检查数据库中enum_mapping字段的Unicode转义问题
    
    Returns:
        包含问题记录的列表
    """
    try:
        logger.info("Checking for Unicode escape issues in enum_mapping fields...")
        
        # 查询所有包含enum_mapping的记录
        configs = query_decision_analysis_static_configs(limit=1000)
        
        problem_records = []
        total_checked = 0
        
        for config in configs:
            if config.enum_mapping:
                total_checked += 1
                
                # 将enum_mapping转换为JSON字符串来检查
                json_str = json.dumps(config.enum_mapping, ensure_ascii=True)
                
                # 检查是否包含Unicode转义序列
                if '\\\\u' in json_str:
                    problem_records.append({
                        'id': config.id,
                        'room_id': config.room_id,
                        'device_type': config.device_type,
                        'point_alias': config.point_alias,
                        'enum_mapping': config.enum_mapping,
                        'json_escaped': json_str
                    })
        
        logger.info(f"Checked {total_checked} records with enum_mapping")
        logger.info(f"Found {len(problem_records)} records with potential Unicode escape issues")
        
        return problem_records
        
    except Exception as e:
        logger.error(f"Failed to check Unicode escape issues: {e}")
        return []


def fix_unicode_escape_issues():
    """
    修复数据库中enum_mapping字段的Unicode转义问题
    
    Returns:
        修复的记录数量
    """
    try:
        logger.info("Starting to fix Unicode escape issues...")
        
        # 首先检查问题记录
        problem_records = check_unicode_escape_issues()
        
        if not problem_records:
            logger.info("No Unicode escape issues found")
            return 0
        
        logger.info(f"Found {len(problem_records)} records to fix")
        
        # 创建数据库会话
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            fixed_count = 0
            
            for record in problem_records:
                # 获取记录
                config = session.query(DecisionAnalysisStaticConfig).filter_by(
                    id=record['id']
                ).first()
                
                if config and config.enum_mapping:
                    # 修复enum_mapping
                    original_mapping = config.enum_mapping.copy()
                    fixed_mapping = fix_enum_mapping_dict(config.enum_mapping)
                    
                    # 检查是否真的需要修复
                    if fixed_mapping != original_mapping:
                        config.enum_mapping = fixed_mapping
                        fixed_count += 1
                        
                        logger.info(f"Fixed record {config.room_id}-{config.device_type}-{config.point_alias}")
                        logger.info(f"  Before: {original_mapping}")
                        logger.info(f"  After:  {fixed_mapping}")
                        
                        # 每10条记录提交一次
                        if fixed_count % 10 == 0:
                            session.commit()
                            logger.info(f"Committed {fixed_count} fixes so far...")
            
            # 最终提交
            session.commit()
            
            logger.info(f"Successfully fixed {fixed_count} records")
            return fixed_count
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to fix Unicode escape issues: {e}")
        return 0


def verify_fix_results():
    """
    验证修复结果
    
    Returns:
        验证是否成功
    """
    try:
        logger.info("Verifying fix results...")
        
        # 重新检查问题
        problem_records = check_unicode_escape_issues()
        
        if not problem_records:
            logger.info("✅ Verification successful: No Unicode escape issues found")
            return True
        else:
            logger.warning(f"❌ Verification failed: Still found {len(problem_records)} issues")
            
            # 显示剩余问题的详情
            for record in problem_records[:5]:  # 只显示前5个
                logger.warning(f"  - {record['room_id']}-{record['device_type']}-{record['point_alias']}: {record['json_escaped']}")
            
            return False
            
    except Exception as e:
        logger.error(f"Failed to verify fix results: {e}")
        return False


def demonstrate_issue():
    """
    演示Unicode转义问题
    """
    logger.info("Demonstrating Unicode escape issue...")
    
    # 示例数据
    sample_data = {"0": "关闭", "1": "开启", "2": "自动模式"}
    
    print("Original data:", sample_data)
    print("JSON dumps (default):", json.dumps(sample_data))
    print("JSON dumps (ensure_ascii=False):", json.dumps(sample_data, ensure_ascii=False))
    print("JSON dumps (ensure_ascii=True):", json.dumps(sample_data, ensure_ascii=True))
    
    # 演示解码过程
    escaped_json = json.dumps(sample_data, ensure_ascii=True)
    print(f"\nEscaped JSON: {escaped_json}")
    
    # 解析回来
    parsed_data = json.loads(escaped_json)
    print(f"Parsed back: {parsed_data}")
    
    # 使用我们的修复函数
    fixed_data = fix_enum_mapping_dict(parsed_data)
    print(f"After fix: {fixed_data}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Fix Unicode escape issues in enum_mapping fields")
    
    parser.add_argument("--check", action="store_true", help="Check for Unicode escape issues")
    parser.add_argument("--fix", action="store_true", help="Fix Unicode escape issues")
    parser.add_argument("--verify", action="store_true", help="Verify fix results")
    parser.add_argument("--demo", action="store_true", help="Demonstrate the Unicode escape issue")
    
    args = parser.parse_args()
    
    if not any([args.check, args.fix, args.verify, args.demo]):
        # 默认执行检查
        args.check = True
    
    try:
        logger.info("Unicode Escape Fix Tool for enum_mapping")
        logger.info("=" * 50)
        
        success = True
        
        if args.demo:
            logger.info("🎭 Demonstrating Unicode escape issue...")
            demonstrate_issue()
        
        if args.check:
            logger.info("🔍 Checking for Unicode escape issues...")
            problem_records = check_unicode_escape_issues()
            
            if problem_records:
                logger.warning(f"Found {len(problem_records)} records with Unicode escape issues")
                
                # 显示前几个问题记录的详情
                for record in problem_records[:3]:
                    logger.warning(f"Problem record: {record['room_id']}-{record['device_type']}-{record['point_alias']}")
                    logger.warning(f"  Current: {record['enum_mapping']}")
                    logger.warning(f"  JSON:    {record['json_escaped']}")
                
                success = False
            else:
                logger.info("✅ No Unicode escape issues found")
        
        if args.fix:
            logger.info("🔧 Fixing Unicode escape issues...")
            fixed_count = fix_unicode_escape_issues()
            
            if fixed_count > 0:
                logger.info(f"✅ Successfully fixed {fixed_count} records")
            else:
                logger.info("ℹ️  No records needed fixing")
        
        if args.verify:
            logger.info("✅ Verifying fix results...")
            if not verify_fix_results():
                success = False
        
        print("\n" + "="*60)
        print("UNICODE ESCAPE FIX SUMMARY")
        print("="*60)
        
        if success:
            print("✅ All operations completed successfully")
            print("enum_mapping fields should display Chinese characters correctly")
        else:
            print("❌ Some issues were detected")
            print("Recommendations:")
            print("1. Run with --fix to fix Unicode escape issues")
            print("2. Run with --verify to check fix results")
            print("3. Check database client encoding settings")
        
        print("="*60)
        
        if not success:
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Unicode escape fix failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()