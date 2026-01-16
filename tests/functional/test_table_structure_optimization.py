"""
测试表结构优化后的系统功能
验证：
1. 表结构是否正确
2. 数据插入是否正常
3. 图像质量评分是否计算
4. 索引是否生效
"""

from loguru import logger
from sqlalchemy import text, inspect
from global_const.global_const import pgsql_engine


def test_table_structure():
    """测试表结构"""
    logger.info("=" * 60)
    logger.info("测试 1: 验证表结构")
    logger.info("=" * 60)
    
    with pgsql_engine.connect() as conn:
        # 获取表结构
        result = conn.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = 'mushroom_embedding'
            ORDER BY ordinal_position;
        """))
        
        columns = {row.column_name: row for row in result}
        
        # 验证必需字段存在
        required_fields = [
            'id', 'collection_datetime', 'image_path', 'room_id', 
            'in_date', 'growth_day', 'image_quality_score',
            'air_cooler_config', 'fresh_fan_config', 'light_count',
            'light_config', 'humidifier_count', 'humidifier_config',
            'env_sensor_status', 'semantic_description', 'embedding',
            'llama_description'
        ]
        
        missing_fields = []
        for field in required_fields:
            if field not in columns:
                missing_fields.append(field)
        
        if missing_fields:
            logger.error(f"❌ 缺少字段: {missing_fields}")
            return False
        else:
            logger.info("✅ 所有必需字段都存在")
        
        # 验证已删除的字段不存在
        deleted_fields = ['file_name', 'full_text_description', 'growth_stage']
        found_deleted = []
        for field in deleted_fields:
            if field in columns:
                found_deleted.append(field)
        
        if found_deleted:
            logger.error(f"❌ 发现应该删除的字段: {found_deleted}")
            return False
        else:
            logger.info("✅ 已删除的字段确认不存在")
        
        # 验证 image_quality_score 字段类型
        quality_field = columns.get('image_quality_score')
        if quality_field:
            logger.info(f"✅ image_quality_score 字段类型: {quality_field.data_type}, nullable: {quality_field.is_nullable}")
        
        return True


def test_indexes():
    """测试索引"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 2: 验证索引")
    logger.info("=" * 60)
    
    with pgsql_engine.connect() as conn:
        result = conn.execute(text("""
            SELECT indexname 
            FROM pg_indexes 
            WHERE tablename = 'mushroom_embedding'
            ORDER BY indexname;
        """))
        
        indexes = [row.indexname for row in result]
        
        # 验证必需索引存在
        required_indexes = [
            'idx_room_growth_day',
            'idx_collection_time',
            'idx_in_date',
            'idx_image_quality',
            'uq_image_path'
        ]
        
        missing_indexes = []
        for idx in required_indexes:
            if idx not in indexes:
                missing_indexes.append(idx)
        
        if missing_indexes:
            logger.error(f"❌ 缺少索引: {missing_indexes}")
            return False
        else:
            logger.info("✅ 所有必需索引都存在")
        
        # 验证旧索引已删除
        if 'idx_room_stage' in indexes:
            logger.error("❌ 旧索引 idx_room_stage 仍然存在")
            return False
        else:
            logger.info("✅ 旧索引 idx_room_stage 已删除")
        
        logger.info(f"\n当前索引列表:")
        for idx in indexes:
            logger.info(f"  - {idx}")
        
        return True


def test_data_query():
    """测试数据查询"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 3: 验证数据查询")
    logger.info("=" * 60)
    
    with pgsql_engine.connect() as conn:
        # 查询总记录数
        result = conn.execute(text("""
            SELECT COUNT(*) as total FROM mushroom_embedding;
        """))
        total = result.scalar()
        logger.info(f"✅ 总记录数: {total}")
        
        # 查询有质量评分的记录数
        result = conn.execute(text("""
            SELECT COUNT(*) as with_quality 
            FROM mushroom_embedding 
            WHERE image_quality_score IS NOT NULL;
        """))
        with_quality = result.scalar()
        logger.info(f"✅ 有质量评分的记录数: {with_quality}")
        
        if total > 0:
            # 查询质量评分统计
            result = conn.execute(text("""
                SELECT 
                    MIN(image_quality_score) as min_score,
                    MAX(image_quality_score) as max_score,
                    AVG(image_quality_score) as avg_score
                FROM mushroom_embedding 
                WHERE image_quality_score IS NOT NULL;
            """))
            
            row = result.fetchone()
            if row and row.min_score is not None:
                logger.info(f"✅ 质量评分范围: {row.min_score:.2f} - {row.max_score:.2f}")
                logger.info(f"✅ 平均质量评分: {row.avg_score:.2f}")
            
            # 查询质量分布
            result = conn.execute(text("""
                SELECT 
                    CASE 
                        WHEN image_quality_score >= 80 THEN '优秀(80-100)'
                        WHEN image_quality_score >= 60 THEN '良好(60-80)'
                        WHEN image_quality_score >= 40 THEN '一般(40-60)'
                        WHEN image_quality_score >= 0 THEN '较差(0-40)'
                        ELSE '未评分'
                    END AS quality_level,
                    COUNT(*) as count
                FROM mushroom_embedding
                GROUP BY quality_level
                ORDER BY quality_level;
            """))
            
            logger.info("\n质量分布:")
            for row in result:
                logger.info(f"  {row.quality_level}: {row.count} 条")
        
        return True


def test_field_access():
    """测试字段访问"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 4: 验证字段访问")
    logger.info("=" * 60)
    
    with pgsql_engine.connect() as conn:
        # 尝试查询一条记录的所有字段
        result = conn.execute(text("""
            SELECT 
                id, room_id, growth_day, image_quality_score,
                semantic_description, llama_description
            FROM mushroom_embedding 
            LIMIT 1;
        """))
        
        row = result.fetchone()
        if row:
            logger.info("✅ 成功查询记录字段:")
            logger.info(f"  - room_id: {row.room_id}")
            logger.info(f"  - growth_day: {row.growth_day}")
            logger.info(f"  - image_quality_score: {row.image_quality_score}")
            logger.info(f"  - semantic_description: {row.semantic_description[:50]}...")
            logger.info(f"  - llama_description: {row.llama_description[:50] if row.llama_description else 'N/A'}...")
        else:
            logger.info("⚠️ 表中暂无数据")
        
        return True


def main():
    """运行所有测试"""
    logger.info("开始测试表结构优化...")
    
    try:
        # 测试 1: 表结构
        if not test_table_structure():
            logger.error("❌ 表结构测试失败")
            return False
        
        # 测试 2: 索引
        if not test_indexes():
            logger.error("❌ 索引测试失败")
            return False
        
        # 测试 3: 数据查询
        if not test_data_query():
            logger.error("❌ 数据查询测试失败")
            return False
        
        # 测试 4: 字段访问
        if not test_field_access():
            logger.error("❌ 字段访问测试失败")
            return False
        
        logger.info("\n" + "=" * 60)
        logger.info("✅ 所有测试通过！表结构优化成功！")
        logger.info("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
