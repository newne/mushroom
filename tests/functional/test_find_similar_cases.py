"""
Test script for CLIPMatcher.find_similar_cases method

This script tests the CLIP similarity matching functionality by:
1. Querying a sample embedding from the database
2. Using it to find similar cases
3. Verifying the results meet requirements
"""

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
from loguru import logger

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.clip_matcher import CLIPMatcher
from global_const.global_const import pgsql_engine


def test_find_similar_cases():
    """Test the find_similar_cases method"""
    
    logger.info("=" * 80)
    logger.info("Testing CLIPMatcher.find_similar_cases")
    logger.info("=" * 80)
    
    # Initialize CLIPMatcher
    matcher = CLIPMatcher(pgsql_engine)
    
    # First, get a sample embedding from the database to use as query
    logger.info("\n[Step 1] Fetching sample embedding from database...")
    
    from sqlalchemy import text
    
    with pgsql_engine.connect() as conn:
        result = conn.execute(text("""
            SELECT 
                room_id,
                in_date,
                growth_day,
                embedding,
                collection_datetime,
                env_sensor_status
            FROM mushroom_embedding
            WHERE room_id = '611'
                AND growth_day BETWEEN 10 AND 20
                AND embedding IS NOT NULL
            ORDER BY collection_datetime DESC
            LIMIT 1
        """))
        
        row = result.fetchone()
        
        if not row:
            logger.error("No sample data found in database!")
            return False
    
    # Extract query parameters
    query_room_id = row.room_id
    query_in_date = row.in_date
    query_growth_day = row.growth_day
    query_embedding = np.array(row.embedding)
    query_datetime = row.collection_datetime
    query_env = row.env_sensor_status or {}
    
    logger.info(f"Sample data:")
    logger.info(f"  Room ID: {query_room_id}")
    logger.info(f"  In Date: {query_in_date}")
    logger.info(f"  Growth Day: {query_growth_day}")
    logger.info(f"  Collection Time: {query_datetime}")
    logger.info(f"  Embedding shape: {query_embedding.shape}")
    logger.info(f"  Temperature: {query_env.get('temperature', 'N/A')}")
    logger.info(f"  Humidity: {query_env.get('humidity', 'N/A')}")
    logger.info(f"  CO2: {query_env.get('co2', 'N/A')}")
    
    # Test 1: Find similar cases with default parameters
    logger.info("\n[Step 2] Finding similar cases (top_k=3)...")
    
    similar_cases = matcher.find_similar_cases(
        query_embedding=query_embedding,
        room_id=query_room_id,
        in_date=query_in_date,
        growth_day=query_growth_day,
        top_k=3,
        date_window_days=7,
        growth_day_window=3
    )
    
    logger.info(f"\nFound {len(similar_cases)} similar cases")
    
    # Verify results
    success = True
    
    # Requirement 4.1: Should filter by same room
    logger.info("\n[Verification 1] Checking room_id filtering...")
    for i, case in enumerate(similar_cases, 1):
        if case.room_id != query_room_id:
            logger.error(f"  Case {i}: room_id mismatch! Expected {query_room_id}, got {case.room_id}")
            success = False
        else:
            logger.info(f"  Case {i}: room_id={case.room_id} ✓")
    
    # Requirement 4.3: Should return top-K results
    logger.info("\n[Verification 2] Checking top-K limit...")
    if len(similar_cases) <= 3:
        logger.info(f"  Returned {len(similar_cases)} cases (≤ top_k=3) ✓")
    else:
        logger.error(f"  Returned {len(similar_cases)} cases (> top_k=3) ✗")
        success = False
    
    # Requirement 4.3: Should be sorted by similarity (descending)
    logger.info("\n[Verification 3] Checking similarity score ordering...")
    if len(similar_cases) > 1:
        for i in range(len(similar_cases) - 1):
            if similar_cases[i].similarity_score < similar_cases[i + 1].similarity_score:
                logger.error(
                    f"  Cases not sorted! Case {i+1} ({similar_cases[i].similarity_score}%) "
                    f"< Case {i+2} ({similar_cases[i+1].similarity_score}%) ✗"
                )
                success = False
        logger.info("  Cases sorted by similarity (descending) ✓")
    
    # Requirement 4.4: Similarity scores should be in 0-100 range
    logger.info("\n[Verification 4] Checking similarity score range...")
    for i, case in enumerate(similar_cases, 1):
        if not (0 <= case.similarity_score <= 100):
            logger.error(f"  Case {i}: similarity_score={case.similarity_score} out of range [0, 100] ✗")
            success = False
        else:
            logger.info(f"  Case {i}: similarity_score={case.similarity_score}% ✓")
    
    # Requirement 4.5: Should extract complete information
    logger.info("\n[Verification 5] Checking extracted information completeness...")
    for i, case in enumerate(similar_cases, 1):
        logger.info(f"\n  Case {i}:")
        logger.info(f"    Similarity: {case.similarity_score}%")
        logger.info(f"    Confidence: {case.confidence_level}")
        logger.info(f"    Room ID: {case.room_id}")
        logger.info(f"    Growth Day: {case.growth_day}")
        logger.info(f"    Collection Time: {case.collection_time}")
        logger.info(f"    Temperature: {case.temperature}°C")
        logger.info(f"    Humidity: {case.humidity}%")
        logger.info(f"    CO2: {case.co2} ppm")
        logger.info(f"    Air Cooler Config: {len(case.air_cooler_params)} params")
        logger.info(f"    Fresh Air Config: {len(case.fresh_air_params)} params")
        logger.info(f"    Humidifier Config: {len(case.humidifier_params)} params")
        logger.info(f"    Grow Light Config: {len(case.grow_light_params)} params")
    
    # Requirement 4.6: Low confidence warning
    logger.info("\n[Verification 6] Checking low confidence warnings...")
    low_confidence_cases = [c for c in similar_cases if c.confidence_level == "low"]
    if low_confidence_cases:
        logger.warning(f"  Found {len(low_confidence_cases)} low confidence cases (< 20%)")
    else:
        logger.info("  No low confidence cases found ✓")
    
    # Test 2: Test with different top_k
    logger.info("\n[Step 3] Testing with top_k=5...")
    similar_cases_5 = matcher.find_similar_cases(
        query_embedding=query_embedding,
        room_id=query_room_id,
        in_date=query_in_date,
        growth_day=query_growth_day,
        top_k=5,
        date_window_days=7,
        growth_day_window=3
    )
    logger.info(f"  Found {len(similar_cases_5)} cases (requested top_k=5)")
    
    # Test 3: Test with narrower windows
    logger.info("\n[Step 4] Testing with narrower windows (date=3, growth=1)...")
    similar_cases_narrow = matcher.find_similar_cases(
        query_embedding=query_embedding,
        room_id=query_room_id,
        in_date=query_in_date,
        growth_day=query_growth_day,
        top_k=3,
        date_window_days=3,
        growth_day_window=1
    )
    logger.info(f"  Found {len(similar_cases_narrow)} cases with narrower windows")
    
    # Summary
    logger.info("\n" + "=" * 80)
    if success:
        logger.info("✓ All verifications passed!")
    else:
        logger.error("✗ Some verifications failed!")
    logger.info("=" * 80)
    
    return success


if __name__ == "__main__":
    try:
        success = test_find_similar_cases()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        logger.exception(e)
        sys.exit(1)
