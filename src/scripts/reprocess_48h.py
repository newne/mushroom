import sys
import os
from datetime import datetime, timedelta

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from vision.mushroom_image_encoder import create_mushroom_encoder
from utils.create_table import ImageTextQuality
from loguru import logger

def main():
    encoder = create_mushroom_encoder()
    session = encoder.Session()
    
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=48)
    
    logger.info(f"Reprocessing images from {start_time} to {end_time}")
    
    # Find records in the last 48 hours
    records = session.query(ImageTextQuality).filter(
        ImageTextQuality.collection_datetime >= start_time,
        ImageTextQuality.collection_datetime <= end_time
    ).all()
    
    logger.info(f"Found {len(records)} records to reprocess")
    
    success = 0
    failed = 0
    
    for i, record in enumerate(records):
        try:
            logger.info(f"Processing {i+1}/{len(records)}: {record.image_path}")
            image = encoder.minio_client.get_image(record.image_path)
            if image is None:
                logger.error(f"Failed to get image: {record.image_path}")
                failed += 1
                continue
                
            llama_result = encoder._get_llama_description(image)
            growth_stage_description = llama_result.get("growth_stage_description", "")
            chinese_description = llama_result.get("chinese_description", None)
            quality_score = llama_result.get("image_quality_score", None)
            
            if growth_stage_description:
                record.llama_description = growth_stage_description
            if chinese_description:
                record.chinese_description = chinese_description
            if quality_score is not None:
                record.image_quality_score = quality_score
                
            session.commit()
            success += 1
            logger.info(f"Updated record {record.id} successfully")
            
        except Exception as e:
            logger.error(f"Failed to process record {record.id}: {e}")
            session.rollback()
            failed += 1
            
    logger.info(f"Reprocessing complete. Success: {success}, Failed: {failed}")
    session.close()

if __name__ == "__main__":
    main()
