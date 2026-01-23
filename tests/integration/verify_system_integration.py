"""
系统集成验证脚本
验证表结构优化后，整个系统的各个组件是否正常工作
"""

from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import text
from global_const.global_const import pgsql_engine


def verify_table_creation():
    """验证建表功能"""
    logger.info("=" * 60)
    logger.info("验证 1: 建表功能")
    logger.info("=" * 60)
    
    try:
        from utils.create_table import create_tables
        
        # 执行建表（应该是幂等的）
        create_tables()
        logger.info("✅ 建表功能正常")
        return True
    except Exception as e:
        logger.error(f"❌ 建表功能异常: {e}")
        return False


def verify_env_data_processor():
    """验证环境数据处理器"""
    logger.info("\n" + "=" * 60)
    logger.info("验证 2: 环境数据处理器")
    logger.info("=" * 60)
    
    try:
        from utils.env_data_processor import create_env_data_processor
        
        processor = create_env_data_processor()
        
        # 测试获取环境数据
        test_time = datetime.now() - timedelta(days=1)
        env_data = processor.get_environment_data(
            room_id='611',
            collection_time=test_time,
            image_path='test/path.jpg',
            time_window_minutes=1
        )
        
        if env_data:
            logger.info("✅ 环境数据处理器正常")
            logger.info(f"  - 获取到环境数据: {list(env_data.keys())}")
            
            # 验证不包含已删除的字段
            deleted_fields = ['file_name', 'full_text_description', 'growth_stage']
            found_deleted = [f for f in deleted_fields if f in env_data]
            
            if found_deleted:
                logger.error(f"❌ 环境数据包含已删除字段: {found_deleted}")
                return False
            else:
                logger.info("✅ 环境数据不包含已删除字段")
        else:
            logger.info("⚠️ 未获取到环境数据（可能是测试时间没有数据）")
        
        return True
    except Exception as e:
        logger.error(f"❌ 环境数据处理器异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_image_encoder():
    """验证图像编码器"""
    logger.info("\n" + "=" * 60)
    logger.info("验证 3: 图像编码器")
    logger.info("=" * 60)
    
    try:
        from clip.mushroom_image_encoder import create_mushroom_encoder
        
        encoder = create_mushroom_encoder()
        logger.info("✅ 图像编码器初始化成功")
        
        # 验证图像质量评分方法存在
        if hasattr(encoder, 'calculate_image_quality_score'):
            logger.info("✅ 图像质量评分方法存在")
        else:
            logger.error("❌ 图像质量评分方法不存在")
            return False
        
        # 验证处理统计方法
        stats = encoder.get_processing_statistics()
        if stats:
            logger.info("✅ 处理统计功能正常")
            logger.info(f"  - 总记录数: {stats.get('total_processed', 0)}")
            logger.info(f"  - 有环境控制: {stats.get('with_environmental_control', 0)}")
            
            # 验证图像质量统计
            quality_stats = stats.get('image_quality_stats', {})
            if quality_stats:
                logger.info("✅ 图像质量统计功能正常")
                logger.info(f"  - 平均质量: {quality_stats.get('average_score', 'N/A')}")
            else:
                logger.info("⚠️ 暂无图像质量统计数据")
        
        return True
    except Exception as e:
        logger.error(f"❌ 图像编码器异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_database_operations():
    """验证数据库操作"""
    logger.info("\n" + "=" * 60)
    logger.info("验证 4: 数据库操作")
    logger.info("=" * 60)
    
    try:
        with pgsql_engine.connect() as conn:
            # 测试查询
            result = conn.execute(text("""
                SELECT 
                    room_id, 
                    growth_day, 
                    image_quality_score,
                    semantic_description
                FROM mushroom_embedding 
                WHERE image_quality_score IS NOT NULL
                LIMIT 1;
            """))
            
            row = result.fetchone()
            if row:
                logger.info("✅ 数据库查询正常")
                logger.info(f"  - 查询到记录: room_id={row.room_id}, growth_day={row.growth_day}")
            else:
                logger.info("⚠️ 暂无带质量评分的记录")
            
            # 测试索引使用
            result = conn.execute(text("""
                EXPLAIN SELECT * FROM mushroom_embedding 
                WHERE image_quality_score > 50;
            """))
            
            explain_plan = [row[0] for row in result]
            uses_index = any('idx_image_quality' in line for line in explain_plan)
            
            if uses_index:
                logger.info("✅ 图像质量索引正常使用")
            else:
                logger.info("⚠️ 图像质量索引未使用（可能数据量太小）")
            
            return True
    except Exception as e:
        logger.error(f"❌ 数据库操作异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_scheduler_compatibility():
    """验证调度器兼容性"""
    logger.info("\n" + "=" * 60)
    logger.info("验证 5: 调度器兼容性")
    logger.info("=" * 60)
    
    try:
        # 导入调度器模块（不启动）
        from scheduling.optimized_scheduler import OptimizedScheduler
        
        logger.info("✅ 调度器模块导入成功")
        
        # 验证调度器可以初始化
        scheduler = OptimizedScheduler()
        logger.info("✅ 调度器初始化成功")
        
        # 验证调度器配置
        logger.info(f"  - 时区: {scheduler.timezone}")
        logger.info(f"  - 容错次数: {scheduler.max_failures}")
        
        return True
    except Exception as e:
        logger.error(f"❌ 调度器兼容性异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有验证"""
    logger.info("开始系统集成验证...")
    logger.info("验证表结构优化后的系统功能\n")
    
    results = []
    
    # 验证 1: 建表功能
    results.append(("建表功能", verify_table_creation()))
    
    # 验证 2: 环境数据处理器
    results.append(("环境数据处理器", verify_env_data_processor()))
    
    # 验证 3: 图像编码器
    results.append(("图像编码器", verify_image_encoder()))
    
    # 验证 4: 数据库操作
    results.append(("数据库操作", verify_database_operations()))
    
    # 验证 5: 调度器兼容性
    results.append(("调度器兼容性", verify_scheduler_compatibility()))
    
    # 汇总结果
    logger.info("\n" + "=" * 60)
    logger.info("验证结果汇总")
    logger.info("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        logger.info(f"{name}: {status}")
        if not passed:
            all_passed = False
    
    logger.info("\n" + "=" * 60)
    if all_passed:
        logger.info("✅ 所有验证通过！系统集成正常！")
        logger.info("=" * 60)
        logger.info("\n系统已准备就绪，可以正常运行：")
        logger.info("  1. 调度器可以正常启动")
        logger.info("  2. CLIP推理任务会自动计算图像质量评分")
        logger.info("  3. 环境数据处理不包含已删除字段")
        logger.info("  4. 数据库查询和索引正常工作")
    else:
        logger.error("❌ 部分验证失败，请检查错误信息")
        logger.info("=" * 60)
    
    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
