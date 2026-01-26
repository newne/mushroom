"""
添加图像质量索引脚本
为 mushroom_embedding 表添加 image_quality_score 索引
"""

from loguru import logger
from sqlalchemy import text
from global_const.global_const import pgsql_engine


def add_image_quality_index():
    """添加图像质量索引"""
    
    logger.info("开始添加图像质量索引...")
    
    with pgsql_engine.connect() as conn:
        try:
            # 检查表是否存在
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'mushroom_embedding'
                );
            """))
            
            if not result.scalar():
                logger.warning("表 mushroom_embedding 不存在，跳过索引创建")
                return
            
            logger.info("✓ 表 mushroom_embedding 存在")
            
            # 检查索引是否已存在
            logger.info("检查索引 idx_image_quality 是否存在...")
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM pg_indexes 
                    WHERE tablename = 'mushroom_embedding' 
                    AND indexname = 'idx_image_quality'
                );
            """))
            
            if result.scalar():
                logger.info("✓ 索引 idx_image_quality 已存在，跳过创建")
                return
            
            # 创建图像质量索引
            logger.info("创建索引 idx_image_quality...")
            conn.execute(text("""
                CREATE INDEX idx_image_quality 
                ON mushroom_embedding (image_quality_score);
            """))
            
            # 提交事务
            conn.commit()
            
            logger.info("=" * 60)
            logger.info("✅ 图像质量索引创建完成！")
            logger.info("=" * 60)
            
            # 显示索引信息
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
            logger.error(f"❌ 索引创建失败: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    add_image_quality_index()
