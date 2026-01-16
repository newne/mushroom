"""
Test script for extract_current_embedding_data method

This script tests the DataExtractor.extract_current_embedding_data method
to ensure it correctly filters and extracts data from the MushroomImageEmbedding table.
"""

import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from global_const.global_const import pgsql_engine
from decision_analysis.data_extractor import DataExtractor


def test_extract_embedding_data():
    """Test the extract_current_embedding_data method"""
    
    logger.info("=" * 80)
    logger.info("Testing DataExtractor.extract_current_embedding_data")
    logger.info("=" * 80)
    
    # Initialize extractor
    extractor = DataExtractor(pgsql_engine)
    
    # Test parameters - using actual data dates from the database
    # Note: in_date is the batch entry date, not the collection date
    test_cases = [
        {
            "room_id": "611",
            "target_datetime": datetime(2026, 1, 15, 17, 0, 0),
            "time_window_days": 30,  # Wider window to catch in_date=2025-12-24
            "growth_day_window": 3,
        },
        {
            "room_id": "607",
            "target_datetime": datetime(2026, 1, 16, 10, 0, 0),
            "time_window_days": 30,  # Wider window to catch in_date=2025-12-31
            "growth_day_window": 3,
        },
        {
            "room_id": "608",
            "target_datetime": datetime(2026, 1, 16, 10, 0, 0),
            "time_window_days": 15,  # Wider window to catch in_date=2026-01-05
            "growth_day_window": 3,
        },
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        logger.info(f"\n--- Test Case {i} ---")
        logger.info(f"Parameters: {test_case}")
        
        result = extractor.extract_current_embedding_data(**test_case)
        
        if result.empty:
            logger.warning(f"Test case {i}: No data returned (empty DataFrame)")
        else:
            logger.success(f"Test case {i}: Retrieved {len(result)} records")
            logger.info(f"Columns: {list(result.columns)}")
            logger.info(f"Room IDs: {result['room_id'].unique()}")
            logger.info(f"Growth days range: {result['growth_day'].min()} - {result['growth_day'].max()}")
            logger.info(f"In dates range: {result['in_date'].min()} - {result['in_date'].max()}")
            logger.info(f"Collection datetime range: {result['collection_datetime'].min()} - {result['collection_datetime'].max()}")
            
            # Verify filters are applied correctly
            assert all(result['room_id'] == test_case['room_id']), "Room ID filter failed"
            logger.success("✓ Room ID filter verified")
            
            # Check date window
            target_date = test_case['target_datetime'].date()
            from datetime import timedelta
            min_date = target_date - timedelta(days=test_case['time_window_days'])
            max_date = target_date + timedelta(days=test_case['time_window_days'])
            assert all((result['in_date'] >= min_date) & (result['in_date'] <= max_date)), "Date window filter failed"
            logger.success(f"✓ Date window filter verified: [{min_date}, {max_date}]")
            
            # Display sample record
            logger.info("\nSample record (first row):")
            sample = result.iloc[0]
            logger.info(f"  ID: {sample['id']}")
            logger.info(f"  Collection time: {sample['collection_datetime']}")
            logger.info(f"  Room: {sample['room_id']}")
            logger.info(f"  Growth day: {sample['growth_day']}")
            logger.info(f"  In date: {sample['in_date']}")
            logger.info(f"  Semantic description: {sample['semantic_description'][:100]}...")
            logger.info(f"  Embedding shape: {sample['embedding'].shape if hasattr(sample['embedding'], 'shape') else 'N/A'}")
            logger.info(f"  Env sensor status: {sample['env_sensor_status']}")
            logger.info(f"  Air cooler config keys: {list(sample['air_cooler_config'].keys()) if isinstance(sample['air_cooler_config'], dict) else 'N/A'}")
    
    logger.info("\n" + "=" * 80)
    logger.success("All tests completed!")
    logger.info("=" * 80)


if __name__ == "__main__":
    test_extract_embedding_data()
