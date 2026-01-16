"""
Integration test for validate_env_params with real data extraction

This script demonstrates how validate_env_params integrates with the
actual data extraction workflow, validating data from the database.
"""

from datetime import datetime, date, timedelta
from loguru import logger

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.data_extractor import DataExtractor
from utils.create_table import pgsql_engine


def test_integration_with_embedding_data():
    """Test validate_env_params with extracted embedding data"""
    
    print("\n" + "="*80)
    print("INTEGRATION TEST 1: Validate extracted embedding data")
    print("="*80)
    
    extractor = DataExtractor(pgsql_engine)
    
    # Extract some real data
    room_id = "611"
    target_datetime = datetime(2025, 1, 10, 10, 0, 0)
    
    print(f"Extracting embedding data for room {room_id} at {target_datetime}")
    df = extractor.extract_current_embedding_data(
        room_id=room_id,
        target_datetime=target_datetime,
        time_window_days=7,
        growth_day_window=3
    )
    
    if df.empty:
        print("⚠ No data found for this query. Skipping validation test.")
        return
    
    print(f"Extracted {len(df)} records")
    
    # Extract environmental parameters from env_sensor_status JSON
    import json
    
    env_data = []
    for idx, row in df.iterrows():
        if row['env_sensor_status']:
            try:
                env_status = row['env_sensor_status']
                if isinstance(env_status, str):
                    env_status = json.loads(env_status)
                
                env_data.append({
                    'temperature': env_status.get('temperature'),
                    'humidity': env_status.get('humidity'),
                    'co2': env_status.get('co2'),
                })
            except Exception as e:
                logger.warning(f"Failed to parse env_sensor_status: {e}")
    
    if not env_data:
        print("⚠ No environmental data found in records. Skipping validation.")
        return
    
    import pandas as pd
    env_df = pd.DataFrame(env_data)
    
    print(f"\nEnvironmental data summary:")
    print(f"  Temperature: min={env_df['temperature'].min():.2f}, "
          f"max={env_df['temperature'].max():.2f}, "
          f"mean={env_df['temperature'].mean():.2f}")
    print(f"  Humidity: min={env_df['humidity'].min():.2f}, "
          f"max={env_df['humidity'].max():.2f}, "
          f"mean={env_df['humidity'].mean():.2f}")
    print(f"  CO2: min={env_df['co2'].min():.2f}, "
          f"max={env_df['co2'].max():.2f}, "
          f"mean={env_df['co2'].mean():.2f}")
    
    # Validate the environmental parameters
    print("\nValidating environmental parameters...")
    warnings = extractor.validate_env_params(env_df)
    
    if warnings:
        print(f"\n⚠ Found {len(warnings)} validation warnings:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("✓ All environmental parameters are within valid ranges")
    
    print()


def test_integration_with_env_stats():
    """Test validate_env_params with extracted environmental statistics"""
    
    print("="*80)
    print("INTEGRATION TEST 2: Validate extracted environmental statistics")
    print("="*80)
    
    extractor = DataExtractor(pgsql_engine)
    
    # Extract environmental statistics
    room_id = "611"
    target_date = date(2025, 1, 10)
    
    print(f"Extracting env stats for room {room_id} around {target_date}")
    df = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=1
    )
    
    if df.empty:
        print("⚠ No environmental statistics found. Skipping validation test.")
        return
    
    print(f"Extracted {len(df)} daily statistics records")
    
    # Display the data
    print("\nEnvironmental statistics:")
    for idx, row in df.iterrows():
        print(f"\n  Date: {row['stat_date']}")
        print(f"    Temperature: median={row['temp_median']:.2f}°C, "
              f"range=[{row['temp_min']:.2f}, {row['temp_max']:.2f}]")
        print(f"    Humidity: median={row['humidity_median']:.2f}%, "
              f"range=[{row['humidity_min']:.2f}, {row['humidity_max']:.2f}]")
        print(f"    CO2: median={row['co2_median']:.2f}ppm, "
              f"range=[{row['co2_min']:.2f}, {row['co2_max']:.2f}]")
        
        if row.get('temp_trend'):
            print(f"    Trends: temp={row['temp_trend']}, "
                  f"humidity={row['humidity_trend']}, "
                  f"co2={row['co2_trend']}")
    
    # Validate the environmental statistics
    print("\nValidating environmental statistics...")
    warnings = extractor.validate_env_params(df)
    
    if warnings:
        print(f"\n⚠ Found {len(warnings)} validation warnings:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("✓ All environmental statistics are within valid ranges")
    
    print()


def test_validation_workflow():
    """Test the complete validation workflow"""
    
    print("="*80)
    print("INTEGRATION TEST 3: Complete validation workflow")
    print("="*80)
    
    extractor = DataExtractor(pgsql_engine)
    
    # Simulate a complete data extraction and validation workflow
    room_id = "611"
    target_datetime = datetime(2025, 1, 10, 10, 0, 0)
    target_date = target_datetime.date()
    
    print(f"Running complete validation workflow for room {room_id}")
    print(f"Target datetime: {target_datetime}")
    
    all_warnings = []
    
    # Step 1: Extract and validate embedding data
    print("\n1. Extracting and validating embedding data...")
    embedding_df = extractor.extract_current_embedding_data(
        room_id=room_id,
        target_datetime=target_datetime,
        time_window_days=7,
        growth_day_window=3
    )
    
    if not embedding_df.empty:
        # Extract env data from JSON
        import json
        import pandas as pd
        
        env_data = []
        for idx, row in embedding_df.iterrows():
            if row['env_sensor_status']:
                try:
                    env_status = row['env_sensor_status']
                    if isinstance(env_status, str):
                        env_status = json.loads(env_status)
                    
                    env_data.append({
                        'temperature': env_status.get('temperature'),
                        'humidity': env_status.get('humidity'),
                        'co2': env_status.get('co2'),
                    })
                except:
                    pass
        
        if env_data:
            env_df = pd.DataFrame(env_data)
            warnings = extractor.validate_env_params(env_df)
            all_warnings.extend(warnings)
            print(f"   Found {len(warnings)} warnings in embedding data")
    else:
        print("   No embedding data found")
    
    # Step 2: Extract and validate environmental statistics
    print("\n2. Extracting and validating environmental statistics...")
    stats_df = extractor.extract_env_daily_stats(
        room_id=room_id,
        target_date=target_date,
        days_range=1
    )
    
    if not stats_df.empty:
        warnings = extractor.validate_env_params(stats_df)
        all_warnings.extend(warnings)
        print(f"   Found {len(warnings)} warnings in environmental statistics")
    else:
        print("   No environmental statistics found")
    
    # Summary
    print("\n" + "="*80)
    print("VALIDATION WORKFLOW SUMMARY")
    print("="*80)
    print(f"Total warnings found: {len(all_warnings)}")
    
    if all_warnings:
        print("\nAll warnings:")
        for i, w in enumerate(all_warnings, 1):
            print(f"  {i}. {w}")
    else:
        print("✓ All data passed validation - no out-of-range values detected")
    
    print()


if __name__ == "__main__":
    try:
        test_integration_with_embedding_data()
        test_integration_with_env_stats()
        test_validation_workflow()
        
        print("="*80)
        print("ALL INTEGRATION TESTS COMPLETED ✓")
        print("="*80)
        
    except Exception as e:
        logger.error(f"Integration test failed: {e}", exc_info=True)
        raise
