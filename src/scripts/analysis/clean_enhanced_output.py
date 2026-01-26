#!/usr/bin/env python3
"""
Clean Enhanced Decision Analysis Output

This script cleans and processes the enhanced decision analysis output file to:
1. Remove invalid content (image quality scores, string representations)
2. Create proper monitoring_points structure
3. Populate with real-time IoT data
4. Generate clean, compatible JSON output

Usage:
    python scripts/analysis/clean_enhanced_output.py
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from loguru import logger
from utils.realtime_data_populator import populate_monitoring_points_old_fields


def extract_parameter_adjustments_from_string(enhanced_decision_str: str) -> Dict:
    """Extract parameter adjustments from the string representation"""
    device_recommendations = {}
    
    # Define device types and their parameters
    device_configs = {
        "air_cooler": ["tem_set", "tem_diff_set", "cyc_on_off", "cyc_on_time", "cyc_off_time", "ar_on_off", "hum_on_off"],
        "fresh_air_fan": ["model", "control", "co2_on", "co2_off", "on", "off"],
        "humidifier": ["model", "on", "off"],
        "grow_light": ["model", "on_mset", "off_mset", "on_off_1", "choose_1", "on_off_2", "choose_2", "on_off_3", "choose_3", "on_off_4", "choose_4"]
    }
    
    for device_type, params in device_configs.items():
        device_recommendations[device_type] = {}
        
        for param in params:
            # Extract parameter adjustment information using regex
            pattern = f"{param}=ParameterAdjustment\\(current_value=([^,]+), recommended_value=([^,]+), action='([^']+)'.*?priority='([^']+)'"
            match = re.search(pattern, enhanced_decision_str)
            
            if match:
                current_value = float(match.group(1)) if match.group(1) != 'None' else 0
                recommended_value = float(match.group(2)) if match.group(2) != 'None' else 0
                action = match.group(3)
                priority = match.group(4)
                
                device_recommendations[device_type][param] = {
                    "current_value": current_value,
                    "recommended_value": recommended_value,
                    "action": action,
                    "priority": priority
                }
            else:
                # Default values
                device_recommendations[device_type][param] = {
                    "current_value": 0,
                    "recommended_value": 0,
                    "action": "maintain",
                    "priority": "low"
                }
    
    return device_recommendations


def create_monitoring_points_config(room_id: str, device_recommendations: Dict) -> Dict:
    """Create monitoring points configuration from device recommendations"""
    
    # Load template configuration
    config_path = Path(__file__).parent.parent.parent / "src" / "configs" / "monitoring_points_config.json"
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            template = json.load(f)
    except Exception as e:
        logger.error(f"Error loading template config: {e}")
        return create_empty_config(room_id)
    
    # Update room_id
    template["room_id"] = room_id
    
    # Update device names for room 607
    template = update_device_names_for_room(template, room_id)
    
    # Update monitoring points based on recommendations
    template = update_monitoring_points(template, device_recommendations)
    
    return template


def update_device_names_for_room(template: Dict, room_id: str) -> Dict:
    """Update device names and aliases for specific room"""
    devices = template.get("devices", {})
    
    for device_type, device_list in devices.items():
        for device in device_list:
            # Update device names for room 607
            if "device_name" in device:
                device_name = device["device_name"]
                if "Q1MD" in device_name:
                    # For room 607, we might need different naming convention
                    # Keep the original pattern but update room reference
                    device["device_name"] = device_name.replace("TD1_Q1MD", f"TD1_Q{room_id}MD")
            
            if "device_alias" in device:
                device_alias = device["device_alias"]
                if "_611" in device_alias:
                    device["device_alias"] = device_alias.replace("_611", f"_{room_id}")
    
    return template


def update_monitoring_points(template: Dict, device_recommendations: Dict) -> Dict:
    """Update monitoring points with recommendations"""
    devices = template.get("devices", {})
    
    # Alias mappings for different device types
    alias_mappings = {
        "air_cooler": {
            "temp_set": "tem_set",
            "temp_diffset": "tem_diff_set",
            "air_on_off": "ar_on_off",
            "cyc_on_off": "cyc_on_off",
            "cyc_on_time": "cyc_on_time",
            "cyc_off_time": "cyc_off_time",
            "hum_on_off": "hum_on_off"
        },
        "fresh_air_fan": {
            "mode": "model",
            "control": "control",
            "co2_on": "co2_on",
            "co2_off": "co2_off",
            "on": "on",
            "off": "off"
        },
        "humidifier": {
            "mode": "model",
            "on": "on",
            "off": "off"
        },
        "grow_light": {
            "model": "model",
            "on_mset": "on_mset",
            "off_mset": "off_mset",
            "on_off1": "on_off_1",
            "on_off2": "on_off_2",
            "on_off3": "on_off_3",
            "on_off4": "on_off_4",
            "choose1": "choose_1",
            "choose2": "choose_2",
            "choose3": "choose_3",
            "choose4": "choose_4"
        }
    }
    
    for device_type, device_list in devices.items():
        device_recs = device_recommendations.get(device_type, {})
        mappings = alias_mappings.get(device_type, {})
        
        for device in device_list:
            for point in device.get("point_list", []):
                point_alias = point.get("point_alias")
                
                # Find corresponding recommendation
                rec_key = mappings.get(point_alias, point_alias)
                recommendation = device_recs.get(rec_key)
                
                if recommendation:
                    # Update point with recommendation
                    point["old"] = recommendation["current_value"]  # Will be updated by real-time data
                    point["new"] = recommendation["recommended_value"]
                    point["change"] = (recommendation["action"] == "adjust")
                    point["level"] = map_priority_to_level(recommendation["priority"])
                else:
                    # Use defaults
                    point["old"] = 0
                    point["new"] = 0
                    point["change"] = False
                    point["level"] = "medium"
    
    return template


def map_priority_to_level(priority: str) -> str:
    """Map priority to level"""
    mapping = {
        "critical": "high",
        "high": "high",
        "medium": "medium",
        "low": "low"
    }
    return mapping.get(priority, "medium")


def create_empty_config(room_id: str) -> Dict:
    """Create empty monitoring configuration"""
    return {
        "room_id": room_id,
        "devices": {
            "air_cooler": [],
            "fresh_air_fan": [],
            "humidifier": [],
            "grow_light": []
        },
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "cleaned_enhanced_decision_analysis",
            "total_points": 0
        }
    }


def main():
    """Main processing function"""
    logger.info("Starting enhanced output cleaning process")
    
    # Input and output files
    input_file = Path("output/enhanced_decision_analysis_607_20260123_100928.json")
    output_file = Path("output/cleaned_monitoring_points_607_20260123_100928.json")
    
    try:
        # Load input file
        logger.info(f"Loading input file: {input_file}")
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract room_id
        room_id = data.get("metadata", {}).get("room_id", "607")
        logger.info(f"Processing room_id: {room_id}")
        
        # Extract device recommendations from string representation
        enhanced_decision_str = data.get("enhanced_decision", "")
        device_recommendations = extract_parameter_adjustments_from_string(enhanced_decision_str)
        
        logger.info(f"Extracted recommendations for devices: {list(device_recommendations.keys())}")
        
        # Create monitoring points configuration
        monitoring_config = create_monitoring_points_config(room_id, device_recommendations)
        
        # Populate with real-time data
        logger.info("Populating with real-time IoT data...")
        monitoring_config, stats = populate_monitoring_points_old_fields(monitoring_config, room_id)
        
        logger.info(f"Real-time data population stats: {stats}")
        
        # Add metadata
        monitoring_config["metadata"] = {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "cleaned_enhanced_decision_analysis",
            "processing_stats": stats,
            "original_file": str(input_file)
        }
        
        # Save cleaned output
        logger.info(f"Saving cleaned output to: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(monitoring_config, f, ensure_ascii=False, indent=2)
        
        # Print summary
        total_devices = sum(len(devices) for devices in monitoring_config["devices"].values())
        total_points = sum(
            len(device.get("point_list", []))
            for device_list in monitoring_config["devices"].values()
            for device in device_list
        )
        
        print("✅ Enhanced output cleaning completed successfully")
        print(f"📊 Summary:")
        print(f"   - Room ID: {room_id}")
        print(f"   - Total devices: {total_devices}")
        print(f"   - Total monitoring points: {total_points}")
        print(f"   - Real-time data success rate: {stats.get('success_rate', 0):.1f}%")
        print(f"   - Output file: {output_file}")
        
        logger.info("Enhanced output cleaning completed successfully")
        
    except Exception as e:
        logger.error(f"Error during processing: {e}")
        print(f"❌ Processing failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())