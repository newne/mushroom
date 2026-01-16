"""
Test script for extract_device_changes method

This script tests the DataExtractor.extract_device_changes method
to ensure it correctly queries the DeviceSetpointChange table.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from global_const.global_const import pgsql_engine
from decision_analysis.data_extractor import DataExtractor


def test_extract_device_changes():
    """Test extract_device_changes method"""
    
    logger.info("=" * 80)
    logger.info("Testing extract_device_changes method")
    logger.info("=" * 80)
    
    # Initialize DataExtractor
    extractor = DataExtractor(pgsql_engine)
    
    # Test parameters
    room_id = "611"
    end_time = datetime.now()
    start_time = end_time - timedelta(days=7)  # Last 7 days
    
    logger.info(f"\nTest 1: Extract all device changes for room {room_id}")
    logger.info(f"Time range: {start_time} to {end_time}")
    
    # Test 1: Extract all device changes
    df_all = extractor.extract_device_changes(
        room_id=room_id,
        start_time=start_time,
        end_time=end_time
    )
    
    if not df_all.empty:
        logger.success(f"✓ Found {len(df_all)} device change records")
        logger.info(f"\nColumns: {df_all.columns.tolist()}")
        logger.info(f"\nFirst few records:")
        logger.info(f"\n{df_all.head()}")
        
        # Check data types
        logger.info(f"\nData types:")
        logger.info(f"\n{df_all.dtypes}")
        
        # Check sorting (should be descending by change_time)
        if len(df_all) > 1:
            is_sorted = all(
                df_all.iloc[i]['change_time'] >= df_all.iloc[i+1]['change_time']
                for i in range(len(df_all) - 1)
            )
            if is_sorted:
                logger.success("✓ Records are correctly sorted by change_time (descending)")
            else:
                logger.error("✗ Records are NOT sorted correctly")
        
        # Show device type distribution
        logger.info(f"\nDevice type distribution:")
        device_counts = df_all['device_type'].value_counts()
        for device_type, count in device_counts.items():
            logger.info(f"  {device_type}: {count} changes")
        
        # Show change type distribution
        logger.info(f"\nChange type distribution:")
        change_type_counts = df_all['change_type'].value_counts()
        for change_type, count in change_type_counts.items():
            logger.info(f"  {change_type}: {count} changes")
    else:
        logger.warning(f"No device changes found for room {room_id}")
    
    # Test 2: Filter by specific device types
    logger.info(f"\n{'=' * 80}")
    logger.info("Test 2: Filter by specific device types (air_cooler, fresh_air_fan)")
    
    df_filtered = extractor.extract_device_changes(
        room_id=room_id,
        start_time=start_time,
        end_time=end_time,
        device_types=['air_cooler', 'fresh_air_fan']
    )
    
    if not df_filtered.empty:
        logger.success(f"✓ Found {len(df_filtered)} filtered device change records")
        
        # Verify all records match the filter
        unique_types = df_filtered['device_type'].unique()
        logger.info(f"Device types in filtered results: {unique_types.tolist()}")
        
        if all(dt in ['air_cooler', 'fresh_air_fan'] for dt in unique_types):
            logger.success("✓ All records match the device type filter")
        else:
            logger.error("✗ Some records don't match the device type filter")
    else:
        logger.warning("No device changes found with the specified device types")
    
    # Test 3: Test with empty result (future date range)
    logger.info(f"\n{'=' * 80}")
    logger.info("Test 3: Test with empty result (future date range)")
    
    future_start = datetime.now() + timedelta(days=365)
    future_end = future_start + timedelta(days=7)
    
    df_empty = extractor.extract_device_changes(
        room_id=room_id,
        start_time=future_start,
        end_time=future_end
    )
    
    if df_empty.empty:
        logger.success("✓ Correctly returned empty DataFrame for future date range")
    else:
        logger.error(f"✗ Expected empty DataFrame but got {len(df_empty)} records")
    
    # Test 4: Test with different room
    logger.info(f"\n{'=' * 80}")
    logger.info("Test 4: Test with different room (607)")
    
    df_room_607 = extractor.extract_device_changes(
        room_id="607",
        start_time=start_time,
        end_time=end_time
    )
    
    if not df_room_607.empty:
        logger.success(f"✓ Found {len(df_room_607)} device change records for room 607")
        
        # Verify all records are for room 607
        if all(df_room_607['room_id'] == "607"):
            logger.success("✓ All records are for room 607")
        else:
            logger.error("✗ Some records are not for room 607")
    else:
        logger.warning("No device changes found for room 607")
    
    # Test 5: Test with narrow time range
    logger.info(f"\n{'=' * 80}")
    logger.info("Test 5: Test with narrow time range (last 24 hours)")
    
    narrow_start = datetime.now() - timedelta(hours=24)
    narrow_end = datetime.now()
    
    df_narrow = extractor.extract_device_changes(
        room_id=room_id,
        start_time=narrow_start,
        end_time=narrow_end
    )
    
    logger.info(f"Found {len(df_narrow)} device changes in the last 24 hours")
    
    if not df_narrow.empty:
        # Verify all records are within the time range
        all_in_range = all(
            (narrow_start <= row['change_time'] <= narrow_end)
            for _, row in df_narrow.iterrows()
        )
        
        if all_in_range:
            logger.success("✓ All records are within the specified time range")
        else:
            logger.error("✗ Some records are outside the specified time range")
    
    logger.info(f"\n{'=' * 80}")
    logger.info("All tests completed!")
    logger.info("=" * 80)


if __name__ == "__main__":
    test_extract_device_changes()
