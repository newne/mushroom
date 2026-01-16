#!/usr/bin/env python3
"""
Test script for OutputHandler implementation

Tests tasks 7.1, 7.2, and 7.3:
- validate_and_format method
- _validate_device_params method
- _correct_invalid_params method (parameter correction logic)
"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.output_handler import OutputHandler
from global_const.global_const import static_settings


def test_validate_device_params():
    """Test device parameter validation"""
    print("\n" + "="*80)
    print("TEST 1: Device Parameter Validation (_validate_device_params)")
    print("="*80)
    
    handler = OutputHandler(static_settings)
    
    # Test 1.1: Valid air cooler parameters
    print("\n[Test 1.1] Valid air cooler parameters")
    valid_air_cooler = {
        'tem_set': 15.0,
        'tem_diff_set': 2.0,
        'cyc_on_off': 1,
        'cyc_on_time': 10,
        'cyc_off_time': 10,
        'ar_on_off': 0,
        'hum_on_off': 1
    }
    is_valid, errors = handler._validate_device_params('air_cooler', valid_air_cooler)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert is_valid, "Valid parameters should pass validation"
    
    # Test 1.2: Invalid air cooler parameters (out of range)
    print("\n[Test 1.2] Invalid air cooler parameters (out of range)")
    invalid_air_cooler = {
        'tem_set': 30.0,  # Out of range [5, 25]
        'tem_diff_set': 10.0,  # Out of range [0.5, 5]
        'cyc_on_time': 100,  # Out of range [1, 60]
        'cyc_on_off': 5  # Invalid enum
    }
    is_valid, errors = handler._validate_device_params('air_cooler', invalid_air_cooler)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert not is_valid, "Invalid parameters should fail validation"
    assert len(errors) > 0, "Should have error messages"
    
    # Test 1.3: Valid fresh air fan parameters
    print("\n[Test 1.3] Valid fresh air fan parameters")
    valid_fresh_air = {
        'model': 1,
        'control': 1,
        'co2_on': 1200,
        'co2_off': 800,
        'on': 15,
        'off': 15
    }
    is_valid, errors = handler._validate_device_params('fresh_air_fan', valid_fresh_air)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert is_valid, "Valid parameters should pass validation"
    
    # Test 1.4: Invalid fresh air fan (co2_on <= co2_off)
    print("\n[Test 1.4] Invalid fresh air fan (co2_on <= co2_off)")
    invalid_fresh_air = {
        'model': 1,
        'control': 1,
        'co2_on': 800,  # Should be > co2_off
        'co2_off': 1000,
        'on': 15,
        'off': 15
    }
    is_valid, errors = handler._validate_device_params('fresh_air_fan', invalid_fresh_air)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert not is_valid, "co2_on <= co2_off should fail validation"
    
    # Test 1.5: Valid humidifier parameters
    print("\n[Test 1.5] Valid humidifier parameters")
    valid_humidifier = {
        'model': 1,
        'on': 85,
        'off': 90
    }
    is_valid, errors = handler._validate_device_params('humidifier', valid_humidifier)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert is_valid, "Valid parameters should pass validation"
    
    # Test 1.6: Invalid humidifier (on >= off)
    print("\n[Test 1.6] Invalid humidifier (on >= off)")
    invalid_humidifier = {
        'model': 1,
        'on': 90,  # Should be < off
        'off': 85
    }
    is_valid, errors = handler._validate_device_params('humidifier', invalid_humidifier)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert not is_valid, "on >= off should fail validation"
    
    # Test 1.7: Valid grow light parameters
    print("\n[Test 1.7] Valid grow light parameters")
    valid_grow_light = {
        'model': 1,
        'on_mset': 60,
        'off_mset': 60,
        'on_off_1': 1,
        'choose_1': 0,
        'on_off_2': 0,
        'choose_2': 1
    }
    is_valid, errors = handler._validate_device_params('grow_light', valid_grow_light)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {errors}")
    assert is_valid, "Valid parameters should pass validation"
    
    print("\n✅ All validation tests passed!")


def test_correct_invalid_params():
    """Test parameter correction logic"""
    print("\n" + "="*80)
    print("TEST 2: Parameter Correction (_correct_invalid_params)")
    print("="*80)
    
    handler = OutputHandler(static_settings)
    
    # Test 2.1: Clamp out-of-range air cooler values
    print("\n[Test 2.1] Clamp out-of-range air cooler values")
    invalid_air_cooler = {
        'tem_set': 30.0,  # Should clamp to 25.0
        'tem_diff_set': 10.0,  # Should clamp to 5.0
        'cyc_on_time': 100,  # Should clamp to 60
        'cyc_off_time': 0,  # Should clamp to 1
    }
    corrected, warnings = handler._correct_invalid_params('air_cooler', invalid_air_cooler)
    print(f"  Original: {invalid_air_cooler}")
    print(f"  Corrected: {corrected}")
    print(f"  Warnings: {warnings}")
    assert corrected['tem_set'] == 25.0, "tem_set should be clamped to 25.0"
    assert corrected['tem_diff_set'] == 5.0, "tem_diff_set should be clamped to 5.0"
    assert corrected['cyc_on_time'] == 60, "cyc_on_time should be clamped to 60"
    assert corrected['cyc_off_time'] == 1, "cyc_off_time should be clamped to 1"
    assert len(warnings) > 0, "Should have warning messages"
    
    # Test 2.2: Fill missing air cooler fields
    print("\n[Test 2.2] Fill missing air cooler fields")
    incomplete_air_cooler = {
        'tem_set': 15.0
        # Missing other fields
    }
    corrected, warnings = handler._correct_invalid_params('air_cooler', incomplete_air_cooler)
    print(f"  Original: {incomplete_air_cooler}")
    print(f"  Corrected: {corrected}")
    print(f"  Warnings: {warnings}")
    assert 'tem_diff_set' in corrected, "Should fill missing tem_diff_set"
    assert 'cyc_on_time' in corrected, "Should fill missing cyc_on_time"
    assert 'cyc_off_time' in corrected, "Should fill missing cyc_off_time"
    assert 'cyc_on_off' in corrected, "Should fill missing cyc_on_off"
    
    # Test 2.3: Correct fresh air fan CO2 thresholds
    print("\n[Test 2.3] Correct fresh air fan CO2 thresholds")
    invalid_fresh_air = {
        'model': 1,
        'control': 1,
        'co2_on': 800,  # Should be adjusted to > co2_off
        'co2_off': 1000,
        'on': 15,
        'off': 15
    }
    corrected, warnings = handler._correct_invalid_params('fresh_air_fan', invalid_fresh_air)
    print(f"  Original: {invalid_fresh_air}")
    print(f"  Corrected: {corrected}")
    print(f"  Warnings: {warnings}")
    assert corrected['co2_on'] > corrected['co2_off'], "co2_on should be > co2_off"
    
    # Test 2.4: Correct humidifier thresholds
    print("\n[Test 2.4] Correct humidifier thresholds")
    invalid_humidifier = {
        'model': 1,
        'on': 90,  # Should be adjusted to < off
        'off': 85
    }
    corrected, warnings = handler._correct_invalid_params('humidifier', invalid_humidifier)
    print(f"  Original: {invalid_humidifier}")
    print(f"  Corrected: {corrected}")
    print(f"  Warnings: {warnings}")
    assert corrected['on'] < corrected['off'], "on should be < off"
    
    # Test 2.5: Replace invalid enum values
    print("\n[Test 2.5] Replace invalid enum values")
    invalid_enums = {
        'model': 5,  # Invalid, should default to 0
        'on_mset': 60,
        'off_mset': 60
    }
    corrected, warnings = handler._correct_invalid_params('grow_light', invalid_enums)
    print(f"  Original: {invalid_enums}")
    print(f"  Corrected: {corrected}")
    print(f"  Warnings: {warnings}")
    assert corrected['model'] in [0, 1, 2], "Invalid enum should be replaced with valid value"
    
    print("\n✅ All correction tests passed!")


def test_validate_and_format():
    """Test complete validation and formatting"""
    print("\n" + "="*80)
    print("TEST 3: Complete Validation and Formatting (validate_and_format)")
    print("="*80)
    
    handler = OutputHandler(static_settings)
    
    # Test 3.1: Valid complete decision
    print("\n[Test 3.1] Valid complete decision")
    valid_decision = {
        'strategy': {
            'core_objective': '维持温度稳定,控制湿度在适宜范围',
            'priority_ranking': ['温度控制', '湿度控制', 'CO2控制'],
            'key_risk_points': ['温度波动', '湿度过高']
        },
        'device_recommendations': {
            'air_cooler': {
                'tem_set': 15.0,
                'tem_diff_set': 2.0,
                'cyc_on_off': 1,
                'cyc_on_time': 10,
                'cyc_off_time': 10,
                'ar_on_off': 0,
                'hum_on_off': 1,
                'rationale': ['当前温度偏高', '需要降温']
            },
            'fresh_air_fan': {
                'model': 1,
                'control': 1,
                'co2_on': 1200,
                'co2_off': 800,
                'on': 15,
                'off': 15,
                'rationale': ['CO2浓度适中', '使用CO2控制模式']
            },
            'humidifier': {
                'model': 1,
                'on': 85,
                'off': 90,
                'left_right_strategy': '左右侧交替运行',
                'rationale': ['湿度需要维持在85-90%']
            },
            'grow_light': {
                'model': 1,
                'on_mset': 60,
                'off_mset': 60,
                'on_off_1': 1,
                'choose_1': 0,
                'on_off_2': 0,
                'choose_2': 0,
                'on_off_3': 0,
                'choose_3': 0,
                'on_off_4': 0,
                'choose_4': 0,
                'rationale': ['使用白光补光']
            }
        },
        'monitoring_points': {
            'key_time_periods': ['08:00-10:00', '14:00-16:00'],
            'warning_thresholds': {'temperature': 18.0, 'humidity': 95.0},
            'emergency_measures': ['温度超过20度立即降温', '湿度超过95%开启通风']
        }
    }
    
    result = handler.validate_and_format(valid_decision, '611')
    print(f"  Status: {result.status}")
    print(f"  Room ID: {result.room_id}")
    print(f"  Strategy: {result.strategy.core_objective}")
    print(f"  Air Cooler tem_set: {result.device_recommendations.air_cooler.tem_set}")
    print(f"  Warnings: {len(result.metadata.warnings)}")
    print(f"  Errors: {len(result.metadata.errors)}")
    assert result.status == "success", "Valid decision should have success status"
    assert result.room_id == "611", "Room ID should match"
    assert len(result.device_recommendations.air_cooler.rationale) > 0, "Should have rationale"
    
    # Test 3.2: Decision with invalid parameters (should auto-correct)
    print("\n[Test 3.2] Decision with invalid parameters (should auto-correct)")
    invalid_decision = {
        'strategy': {
            'core_objective': '测试无效参数修正',
            'priority_ranking': ['温度控制'],
            'key_risk_points': []
        },
        'device_recommendations': {
            'air_cooler': {
                'tem_set': 30.0,  # Out of range
                'tem_diff_set': 10.0,  # Out of range
                'cyc_on_off': 1,
                'cyc_on_time': 100,  # Out of range
                'cyc_off_time': 10,
                'ar_on_off': 0,
                'hum_on_off': 1,
                'rationale': ['测试']
            },
            'fresh_air_fan': {
                'model': 1,
                'control': 1,
                'co2_on': 800,  # Should be > co2_off
                'co2_off': 1000,
                'on': 15,
                'off': 15,
                'rationale': ['测试']
            },
            'humidifier': {
                'model': 1,
                'on': 90,  # Should be < off
                'off': 85,
                'rationale': ['测试']
            },
            'grow_light': {
                'model': 1,
                'on_mset': 60,
                'off_mset': 60,
                'on_off_1': 1,
                'choose_1': 0,
                'on_off_2': 0,
                'choose_2': 0,
                'on_off_3': 0,
                'choose_3': 0,
                'on_off_4': 0,
                'choose_4': 0,
                'rationale': ['测试']
            }
        },
        'monitoring_points': {
            'key_time_periods': [],
            'warning_thresholds': {},
            'emergency_measures': []
        }
    }
    
    result = handler.validate_and_format(invalid_decision, '612')
    print(f"  Status: {result.status}")
    print(f"  Air Cooler tem_set (corrected): {result.device_recommendations.air_cooler.tem_set}")
    print(f"  Air Cooler cyc_on_time (corrected): {result.device_recommendations.air_cooler.cyc_on_time}")
    print(f"  Fresh Air co2_on (corrected): {result.device_recommendations.fresh_air_fan.co2_on}")
    print(f"  Fresh Air co2_off (corrected): {result.device_recommendations.fresh_air_fan.co2_off}")
    print(f"  Humidifier on (corrected): {result.device_recommendations.humidifier.on}")
    print(f"  Humidifier off (corrected): {result.device_recommendations.humidifier.off}")
    print(f"  Warnings: {len(result.metadata.warnings)}")
    for warning in result.metadata.warnings[:5]:  # Show first 5 warnings
        print(f"    - {warning}")
    
    assert result.status == "success", "Should succeed with corrections"
    assert result.device_recommendations.air_cooler.tem_set <= 25.0, "tem_set should be clamped"
    assert result.device_recommendations.air_cooler.cyc_on_time <= 60, "cyc_on_time should be clamped"
    assert result.device_recommendations.fresh_air_fan.co2_on > result.device_recommendations.fresh_air_fan.co2_off, "co2_on should be > co2_off"
    assert result.device_recommendations.humidifier.on < result.device_recommendations.humidifier.off, "on should be < off"
    assert len(result.metadata.warnings) > 0, "Should have warnings for corrections"
    
    # Test 3.3: Missing structure (should return error)
    print("\n[Test 3.3] Missing structure (should return error)")
    incomplete_decision = {
        'strategy': {
            'core_objective': '测试缺失结构'
        }
        # Missing device_recommendations and monitoring_points
    }
    
    result = handler.validate_and_format(incomplete_decision, '607')
    print(f"  Status: {result.status}")
    print(f"  Errors: {result.metadata.errors}")
    assert result.status == "error", "Missing structure should return error status"
    assert len(result.metadata.errors) > 0, "Should have error messages"
    
    print("\n✅ All validation and formatting tests passed!")


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("OutputHandler Implementation Tests")
    print("Testing tasks 7.1, 7.2, and 7.3")
    print("="*80)
    
    try:
        test_validate_device_params()
        test_correct_invalid_params()
        test_validate_and_format()
        
        print("\n" + "="*80)
        print("✅ ALL TESTS PASSED!")
        print("="*80)
        print("\nImplementation Summary:")
        print("  ✅ Task 7.1: validate_and_format method - COMPLETE")
        print("  ✅ Task 7.2: _validate_device_params method - COMPLETE")
        print("  ✅ Task 7.3: _correct_invalid_params method - COMPLETE")
        print("\nKey Features Implemented:")
        print("  • Structure completeness validation")
        print("  • Device parameter validation against static_config")
        print("  • Enumeration value validation")
        print("  • Numeric range validation")
        print("  • Automatic parameter correction (clamping, enum replacement)")
        print("  • Missing field filling with defaults")
        print("  • Comprehensive warning and error reporting")
        print("  • DecisionOutput formatting")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
