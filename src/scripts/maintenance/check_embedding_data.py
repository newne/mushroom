"""
Check what data is available in the MushroomImageEmbedding table
"""

import sys
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from loguru import logger
from global_const.global_const import pgsql_engine
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from utils.create_table import MushroomImageEmbedding


def check_available_data():
    """Check what data is available in the database"""
    
    logger.info("Checking available data in MushroomImageEmbedding table...")
    
    with Session(pgsql_engine) as session:
        # Count total records
        count_query = select(func.count()).select_from(MushroomImageEmbedding)
        total_count = session.execute(count_query).scalar()
        logger.info(f"Total records: {total_count}")
        
        if total_count == 0:
            logger.warning("No data in the table!")
            return
        
        # Get room IDs
        room_query = select(MushroomImageEmbedding.room_id).distinct()
        rooms = session.execute(room_query).scalars().all()
        logger.info(f"Available room IDs: {rooms}")
        
        # Get date range
        date_query = select(
            func.min(MushroomImageEmbedding.collection_datetime),
            func.max(MushroomImageEmbedding.collection_datetime),
            func.min(MushroomImageEmbedding.in_date),
            func.max(MushroomImageEmbedding.in_date)
        )
        date_result = session.execute(date_query).first()
        logger.info(f"Collection datetime range: {date_result[0]} to {date_result[1]}")
        logger.info(f"In date range: {date_result[2]} to {date_result[3]}")
        
        # Get growth day range
        growth_query = select(
            func.min(MushroomImageEmbedding.growth_day),
            func.max(MushroomImageEmbedding.growth_day)
        )
        growth_result = session.execute(growth_query).first()
        logger.info(f"Growth day range: {growth_result[0]} to {growth_result[1]}")
        
        # Get sample records for each room
        for room_id in rooms:
            logger.info(f"\n--- Sample data for room {room_id} ---")
            sample_query = (
                select(
                    MushroomImageEmbedding.collection_datetime,
                    MushroomImageEmbedding.in_date,
                    MushroomImageEmbedding.growth_day,
                    MushroomImageEmbedding.semantic_description
                )
                .where(MushroomImageEmbedding.room_id == room_id)
                .order_by(MushroomImageEmbedding.collection_datetime.desc())
                .limit(3)
            )
            samples = session.execute(sample_query).fetchall()
            for i, sample in enumerate(samples, 1):
                logger.info(f"  Sample {i}:")
                logger.info(f"    Collection time: {sample[0]}")
                logger.info(f"    In date: {sample[1]}")
                logger.info(f"    Growth day: {sample[2]}")
                logger.info(f"    Description: {sample[3][:80]}...")


if __name__ == "__main__":
    check_available_data()
