#!/usr/bin/env python3
"""
清空并重新导入静态配置表

该脚本用于清空decision_analysis_static_config表并重新导入数据，
确保enum_mapping中的中文字符正常显示。

使用方法:
    python scripts/clear_and_reimport_static_config.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import (
    DecisionAnalysisStaticConfig,
    query_decision_analysis_static_configs
)
from utils.loguru_setting import loguru_setting
from global_const.global_const import pgsql_engine
from loguru import logger
from sqlalchemy.orm import sessionmaker

# 初始化日志
loguru_setting(production=False)


def clear_static_config_table():
    """清空静态配置表"""
    try:
        logger.info("Clearing decision_analysis_static_config table...")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 获取清空前的记录数
            count_before = session.query(DecisionAnalysisStaticConfig).count()
            logger.info(f"Records before clearing: {count_before}")
            
            # 清空表
            session.query(DecisionAnalysisStaticConfig).delete()
            session.commit()
            
            # 验证清空结果
            count_after = session.query(DecisionAnalysisStaticConfig).count()
            logger.info(f"Records after clearing: {count_after}")
            
            if count_after == 0:
                logger.info("✅ Table cleared successfully")
                return True
            else:
                logger.error(f"❌ Table clearing failed, {count_after} records remain")
                return False
                
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to clear static config table: {e}")
        raise


def import_static_config_from_file():
    """从配置文件导入静态配置"""
    try:
        logger.info("Importing static config from file...")
        
        # 读取静态配置文件
        config_file = Path(__file__).parent.parent / "src" / "configs" / "static_config.json"
        
        if not config_file.exists():
            logger.error(f"Static config file not found: {config_file}")
            return False
        
        with open(config_file, 'r', encoding='utf-8') as f:
            static_config = json.load(f)
        
        logger.info(f"Loaded static config from: {config_file}")
        
        # 获取文件修改时间作为版本信息
        file_mtime = datetime.fromtimestamp(config_file.stat().st_mtime)
        
        # 计算配置内容哈希
        import hashlib
        config_content = json.dumps(static_config, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.md5(config_content.encode('utf-8')).hexdigest()[:8]
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            imported_count = 0
            
            # 解析新的配置文件结构
            mushroom_config = static_config.get("mushroom", {})
            rooms = mushroom_config.get("rooms", {})
            datapoint = mushroom_config.get("datapoint", {})
            
            # 遍历设备类型
            for device_type, device_config in datapoint.items():
                if device_type == "remark" or not isinstance(device_config, dict):
                    continue
                
                device_list = device_config.get("device_list", [])
                point_list = device_config.get("point_list", [])
                
                logger.info(f"Processing device type: {device_type}")
                
                # 遍历设备列表
                for device_info in device_list:
                    device_name = device_info.get("device_name", "")
                    device_alias = device_info.get("device_alias", "")
                    device_remark = device_info.get("remark", "")
                    
                    # 从device_alias中提取房间号
                    room_id = None
                    for room in rooms.keys():
                        if device_alias.endswith(f"_{room}"):
                            room_id = room
                            break
                    
                    if not room_id:
                        logger.warning(f"Could not determine room_id for device: {device_alias}")
                        continue
                    
                    # 遍历点位列表
                    for point_info in point_list:
                        point_name = point_info.get("point_name", "")
                        point_alias = point_info.get("point_alias", "")
                        point_remark = point_info.get("remark", "")
                        
                        # 处理枚举映射
                        enum_mapping = None
                        if "enum" in point_info:
                            enum_mapping = point_info["enum"]
                        elif "enmum" in point_info:  # 处理拼写错误
                            enum_mapping = point_info["enmum"]
                        
                        # 确定变更类型
                        if enum_mapping:
                            change_type = "enum_state"
                        else:
                            change_type = "analog_value"
                        
                        # 创建静态配置记录
                        static_record = DecisionAnalysisStaticConfig(
                            room_id=str(room_id),
                            device_type=device_type,
                            device_name=device_name,
                            device_alias=device_alias,
                            point_alias=point_alias,
                            point_name=point_name,
                            remark=point_remark,
                            change_type=change_type,
                            threshold=None,
                            enum_mapping=enum_mapping,  # 直接存储字典，SQLAlchemy会自动处理JSON序列化
                            config_version=1,
                            is_active=True,
                            effective_time=file_mtime,
                            source="static_config_import",
                            operator="system",
                            comment=f"Imported from static_config.json (hash: {content_hash})"
                        )
                        
                        session.add(static_record)
                        imported_count += 1
                        
                        if imported_count % 50 == 0:
                            logger.info(f"Processed {imported_count} records...")
            
            # 提交事务
            session.commit()
            logger.info(f"✅ Successfully imported {imported_count} static config records")
            
            return imported_count
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to import static config: {e}")
        raise


def verify_chinese_display():
    """验证中文字符显示"""
    try:
        logger.info("Verifying Chinese character display...")
        
        # 查询包含enum_mapping的记录
        configs = query_decision_analysis_static_configs(limit=10)
        enum_configs = [config for config in configs if config.enum_mapping]
        
        if not enum_configs:
            logger.warning("No records with enum_mapping found")
            return False
        
        print("\n" + "="*80)
        print("中文字符显示验证")
        print("="*80)
        
        chinese_count = 0
        total_enum_count = len(enum_configs)
        
        for i, config in enumerate(enum_configs[:5], 1):  # 只显示前5个
            print(f"\n记录 {i}:")
            print(f"  房间: {config.room_id}")
            print(f"  设备: {config.device_type} ({config.device_alias})")
            print(f"  点位: {config.point_alias} ({config.point_name})")
            print(f"  备注: {config.remark}")
            print(f"  枚举映射: {config.enum_mapping}")
            print(f"  数据类型: {type(config.enum_mapping)}")
            
            # 检查是否包含中文
            if config.enum_mapping:
                enum_str = str(config.enum_mapping)
                if any('\u4e00' <= char <= '\u9fff' for char in enum_str):
                    chinese_count += 1
                    print("  ✅ 包含中文字符")
                else:
                    print("  ⚠️  未发现中文字符")
            
            print("-" * 60)
        
        print(f"\n总结:")
        print(f"  包含enum_mapping的记录: {total_enum_count}")
        print(f"  包含中文字符的记录: {chinese_count}")
        print(f"  中文覆盖率: {chinese_count/total_enum_count*100:.1f}%")
        
        if chinese_count > 0:
            print("  ✅ 中文字符显示正常")
            return True
        else:
            print("  ❌ 中文字符显示异常")
            return False
        
    except Exception as e:
        logger.error(f"Failed to verify Chinese display: {e}")
        return False


def main():
    """主函数"""
    try:
        logger.info("Starting clear and reimport process...")
        logger.info("=" * 60)
        
        # 1. 清空表
        print("步骤 1: 清空静态配置表...")
        if not clear_static_config_table():
            print("❌ 清空表失败")
            return False
        print("✅ 表清空成功")
        
        # 2. 重新导入
        print("\n步骤 2: 重新导入静态配置...")
        imported_count = import_static_config_from_file()
        if imported_count <= 0:
            print("❌ 导入失败")
            return False
        print(f"✅ 成功导入 {imported_count} 条记录")
        
        # 3. 验证中文显示
        print("\n步骤 3: 验证中文字符显示...")
        if verify_chinese_display():
            print("✅ 中文字符显示验证通过")
        else:
            print("⚠️  中文字符显示需要检查")
        
        print("\n" + "="*60)
        print("清空并重新导入完成！")
        print("="*60)
        
        # 4. 提供后续验证建议
        print("\n💡 后续验证建议:")
        print("1. 使用查看工具验证: python scripts/view_enum_mapping.py --limit 5")
        print("2. 导出CSV验证: python scripts/export_csv_utf8.py --table static --output test.csv --check")
        print("3. 在DBeaver中查看数据表")
        
        return True
        
    except Exception as e:
        logger.error(f"Clear and reimport process failed: {e}")
        print(f"❌ 处理失败: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)