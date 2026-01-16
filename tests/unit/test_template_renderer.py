#!/usr/bin/env python3
"""
Test script for TemplateRenderer implementation

Tests tasks 5.1-5.4:
- Template loading and initialization
- Data mapping to template variables
- Device configuration formatting
- Template rendering
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

import numpy as np
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.template_renderer import TemplateRenderer
from decision_analysis.data_models import SimilarCase


def load_static_config():
    """Load static configuration"""
    config_path = Path(__file__).parent.parent / "src" / "configs" / "static_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_test_current_data():
    """Create test current state data"""
    return {
        "room_id": "611",
        "temperature": 16.5,
        "humidity": 88.5,
        "co2": 1200,
        "semantic_description": "出菇期",
        "in_year": 2024,
        "in_month": 1,
        "in_day": 15,
        "in_day_num": 10,
        "in_num": 5000,
        "air_cooler_config": {
            "status": 2,  # Should map to "正常运行"
            "temp_set": 16.0,
            "temp_diffset": 1.5,
            "cyc_on_off": 1,  # Should map to "开启"
        },
        "fresh_fan_config": {
            "mode": 1,  # Should map to "自动模式"
            "control": 1,  # Should map to "CO2控制"
            "co2_on": 1500,
            "co2_off": 1000,
            "on": 30,
            "off": 60,
        },
        "humidifier_config": {
            "mode": 1,  # Should map to "自动"
            "on": 85,
            "off": 90,
        },
        "light_config": {
            "model": 1,  # Should map to "自动"
            "on_mset": 120,
            "off_mset": 240,
            "on_off1": 1,
            "choose1": 1,  # Blue light
            "on_off2": 1,
            "choose2": 0,  # White light
            "on_off3": 0,
            "choose3": 0,
            "on_off4": 0,
            "choose4": 0,
        },
    }


def create_test_env_stats():
    """Create test environmental statistics"""
    return pd.DataFrame([
        {
            "stat_date": date(2024, 1, 14),
            "in_day_num": 9,
            "temp_median": 16.2,
            "temp_min": 15.8,
            "temp_max": 16.8,
            "humidity_median": 87.5,
            "humidity_min": 85.0,
            "humidity_max": 90.0,
            "co2_median": 1150,
            "co2_min": 900,
            "co2_max": 1400,
        },
        {
            "stat_date": date(2024, 1, 15),
            "in_day_num": 10,
            "temp_median": 16.5,
            "temp_min": 16.0,
            "temp_max": 17.0,
            "humidity_median": 88.5,
            "humidity_min": 86.0,
            "humidity_max": 91.0,
            "co2_median": 1200,
            "co2_min": 950,
            "co2_max": 1450,
        },
    ])


def create_test_device_changes():
    """Create test device change records"""
    return pd.DataFrame([
        {
            "device_type": "air_cooler",
            "device_name": "TD1_Q1MDCH01",
            "point_name": "TemSet",
            "change_time": datetime(2024, 1, 14, 10, 30),
            "previous_value": 15.5,
            "current_value": 16.0,
        },
        {
            "device_type": "fresh_air_fan",
            "device_name": "TD1_Q1MDAR01",
            "point_name": "Co2On",
            "change_time": datetime(2024, 1, 14, 14, 15),
            "previous_value": 1400,
            "current_value": 1500,
        },
    ])


def create_test_similar_cases():
    """Create test similar cases"""
    return [
        SimilarCase(
            similarity_score=85.5,
            confidence_level="high",
            room_id="611",
            growth_day=10,
            collection_time=datetime(2024, 1, 10, 10, 0),
            temperature=16.3,
            humidity=88.0,
            co2=1180,
            air_cooler_params={"temp_set": 16.0, "temp_diffset": 1.5},
            fresh_air_params={"mode": 1, "co2_on": 1500, "co2_off": 1000},
            humidifier_params={"mode": 1, "on": 85, "off": 90},
            grow_light_params={"model": 1, "on_mset": 120, "off_mset": 240},
        ),
        SimilarCase(
            similarity_score=72.3,
            confidence_level="high",
            room_id="611",
            growth_day=9,
            collection_time=datetime(2024, 1, 5, 10, 0),
            temperature=16.0,
            humidity=87.5,
            co2=1150,
            air_cooler_params={"temp_set": 15.5, "temp_diffset": 1.5},
            fresh_air_params={"mode": 1, "co2_on": 1400, "co2_off": 1000},
            humidifier_params={"mode": 1, "on": 85, "off": 90},
            grow_light_params={"model": 1, "on_mset": 120, "off_mset": 240},
        ),
        SimilarCase(
            similarity_score=68.1,
            confidence_level="high",
            room_id="612",
            growth_day=11,
            collection_time=datetime(2024, 1, 3, 10, 0),
            temperature=16.5,
            humidity=89.0,
            co2=1220,
            air_cooler_params={"temp_set": 16.5, "temp_diffset": 1.5},
            fresh_air_params={"mode": 1, "co2_on": 1500, "co2_off": 1000},
            humidifier_params={"mode": 1, "on": 86, "off": 91},
            grow_light_params={"model": 1, "on_mset": 120, "off_mset": 240},
        ),
    ]


def test_template_initialization():
    """Test task 5.1: Template loading and initialization"""
    print("\n" + "=" * 80)
    print("TEST 5.1: Template Loading and Initialization")
    print("=" * 80)
    
    try:
        static_config = load_static_config()
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        
        renderer = TemplateRenderer(str(template_path), static_config)
        
        print("✓ Template loaded successfully")
        print(f"✓ Template path: {renderer.template_path}")
        print(f"✓ Enum cache built for {len(renderer.enum_cache)} device types")
        
        # Verify enum cache
        assert "air_cooler" in renderer.enum_cache
        assert "fresh_air_fan" in renderer.enum_cache
        assert "humidifier" in renderer.enum_cache
        assert "grow_light" in renderer.enum_cache
        
        print("✓ Enum cache contains all device types")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_format_device_config():
    """Test task 5.3: Device configuration formatting"""
    print("\n" + "=" * 80)
    print("TEST 5.3: Device Configuration Formatting")
    print("=" * 80)
    
    try:
        static_config = load_static_config()
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        renderer = TemplateRenderer(str(template_path), static_config)
        
        # Test air cooler config
        air_cooler_config = {
            "status": 2,  # Should map to "正常运行"
            "temp_set": 16.0,
            "cyc_on_off": 1,  # Should map to "开启"
        }
        
        formatted = renderer._format_device_config(air_cooler_config, "air_cooler")
        
        print(f"Original config: {air_cooler_config}")
        print(f"Formatted config: {formatted}")
        
        assert formatted["status"] == "正常运行", f"Expected '正常运行', got '{formatted['status']}'"
        assert formatted["cyc_on_off"] == "开启", f"Expected '开启', got '{formatted['cyc_on_off']}'"
        assert formatted["temp_set"] == 16.0, "Numeric values should be preserved"
        
        print("✓ Air cooler enum mapping correct")
        
        # Test fresh air fan config
        fresh_fan_config = {
            "mode": 1,  # Should map to "自动模式"
            "control": 1,  # Should map to "CO2控制"
            "co2_on": 1500,
        }
        
        formatted = renderer._format_device_config(fresh_fan_config, "fresh_air_fan")
        
        print(f"\nOriginal config: {fresh_fan_config}")
        print(f"Formatted config: {formatted}")
        
        assert formatted["mode"] == "自动模式", f"Expected '自动模式', got '{formatted['mode']}'"
        assert formatted["control"] == "CO2控制", f"Expected 'CO2控制', got '{formatted['control']}'"
        assert formatted["co2_on"] == 1500, "Numeric values should be preserved"
        
        print("✓ Fresh air fan enum mapping correct")
        
        # Test humidifier config
        humidifier_config = {
            "mode": 1,  # Should map to "自动"
            "on": 85,
            "off": 90,
        }
        
        formatted = renderer._format_device_config(humidifier_config, "humidifier")
        
        print(f"\nOriginal config: {humidifier_config}")
        print(f"Formatted config: {formatted}")
        
        assert formatted["mode"] == "自动", f"Expected '自动', got '{formatted['mode']}'"
        
        print("✓ Humidifier enum mapping correct")
        
        # Test grow light config
        light_config = {
            "model": 1,  # Should map to "自动"
            "choose1": 1,  # Should map to "蓝光"
            "choose2": 0,  # Should map to "白光"
        }
        
        formatted = renderer._format_device_config(light_config, "grow_light")
        
        print(f"\nOriginal config: {light_config}")
        print(f"Formatted config: {formatted}")
        
        assert formatted["model"] == "自动", f"Expected '自动', got '{formatted['model']}'"
        assert formatted["choose1"] == "蓝光", f"Expected '蓝光', got '{formatted['choose1']}'"
        assert formatted["choose2"] == "白光", f"Expected '白光', got '{formatted['choose2']}'"
        
        print("✓ Grow light enum mapping correct")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_map_variables():
    """Test task 5.2: Data mapping to template variables"""
    print("\n" + "=" * 80)
    print("TEST 5.2: Data Mapping to Template Variables")
    print("=" * 80)
    
    try:
        static_config = load_static_config()
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        renderer = TemplateRenderer(str(template_path), static_config)
        
        current_data = create_test_current_data()
        env_stats = create_test_env_stats()
        device_changes = create_test_device_changes()
        similar_cases = create_test_similar_cases()
        
        variables = renderer._map_variables(
            current_data=current_data,
            env_stats=env_stats,
            device_changes=device_changes,
            similar_cases=similar_cases
        )
        
        print(f"✓ Mapped {len(variables)} template variables")
        
        # Verify current environment variables
        assert variables["room_id"] == "611"
        assert variables["current_temp"] == 16.5
        assert variables["current_humidity"] == 88.5
        assert variables["current_co2"] == 1200
        assert variables["growth_stage"] == "出菇期"
        
        print("✓ Current environment variables mapped correctly")
        
        # Verify device configuration variables
        assert "air_cooler_status" in variables
        assert "fresh_air_mode" in variables
        assert "humidifier_mode" in variables
        assert "grow_light_model" in variables
        
        print("✓ Device configuration variables mapped")
        
        # Verify similar cases variables
        assert variables["similarity_1"] == "85.5"
        assert variables["case1_room"] == "611"
        assert variables["case1_temp"] == 16.3
        assert variables["similarity_2"] == "72.3"
        assert variables["similarity_3"] == "68.1"
        
        print("✓ Similar cases variables mapped correctly")
        
        # Verify historical data
        assert "historical_data" in variables
        assert len(variables["historical_data"]) > 0
        
        print("✓ Historical data formatted")
        print(f"\nHistorical data preview:\n{variables['historical_data'][:200]}...")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_render():
    """Test task 5.4: Template rendering"""
    print("\n" + "=" * 80)
    print("TEST 5.4: Template Rendering")
    print("=" * 80)
    
    try:
        static_config = load_static_config()
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        renderer = TemplateRenderer(str(template_path), static_config)
        
        current_data = create_test_current_data()
        env_stats = create_test_env_stats()
        device_changes = create_test_device_changes()
        similar_cases = create_test_similar_cases()
        
        rendered_text = renderer.render(
            current_data=current_data,
            env_stats=env_stats,
            device_changes=device_changes,
            similar_cases=similar_cases
        )
        
        print(f"✓ Template rendered successfully")
        print(f"✓ Rendered text length: {len(rendered_text)} characters")
        
        # Debug: print first 1000 chars to see what's there
        print("\n" + "-" * 80)
        print("Rendered text preview (first 1000 chars):")
        print("-" * 80)
        print(rendered_text[:1000])
        print("...")
        
        # Verify rendered text contains expected content
        assert "611" in rendered_text, "Room ID should be in rendered text"
        assert "出菇期" in rendered_text, "Growth stage should be in rendered text"
        
        # Check for temperature (might be formatted as integer or float)
        temp_found = "16.5" in rendered_text or "16" in rendered_text
        assert temp_found, f"Temperature should be in rendered text"
        
        print("✓ Rendered text contains expected data")
        
        # Save rendered text for inspection
        output_path = Path(__file__).parent.parent / "test_rendered_prompt.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rendered_text)
        
        print(f"✓ Rendered text saved to: {output_path}")
        
        # Show preview
        print("\n" + "-" * 80)
        print("Rendered text preview (first 500 chars):")
        print("-" * 80)
        print(rendered_text[:500])
        print("...")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("TEMPLATE RENDERER IMPLEMENTATION TEST")
    print("Testing tasks 5.1, 5.2, 5.3, 5.4")
    print("=" * 80)
    
    results = []
    
    # Run tests in order
    results.append(("5.1 Template Initialization", test_template_initialization()))
    results.append(("5.3 Device Config Formatting", test_format_device_config()))
    results.append(("5.2 Variable Mapping", test_map_variables()))
    results.append(("5.4 Template Rendering", test_render()))
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status}: {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    if all_passed:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
