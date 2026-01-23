#!/usr/bin/env python3
"""
Collection IP Field Migration Script

This script adds the collection_ip field to the mushroom_embedding table
and populates it with data extracted from image_path.

Usage:
    python scripts/migration/add_collection_ip_field_migration.py [--dry-run] [--batch-size 1000]
"""

import sys
import argparse
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from global_const.global_const import pgsql_engine
from utils.create_table import MushroomImageEmbedding


def check_field_exists():
    """
    检查collection_ip字段是否已存在
    
    Returns:
        bool: 字段是否存在
    """
    try:
        with pgsql_engine.connect() as conn:
            check_sql = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'mushroom_embedding' 
                AND column_name = 'collection_ip'
            """)
            result = conn.execute(check_sql).fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error checking field existence: {e}")
        return False


def add_collection_ip_field_safe():
    """
    安全地添加collection_ip字段
    
    Returns:
        bool: 是否成功添加字段
    """
    try:
        if check_field_exists():
            logger.info("collection_ip field already exists")
            return True
            
        logger.info("Adding collection_ip field to mushroom_embedding table...")
        
        with pgsql_engine.connect() as conn:
            # 添加字段
            add_column_sql = text("""
                ALTER TABLE mushroom_embedding 
                ADD COLUMN collection_ip VARCHAR(15) NULL
            """)
            conn.execute(add_column_sql)
            
            # 添加注释
            add_comment_sql = text("""
                COMMENT ON COLUMN mushroom_embedding.collection_ip 
                IS '图像采集设备IP地址，从image_path自动解析'
            """)
            conn.execute(add_comment_sql)
            
            conn.commit()
            logger.info("Successfully added collection_ip field")
            
        # 创建索引（分别执行以避免锁定问题）
        logger.info("Creating indexes for collection_ip field...")
        
        with pgsql_engine.connect() as conn:
            # 创建单字段索引
            create_ip_index_sql = text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_collection_ip 
                ON mushroom_embedding (collection_ip)
            """)
            conn.execute(create_ip_index_sql)
            conn.commit()
            
        with pgsql_engine.connect() as conn:
            # 创建复合索引
            create_room_ip_index_sql = text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_room_collection_ip 
                ON mushroom_embedding (room_id, collection_ip)
            """)
            conn.execute(create_room_ip_index_sql)
            conn.commit()
            
        logger.info("Successfully created indexes")
        return True
        
    except Exception as e:
        logger.error(f"Failed to add collection_ip field: {e}")
        return False


def update_collection_ip_batch(batch_size=1000, dry_run=False):
    """
    批量更新collection_ip字段
    
    Args:
        batch_size: 批处理大小
        dry_run: 是否为试运行模式
        
    Returns:
        dict: 更新统计信息
    """
    logger.info(f"Starting batch update of collection_ip field (batch_size={batch_size}, dry_run={dry_run})")
    
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()
    
    stats = {
        'total_processed': 0,
        'successfully_updated': 0,
        'failed_extractions': 0,
        'already_populated': 0,
        'batches_processed': 0
    }
    
    try:
        # 获取需要更新的记录总数
        total_count = session.query(MushroomImageEmbedding).filter(
            MushroomImageEmbedding.collection_ip.is_(None)
        ).count()
        
        logger.info(f"Found {total_count} records to update")
        
        if total_count == 0:
            logger.info("No records need updating")
            return stats
        
        # 分批处理
        offset = 0
        
        while offset < total_count:
            batch_start = time.time()
            
            # 获取当前批次的记录
            records = session.query(MushroomImageEmbedding).filter(
                MushroomImageEmbedding.collection_ip.is_(None)
            ).offset(offset).limit(batch_size).all()
            
            if not records:
                break
            
            batch_updated = 0
            batch_failed = 0
            
            for record in records:
                try:
                    # 提取IP地址
                    ip_address = MushroomImageEmbedding.extract_collection_ip_from_path(record.image_path)
                    
                    if ip_address:
                        if not dry_run:
                            record.collection_ip = ip_address
                        batch_updated += 1
                        stats['successfully_updated'] += 1
                    else:
                        batch_failed += 1
                        stats['failed_extractions'] += 1
                        logger.debug(f"Could not extract IP from: {record.image_path}")
                        
                except Exception as e:
                    batch_failed += 1
                    stats['failed_extractions'] += 1
                    logger.warning(f"Error processing record {record.id}: {e}")
            
            # 提交当前批次
            if not dry_run and batch_updated > 0:
                try:
                    session.commit()
                except Exception as e:
                    logger.error(f"Failed to commit batch: {e}")
                    session.rollback()
                    break
            
            stats['total_processed'] += len(records)
            stats['batches_processed'] += 1
            offset += batch_size
            
            batch_time = time.time() - batch_start
            
            logger.info(
                f"Batch {stats['batches_processed']}: "
                f"processed {len(records)} records, "
                f"updated {batch_updated}, "
                f"failed {batch_failed}, "
                f"time: {batch_time:.2f}s"
            )
            
            # 进度报告
            progress = (stats['total_processed'] / total_count) * 100
            logger.info(f"Progress: {progress:.1f}% ({stats['total_processed']}/{total_count})")
            
            # 短暂休息以避免数据库过载
            if not dry_run:
                time.sleep(0.1)
        
        logger.info(f"Batch update completed:")
        logger.info(f"  - Total processed: {stats['total_processed']}")
        logger.info(f"  - Successfully updated: {stats['successfully_updated']}")
        logger.info(f"  - Failed extractions: {stats['failed_extractions']}")
        logger.info(f"  - Batches processed: {stats['batches_processed']}")
        
        return stats
        
    except Exception as e:
        logger.error(f"Batch update failed: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def validate_migration():
    """
    验证迁移结果
    
    Returns:
        dict: 验证统计信息
    """
    logger.info("Validating migration results...")
    
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()
    
    try:
        # 统计信息
        total_records = session.query(MushroomImageEmbedding).count()
        records_with_ip = session.query(MushroomImageEmbedding).filter(
            MushroomImageEmbedding.collection_ip.isnot(None)
        ).count()
        records_without_ip = total_records - records_with_ip
        
        # 获取IP分布
        ip_counts = session.query(
            MushroomImageEmbedding.collection_ip,
            func.count(MushroomImageEmbedding.id).label('count')
        ).filter(
            MushroomImageEmbedding.collection_ip.isnot(None)
        ).group_by(MushroomImageEmbedding.collection_ip).limit(10).all()
        
        validation_result = {
            'total_records': total_records,
            'records_with_ip': records_with_ip,
            'records_without_ip': records_without_ip,
            'coverage_percentage': (records_with_ip / total_records * 100) if total_records > 0 else 0,
            'top_ips': [(ip, count) for ip, count in ip_counts]
        }
        
        logger.info(f"Validation Results:")
        logger.info(f"  - Total records: {validation_result['total_records']}")
        logger.info(f"  - Records with IP: {validation_result['records_with_ip']}")
        logger.info(f"  - Records without IP: {validation_result['records_without_ip']}")
        logger.info(f"  - Coverage: {validation_result['coverage_percentage']:.2f}%")
        
        if validation_result['top_ips']:
            logger.info("  - Top IP addresses:")
            for ip, count in validation_result['top_ips']:
                logger.info(f"    {ip}: {count} records")
        
        return validation_result
        
    finally:
        session.close()


def main():
    """
    主函数
    """
    parser = argparse.ArgumentParser(description="Collection IP Field Migration")
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no actual changes)')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for updates (default: 1000)')
    parser.add_argument('--skip-field-creation', action='store_true', help='Skip field creation step')
    parser.add_argument('--skip-data-update', action='store_true', help='Skip data update step')
    parser.add_argument('--validate-only', action='store_true', help='Only run validation')
    
    args = parser.parse_args()
    
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    
    logger.info("=" * 80)
    logger.info("Collection IP Field Migration Script")
    logger.info("=" * 80)
    
    if args.dry_run:
        logger.info("🔍 Running in DRY-RUN mode - no changes will be made")
    
    try:
        # 步骤1: 验证模式
        if args.validate_only:
            logger.info("Running validation only...")
            validate_migration()
            return 0
        
        # 步骤2: 添加字段
        if not args.skip_field_creation:
            logger.info("Step 1: Adding collection_ip field...")
            if not add_collection_ip_field_safe():
                logger.error("Failed to add collection_ip field")
                return 1
        else:
            logger.info("Skipping field creation step")
        
        # 步骤3: 更新数据
        if not args.skip_data_update:
            logger.info("Step 2: Updating collection_ip data...")
            stats = update_collection_ip_batch(
                batch_size=args.batch_size,
                dry_run=args.dry_run
            )
            
            if stats['total_processed'] == 0:
                logger.info("No records needed updating")
            else:
                success_rate = (stats['successfully_updated'] / stats['total_processed']) * 100
                logger.info(f"Update success rate: {success_rate:.2f}%")
        else:
            logger.info("Skipping data update step")
        
        # 步骤4: 验证结果
        logger.info("Step 3: Validating migration results...")
        validate_migration()
        
        logger.info("=" * 80)
        logger.info("✅ Migration completed successfully!")
        logger.info("=" * 80)
        
        return 0
        
    except Exception as e:
        logger.error(f"💥 Migration failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())