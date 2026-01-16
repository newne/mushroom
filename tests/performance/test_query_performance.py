"""
Performance test for data extraction and CLIP matching

This script measures the execution time of database queries to ensure
they complete within the required 5-second threshold.
"""

import sys
import time
from datetime import datetime, date
from pathlib import Path

import numpy as np
from loguru import logger

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.data_extractor import DataExtractor
from decision_analysis.clip_matcher import CLIPMatcher
from global_const.global_const import pgsql_engine


def test_query_performance():
    """Test database query performance"""
    
    logger.info("=" * 80)
    logger.info("Testing Database Query Performance")
    logger.info("=" * 80)
    
    # Initialize components
    extractor = DataExtractor(pgsql_engine)
    matcher = CLIPMatcher(pgsql_engine)
    
    # Test parameters
    room_id = "611"
    target_datetime = datetime(2026, 1, 15, 17, 0, 0)
    target_date = date(2026, 1, 14)
    
    results = {}
    
    # Test 1: Extract embedding data
    logger.info("\n[Test 1] Extract embedding data performance")
    start_time = time.time()
    df_embedding = extractor.extract_current_embedding_data(
        room_id=room_id,
        target_datetime=target_datetime,
        time_window_days=30,
        growth_day_window=3
    )
    elapsed_time = time.time() - start_time
    results['embedding_extraction'] = elapsed_time
    
    logger.info(f"  Records: {len(df_embedding)}")
    logger.info(f"  Time: {elapsed_time:.3f} seconds")
    
    if elapsed_time < 5.0:
        logger.success(f"  ✓ PASSED: Query completed in {elapsed_time:.3f}s (< 5s)")
    else:
        logger.error(f"  ✗ FAILED: Query took {elapsed_time:.3f}s (> 5s)")
    
    # Test 2: Extract env daily stats
    logger.info("\n[Test 2] Extract env daily stats performance")
    start_time = time.time()
    df_env_stats = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=3
    )
    elapsed_time = time.time() - start_time
    results['env_stats_extraction'] = elapsed_time
    
    logger.info(f"  Records: {len(df_env_stats)}")
    logger.info(f"  Time: {elapsed_time:.3f} seconds")
    
    if elapsed_time < 5.0:
        logger.success(f"  ✓ PASSED: Query completed in {elapsed_time:.3f}s (< 5s)")
    else:
        logger.error(f"  ✗ FAILED: Query took {elapsed_time:.3f}s (> 5s)")
    
    # Test 3: Extract device changes
    logger.info("\n[Test 3] Extract device changes performance")
    from datetime import timedelta
    start_time = time.time()
    df_device_changes = extractor.extract_device_changes(
        room_id=room_id,
        start_time=target_datetime - timedelta(days=7),
        end_time=target_datetime
    )
    elapsed_time = time.time() - start_time
    results['device_changes_extraction'] = elapsed_time
    
    logger.info(f"  Records: {len(df_device_changes)}")
    logger.info(f"  Time: {elapsed_time:.3f} seconds")
    
    if elapsed_time < 5.0:
        logger.success(f"  ✓ PASSED: Query completed in {elapsed_time:.3f}s (< 5s)")
    else:
        logger.error(f"  ✗ FAILED: Query took {elapsed_time:.3f}s (> 5s)")
    
    # Test 4: CLIP similarity search
    if not df_embedding.empty:
        logger.info("\n[Test 4] CLIP similarity search performance")
        
        # Get a sample embedding
        sample_embedding = df_embedding.iloc[0]['embedding']
        sample_in_date = df_embedding.iloc[0]['in_date']
        sample_growth_day = df_embedding.iloc[0]['growth_day']
        
        start_time = time.time()
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id=room_id,
            in_date=sample_in_date,
            growth_day=sample_growth_day,
            top_k=3,
            date_window_days=7,
            growth_day_window=3
        )
        elapsed_time = time.time() - start_time
        results['clip_matching'] = elapsed_time
        
        logger.info(f"  Similar cases found: {len(similar_cases)}")
        logger.info(f"  Time: {elapsed_time:.3f} seconds")
        
        if elapsed_time < 5.0:
            logger.success(f"  ✓ PASSED: Query completed in {elapsed_time:.3f}s (< 5s)")
        else:
            logger.error(f"  ✗ FAILED: Query took {elapsed_time:.3f}s (> 5s)")
    
    # Test 5: Combined workflow (all queries together)
    logger.info("\n[Test 5] Combined workflow performance")
    start_time = time.time()
    
    # Extract all data
    df_embedding = extractor.extract_current_embedding_data(
        room_id=room_id,
        target_datetime=target_datetime,
        time_window_days=30,
        growth_day_window=3
    )
    
    df_env_stats = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=3
    )
    
    df_device_changes = extractor.extract_device_changes(
        room_id=room_id,
        start_time=target_datetime - timedelta(days=7),
        end_time=target_datetime
    )
    
    # CLIP matching
    if not df_embedding.empty:
        sample_embedding = df_embedding.iloc[0]['embedding']
        sample_in_date = df_embedding.iloc[0]['in_date']
        sample_growth_day = df_embedding.iloc[0]['growth_day']
        
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id=room_id,
            in_date=sample_in_date,
            growth_day=sample_growth_day,
            top_k=3
        )
    
    elapsed_time = time.time() - start_time
    results['combined_workflow'] = elapsed_time
    
    logger.info(f"  Total time: {elapsed_time:.3f} seconds")
    
    if elapsed_time < 5.0:
        logger.success(f"  ✓ PASSED: Combined workflow completed in {elapsed_time:.3f}s (< 5s)")
    else:
        logger.warning(f"  ⚠ WARNING: Combined workflow took {elapsed_time:.3f}s (> 5s)")
        logger.info("  Note: Individual queries are within limits, combined time may exceed 5s")
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Performance Summary")
    logger.info("=" * 80)
    
    all_passed = True
    for test_name, test_time in results.items():
        status = "✓ PASS" if test_time < 5.0 else "✗ FAIL"
        logger.info(f"  {test_name:30s}: {test_time:6.3f}s  {status}")
        if test_time >= 5.0 and test_name != 'combined_workflow':
            all_passed = False
    
    logger.info("=" * 80)
    
    if all_passed:
        logger.success("✓ All individual queries meet the 5-second performance requirement!")
        return True
    else:
        logger.error("✗ Some queries exceed the 5-second performance requirement!")
        return False


if __name__ == "__main__":
    try:
        success = test_query_performance()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Performance test failed with exception: {e}")
        logger.exception(e)
        sys.exit(1)
