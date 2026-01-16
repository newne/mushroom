"""
Test script for extract_env_daily_stats method

This script tests the implementation of the extract_env_daily_stats method
from the DataExtractor class.
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
from loguru import logger

from global_const.global_const import pgsql_engine
from decision_analysis.data_extractor import DataExtractor


def test_extract_env_daily_stats():
    """Test the extract_env_daily_stats method"""
    
    logger.info("=" * 80)
    logger.info("Testing extract_env_daily_stats method")
    logger.info("=" * 80)
    
    # Initialize DataExtractor
    extractor = DataExtractor(pgsql_engine)
    
    # Test case 1: Extract data for a specific room and date
    logger.info("\n[Test 1] Extract env stats for room 611, date 2026-01-14")
    room_id = "611"
    target_date = date(2026, 1, 14)
    
    result = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=1
    )
    
    if not result.empty:
        logger.info(f"✓ Successfully extracted {len(result)} records")
        logger.info(f"Columns: {list(result.columns)}")
        logger.info(f"\nFirst few records:")
        logger.info(f"\n{result.head()}")
        
        # Check for trend columns
        trend_columns = ['temp_change_rate', 'humidity_change_rate', 'co2_change_rate',
                        'temp_trend', 'humidity_trend', 'co2_trend']
        
        for col in trend_columns:
            if col in result.columns:
                logger.info(f"✓ Trend column '{col}' present")
            else:
                logger.warning(f"✗ Trend column '{col}' missing")
        
        # Display trend information
        if len(result) > 1:
            logger.info("\n[Trend Analysis]")
            for idx, row in result.iterrows():
                if pd.notna(row.get('temp_trend')):
                    logger.info(
                        f"Date: {row['stat_date']}, "
                        f"Temp: {row['temp_median']}°C ({row['temp_trend']}, {row['temp_change_rate']}%), "
                        f"Humidity: {row['humidity_median']}% ({row['humidity_trend']}, {row['humidity_change_rate']}%), "
                        f"CO2: {row['co2_median']}ppm ({row['co2_trend']}, {row['co2_change_rate']}%)"
                    )
    else:
        logger.warning("✗ No data returned")
    
    # Test case 2: Test with different date range
    logger.info("\n[Test 2] Extract env stats with larger date range (±3 days)")
    result2 = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=3
    )
    
    if not result2.empty:
        logger.info(f"✓ Successfully extracted {len(result2)} records")
        logger.info(f"Date range: {result2['stat_date'].min()} to {result2['stat_date'].max()}")
        
        # Verify date range
        expected_min = target_date - timedelta(days=3)
        expected_max = target_date + timedelta(days=3)
        actual_min = result2['stat_date'].min()
        actual_max = result2['stat_date'].max()
        
        if actual_min >= expected_min and actual_max <= expected_max:
            logger.info(f"✓ Date range is correct: [{actual_min}, {actual_max}]")
        else:
            logger.warning(f"✗ Date range mismatch: expected [{expected_min}, {expected_max}], got [{actual_min}, {actual_max}]")
    else:
        logger.warning("✗ No data returned")
    
    # Test case 3: Test with non-existent room
    logger.info("\n[Test 3] Test with non-existent room (should return empty)")
    result3 = extractor.extract_env_daily_stats(
        room_id="999",
        target_date=target_date,
        days_range=1
    )
    
    if result3.empty:
        logger.info("✓ Correctly returned empty DataFrame for non-existent room")
    else:
        logger.warning(f"✗ Expected empty DataFrame, got {len(result3)} records")
    
    # Test case 4: Test with edge date (year boundary)
    logger.info("\n[Test 4] Test with edge date (year boundary)")
    result4 = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=date(2026, 1, 1),
        days_range=1
    )
    
    if not result4.empty:
        logger.info(f"✓ Successfully handled year boundary: {len(result4)} records")
        logger.info(f"Date range: {result4['stat_date'].min()} to {result4['stat_date'].max()}")
    else:
        logger.info("No data available for this date range (expected if no data exists)")
    
    logger.info("\n" + "=" * 80)
    logger.info("Test completed")
    logger.info("=" * 80)


if __name__ == "__main__":
    test_extract_env_daily_stats()
