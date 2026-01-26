"""
数据库表结构迁移脚本
用于更新 mushroom_embedding 表结构：
1. 删除字段：file_name, full_text_description, growth_stage
2. 新增字段：image_quality_score
3. 更新索引：将 idx_room_stage 改为 idx_room_growth_day
"""

from loguru import logger
from sqlalchemy import text
from global_const.global_const import pgsql_engine


def migrate_table_structure():
    """执行表结构迁移"""
    
    logger.info("开始数据库表结构迁移...")
    
    with pgsql_engine.connect() as conn:
        try:
            # 1. 检查表是否存在
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'mushroom_embedding'
                );
            """))
            
            if not result.scalar():
                logger.warning("表 mushroom_embedding 不存在，跳过迁移")
                return
            
            logger.info("✓ 表 mushroom_embedding 存在")
            
            # 2. 检查并删除旧索引 idx_room_stage
            logger.info("检查并删除旧索引 idx_room_stage...")
            conn.execute(text("""
                DROP INDEX IF EXISTS idx_room_stage;
            """))
            logger.info("✓ 旧索引 idx_room_stage 已删除（如果存在）")
            
            # 3. 创建新索引 idx_room_growth_day
            logger.info("创建新索引 idx_room_growth_day...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_room_growth_day 
                ON mushroom_embedding (room_id, growth_day);
            """))
            logger.info("✓ 新索引 idx_room_growth_day 已创建")
            
            # 4. 检查并删除字段 file_name
            logger.info("检查并删除字段 file_name...")
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding' 
                AND column_name = 'file_name';
            """))
            
            if result.scalar():
                conn.execute(text("""
                    ALTER TABLE mushroom_embedding 
                    DROP COLUMN IF EXISTS file_name;
                """))
                logger.info("✓ 字段 file_name 已删除")
            else:
                logger.info("✓ 字段 file_name 不存在，跳过删除")
            
            # 5. 检查并删除字段 full_text_description
            logger.info("检查并删除字段 full_text_description...")
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding' 
                AND column_name = 'full_text_description';
            """))
            
            if result.scalar():
                conn.execute(text("""
                    ALTER TABLE mushroom_embedding 
                    DROP COLUMN IF EXISTS full_text_description;
                """))
                logger.info("✓ 字段 full_text_description 已删除")
            else:
                logger.info("✓ 字段 full_text_description 不存在，跳过删除")
            
            # 6. 检查并删除字段 growth_stage
            logger.info("检查并删除字段 growth_stage...")
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding' 
                AND column_name = 'growth_stage';
            """))
            
            if result.scalar():
                conn.execute(text("""
                    ALTER TABLE mushroom_embedding 
                    DROP COLUMN IF EXISTS growth_stage;
                """))
                logger.info("✓ 字段 growth_stage 已删除")
            else:
                logger.info("✓ 字段 growth_stage 不存在，跳过删除")
            
            # 7. 检查并添加字段 image_quality_score
            logger.info("检查并添加字段 image_quality_score...")
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding' 
                AND column_name = 'image_quality_score';
            """))
            
            if not result.scalar():
                conn.execute(text("""
                    ALTER TABLE mushroom_embedding 
                    ADD COLUMN image_quality_score FLOAT;
                """))
                conn.execute(text("""
                    COMMENT ON COLUMN mushroom_embedding.image_quality_score 
                    IS '图像质量评分 (0-100)';
                """))
                logger.info("✓ 字段 image_quality_score 已添加")
            else:
                logger.info("✓ 字段 image_quality_score 已存在，跳过添加")
            
            # 8. 提交事务
            conn.commit()
            
            logger.info("=" * 60)
            logger.info("✅ 数据库表结构迁移完成！")
            logger.info("=" * 60)
            
            # 9. 显示迁移后的表结构
            logger.info("\n当前表结构：")
            result = conn.execute(text("""
                SELECT 
                    column_name, 
                    data_type, 
                    is_nullable,
                    column_default
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'mushroom_embedding'
                ORDER BY ordinal_position;
            """))
            
            for row in result:
                logger.info(f"  - {row.column_name}: {row.data_type} "
                          f"(nullable: {row.is_nullable})")
            
            # 10. 显示索引信息
            logger.info("\n当前索引：")
            result = conn.execute(text("""
                SELECT indexname, indexdef 
                FROM pg_indexes 
                WHERE tablename = 'mushroom_embedding'
                ORDER BY indexname;
            """))
            
            for row in result:
                logger.info(f"  - {row.indexname}")
            
        except Exception as e:
            logger.error(f"❌ 迁移失败: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    migrate_table_structure()
