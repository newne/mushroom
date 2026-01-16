#!/usr/bin/env python3
"""
数据库迁移脚本：为mushroom_embedding表添加LLaMA描述字段
"""

import sys
from pathlib import Path

# 添加src目录到路径
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

from sqlalchemy import text
from loguru import logger
from global_const.global_const import pgsql_engine


def add_llama_description_fields():
    """为mushroom_embedding表添加LLaMA描述字段"""
    
    # 要添加的字段
    fields_to_add = [
        {
            'name': 'llama_description',
            'definition': 'TEXT',
            'comment': 'LLaMA生成的蘑菇生长情况描述'
        },
        {
            'name': 'full_text_description', 
            'definition': 'TEXT',
            'comment': '完整文本描述（身份元数据 + LLaMA描述）'
        }
    ]
    
    try:
        with pgsql_engine.connect() as conn:
            # 检查表是否存在
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding'
            );
            """
            
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                logger.warning("表 mushroom_embedding 不存在，请先运行 create_tables()")
                return False
            
            logger.info("开始为 mushroom_embedding 表添加 LLaMA 描述字段...")
            
            # 为每个字段检查是否已存在，如果不存在则添加
            for field in fields_to_add:
                # 检查字段是否已存在
                check_column_sql = """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = 'mushroom_embedding'
                    AND column_name = :column_name
                );
                """
                
                result = conn.execute(text(check_column_sql), {'column_name': field['name']})
                column_exists = result.scalar()
                
                if column_exists:
                    logger.info(f"字段 {field['name']} 已存在，跳过")
                    continue
                
                # 添加字段
                add_column_sql = f"""
                ALTER TABLE mushroom_embedding 
                ADD COLUMN {field['name']} {field['definition']};
                """
                
                conn.execute(text(add_column_sql))
                logger.info(f"✅ 成功添加字段: {field['name']}")
                
                # 添加字段注释
                comment_sql = f"""
                COMMENT ON COLUMN mushroom_embedding.{field['name']} 
                IS '{field['comment']}';
                """
                
                conn.execute(text(comment_sql))
                logger.info(f"✅ 成功添加字段注释: {field['name']}")
            
            # 提交事务
            conn.commit()
            logger.info("🎉 所有字段添加完成！")
            
            # 显示表结构
            show_columns_sql = """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = 'mushroom_embedding'
            AND column_name IN ('llama_description', 'full_text_description')
            ORDER BY ordinal_position;
            """
            
            result = conn.execute(text(show_columns_sql))
            columns = result.fetchall()
            
            if columns:
                logger.info("📋 新添加的字段信息:")
                for col in columns:
                    logger.info(f"   {col.column_name}: {col.data_type}, nullable={col.is_nullable}")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ 添加字段失败: {e}")
        return False


def verify_fields():
    """验证字段是否添加成功"""
    try:
        with pgsql_engine.connect() as conn:
            # 查询表结构
            sql = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = 'mushroom_embedding'
            ORDER BY ordinal_position;
            """
            
            result = conn.execute(text(sql))
            columns = result.fetchall()
            
            logger.info("📋 mushroom_embedding 表当前字段:")
            for col in columns:
                logger.info(f"   {col.column_name}: {col.data_type} ({'NULL' if col.is_nullable == 'YES' else 'NOT NULL'})")
            
            # 检查新字段是否存在
            new_fields = ['llama_description', 'full_text_description']
            existing_fields = [col.column_name for col in columns]
            
            for field in new_fields:
                if field in existing_fields:
                    logger.info(f"✅ 字段 {field} 存在")
                else:
                    logger.error(f"❌ 字段 {field} 不存在")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ 验证字段失败: {e}")
        return False


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("数据库迁移：添加 LLaMA 描述字段")
    logger.info("=" * 60)
    
    try:
        # 添加字段
        success = add_llama_description_fields()
        
        if success:
            logger.info("\n🔍 验证字段添加结果...")
            verify_fields()
            logger.info("\n✅ 数据库迁移完成！")
        else:
            logger.error("\n❌ 数据库迁移失败！")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ 迁移过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()