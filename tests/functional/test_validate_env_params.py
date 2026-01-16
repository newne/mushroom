"""
Test script for validate_env_params method

This script tests the environmental parameter validation functionality
with various test cases including valid data, out-of-range values, and edge cases.
"""

import pandas as pd
import numpy as np
from loguru import logger

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.data_extractor import DataExtractor
from utils.create_table import pgsql_engine


def test_validate_env_params():
    """Test validate_env_params with various scenarios"""
    
    # Initialize DataExtractor
    extractor = DataExtractor(pgsql_engine)
    
    print("\n" + "="*80)
    print("TEST 1: Valid environmental parameters")
    print("="*80)
    
    # Test case 1: All valid parameters
    valid_data = pd.DataFrame([
        {'temperature': 15.5, 'humidity': 85.0, 'co2': 1200.0},
        {'temperature': 20.0, 'humidity': 90.0, 'co2': 800.0},
        {'temperature': 18.5, 'humidity': 88.5, 'co2': 1000.0},
    ])
    
    warnings = extractor.validate_env_params(valid_data)
    print(f"Valid data warnings: {len(warnings)}")
    assert len(warnings) == 0, "Expected no warnings for valid data"
    print("✓ PASSED: No warnings for valid data\n")
    
    print("="*80)
    print("TEST 2: Temperature out of range")
    print("="*80)
    
    # Test case 2: Temperature out of range
    temp_invalid_data = pd.DataFrame([
        {'temperature': -5.0, 'humidity': 85.0, 'co2': 1200.0},  # Too low
        {'temperature': 45.0, 'humidity': 90.0, 'co2': 800.0},   # Too high
        {'temperature': 20.0, 'humidity': 88.5, 'co2': 1000.0},  # Valid
    ])
    
    warnings = extractor.validate_env_params(temp_invalid_data)
    print(f"Temperature out-of-range warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 2, f"Expected 2 warnings, got {len(warnings)}"
    assert any('Temperature out of range' in w for w in warnings)
    print("✓ PASSED: Detected temperature out of range\n")
    
    print("="*80)
    print("TEST 3: Humidity out of range")
    print("="*80)
    
    # Test case 3: Humidity out of range
    humidity_invalid_data = pd.DataFrame([
        {'temperature': 20.0, 'humidity': -10.0, 'co2': 1200.0},  # Too low
        {'temperature': 20.0, 'humidity': 105.0, 'co2': 800.0},   # Too high
        {'temperature': 20.0, 'humidity': 88.5, 'co2': 1000.0},   # Valid
    ])
    
    warnings = extractor.validate_env_params(humidity_invalid_data)
    print(f"Humidity out-of-range warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 2, f"Expected 2 warnings, got {len(warnings)}"
    assert any('Humidity out of range' in w for w in warnings)
    print("✓ PASSED: Detected humidity out of range\n")
    
    print("="*80)
    print("TEST 4: CO2 out of range")
    print("="*80)
    
    # Test case 4: CO2 out of range
    co2_invalid_data = pd.DataFrame([
        {'temperature': 20.0, 'humidity': 85.0, 'co2': -100.0},  # Too low
        {'temperature': 20.0, 'humidity': 90.0, 'co2': 6000.0},  # Too high
        {'temperature': 20.0, 'humidity': 88.5, 'co2': 1000.0},  # Valid
    ])
    
    warnings = extractor.validate_env_params(co2_invalid_data)
    print(f"CO2 out-of-range warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 2, f"Expected 2 warnings, got {len(warnings)}"
    assert any('CO2 out of range' in w for w in warnings)
    print("✓ PASSED: Detected CO2 out of range\n")
    
    print("="*80)
    print("TEST 5: Multiple parameters out of range")
    print("="*80)
    
    # Test case 5: Multiple parameters out of range
    multi_invalid_data = pd.DataFrame([
        {'temperature': -5.0, 'humidity': 105.0, 'co2': 6000.0},  # All invalid
        {'temperature': 20.0, 'humidity': 90.0, 'co2': 1000.0},   # All valid
    ])
    
    warnings = extractor.validate_env_params(multi_invalid_data)
    print(f"Multiple out-of-range warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 3, f"Expected 3 warnings, got {len(warnings)}"
    print("✓ PASSED: Detected multiple parameters out of range\n")
    
    print("="*80)
    print("TEST 6: Statistical data columns (env_daily_stats)")
    print("="*80)
    
    # Test case 6: Statistical data with median, min, max, q25, q75
    stats_data = pd.DataFrame([
        {
            'temp_median': 20.0, 'temp_min': 18.0, 'temp_max': 22.0,
            'temp_q25': 19.0, 'temp_q75': 21.0,
            'humidity_median': 85.0, 'humidity_min': 80.0, 'humidity_max': 90.0,
            'humidity_q25': 82.0, 'humidity_q75': 88.0,
            'co2_median': 1200.0, 'co2_min': 1000.0, 'co2_max': 1400.0,
            'co2_q25': 1100.0, 'co2_q75': 1300.0,
        },
        {
            'temp_median': 45.0, 'temp_min': 42.0, 'temp_max': 48.0,  # All invalid
            'temp_q25': 43.0, 'temp_q75': 46.0,
            'humidity_median': 85.0, 'humidity_min': 80.0, 'humidity_max': 90.0,
            'humidity_q25': 82.0, 'humidity_q75': 88.0,
            'co2_median': 1200.0, 'co2_min': 1000.0, 'co2_max': 1400.0,
            'co2_q25': 1100.0, 'co2_q75': 1300.0,
        }
    ])
    
    warnings = extractor.validate_env_params(stats_data)
    print(f"Statistical data warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    # Should detect 5 temperature violations in the second row
    assert len(warnings) == 5, f"Expected 5 warnings, got {len(warnings)}"
    print("✓ PASSED: Validated statistical data columns\n")
    
    print("="*80)
    print("TEST 7: Data with NaN/None values")
    print("="*80)
    
    # Test case 7: Data with missing values (should be skipped)
    nan_data = pd.DataFrame([
        {'temperature': np.nan, 'humidity': 85.0, 'co2': 1200.0},
        {'temperature': 20.0, 'humidity': None, 'co2': 800.0},
        {'temperature': 20.0, 'humidity': 88.5, 'co2': np.nan},
        {'temperature': 45.0, 'humidity': 105.0, 'co2': 6000.0},  # All invalid
    ])
    
    warnings = extractor.validate_env_params(nan_data)
    print(f"NaN data warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    # Should only detect the last row's violations (3 warnings)
    assert len(warnings) == 3, f"Expected 3 warnings, got {len(warnings)}"
    print("✓ PASSED: Correctly skipped NaN/None values\n")
    
    print("="*80)
    print("TEST 8: Empty DataFrame")
    print("="*80)
    
    # Test case 8: Empty DataFrame
    empty_data = pd.DataFrame()
    
    warnings = extractor.validate_env_params(empty_data)
    print(f"Empty data warnings: {len(warnings)}")
    assert len(warnings) == 0, "Expected no warnings for empty DataFrame"
    print("✓ PASSED: Handled empty DataFrame\n")
    
    print("="*80)
    print("TEST 9: Boundary values")
    print("="*80)
    
    # Test case 9: Boundary values (exactly at limits)
    boundary_data = pd.DataFrame([
        {'temperature': 0.0, 'humidity': 0.0, 'co2': 0.0},      # Lower boundaries
        {'temperature': 40.0, 'humidity': 100.0, 'co2': 5000.0}, # Upper boundaries
        {'temperature': 0.0, 'humidity': 100.0, 'co2': 2500.0},  # Mixed boundaries
    ])
    
    warnings = extractor.validate_env_params(boundary_data)
    print(f"Boundary values warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 0, "Expected no warnings for boundary values"
    print("✓ PASSED: Boundary values are valid\n")
    
    print("="*80)
    print("TEST 10: Just outside boundaries")
    print("="*80)
    
    # Test case 10: Just outside boundaries
    outside_boundary_data = pd.DataFrame([
        {'temperature': -0.1, 'humidity': -0.1, 'co2': -0.1},      # Just below
        {'temperature': 40.1, 'humidity': 100.1, 'co2': 5000.1},   # Just above
    ])
    
    warnings = extractor.validate_env_params(outside_boundary_data)
    print(f"Outside boundary warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    assert len(warnings) == 6, f"Expected 6 warnings, got {len(warnings)}"
    print("✓ PASSED: Detected values just outside boundaries\n")
    
    print("="*80)
    print("ALL TESTS PASSED! ✓")
    print("="*80)


if __name__ == "__main__":
    try:
        test_validate_env_params()
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        raise
