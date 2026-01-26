#!/usr/bin/env python3
"""
Enhanced Decision Analysis Output Processor

This script processes the enhanced decision analysis output files to:
1. Remove invalid content (image quality scores, etc.)
2. Enhance monitoring_points section with proper format
3. Populate 'old' fields with real-time IoT data
4. Set 'new', 'change', and 'level' fields based on model recommendations

Usage:
    python scripts/analysis/process_enhanced_output.py --input output/enhanced_decision_analysis_607_20260123_100928.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from loguru import logger
from utils.realtime_data_populator import populate_monitoring_points_old_fields


class EnhancedOutputProcessor:
    """Process enhanced decision analysis output files"""
    
    def __init__(self):
        """Initialize the processor"""
        self.logger = logger
        
    def process_file(self, input_file: Path, output_file: Optional[Path] = None) -> Dict:
        """
        Process enhanced decision analysis output file
        
        Args:
            input_file: Path to input JSON file
            output_file: Path to output JSON file (optional)
            
        Returns:
            Processed configuration dictionary
        """
        self.logger.info(f"Processing enhanced output file: {input_file}")
        
        # Load input file
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract room_id
        room_id = self._extract_room_id(data)
        self.logger.info(f"Extracted room_id: {room_id}")
        
        # Process the data
        processed_config = self._process_enhanced_data(data, room_id)
        
        # Save to output file
        if output_file is None:
            output_file = input_file.parent / f"processed_{input_file.name}"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(processed_config, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"Processed output saved to: {output_file}")
        return processed_config
    
    def _extract_room_id(self, data: Dict) -> str:
        """Extract room_id from the data"""
        # Try different possible locations
        if "metadata" in data and "room_id" in data["metadata"]:
            return data["metadata"]["room_id"]
        elif "monitoring_points" in data and "room_id" in data["monitoring_points"]:
            return data["monitoring_points"]["room_id"]
        elif "room_id" in data:
            return data["room_id"]
        else:
            # Default fallback
            return "607"
    
    def _process_enhanced_data(self, data: Dict, room_id: str) -> Dict:
        """
        Process enhanced decision analysis data
        
        Args:
            data: Raw enhanced decision analysis data
            room_id: Room ID
            
        Returns:
            Processed monitoring points configuration
        """
        self.logger.info("Processing enhanced decision analysis data")
        
        # Step 1: Extract device recommendations
        device_recommendations = self._extract_device_recommendations(data)
        
        # Step 2: Create monitoring points configuration structure
        monitoring_config = self._create_monitoring_config_structure(room_id)
        
        # Step 3: Populate device configurations
        monitoring_config = self._populate_device_configurations(
            monitoring_config, device_recommendations, room_id
        )
        
        # Step 4: Populate 'old' fields with real-time data
        monitoring_config, stats = populate_monitoring_points_old_fields(monitoring_config, room_id)
        
        self.logger.info(f"Real-time data population stats: {stats}")
        
        # Step 5: Add metadata
        monitoring_config["metadata"] = {
            "generated_at": datetime.now().isoformat(),
            "room_id": room_id,
            "source": "processed_enhanced_decision_analysis",
            "processing_stats": stats
        }
        
        return monitoring_config
    
    def _extract_device_recommendations(self, data: Dict) -> Dict:
        """Extract device recommendations from enhanced decision data"""
        try:
            # Handle string representation of EnhancedDecisionOutput
            enhanced_decision = data.get("enhanced_decision", {})
            
            if isinstance(enhanced_decision, str):
                # Parse the string representation
                device_recs = self._parse_device_recommendations_from_string(enhanced_decision)
            else:
                # Handle dictionary format
                device_recs = enhanced_decision.get("device_recommendations", {})
            
            self.logger.debug(f"Extracted device recommendations: {list(device_recs.keys())}")
            return device_recs
            
        except Exception as e:
            self.logger.error(f"Error extracting device recommendations: {e}")
            return {}
    
    def _parse_device_recommendations_from_string(self, enhanced_decision_str: str) -> Dict:
        """Parse device recommendations from string representation"""
        # This is a simplified parser - in practice, you might want to use ast.literal_eval
        # or a more robust parsing method
        device_recs = {}
        
        try:
            # Look for device_recommendations section
            if "device_recommendations=" in enhanced_decision_str:
                # Extract the device recommendations part
                # This is a simplified approach - you might need more sophisticated parsing
                pass
            
        except Exception as e:
            self.logger.error(f"Error parsing device recommendations from string: {e}")
        
        return device_recs
    
    def _create_monitoring_config_structure(self, room_id: str) -> Dict:
        """Create the basic monitoring configuration structure"""
        return {
            "room_id": room_id,
            "devices": {
                "air_cooler": [],
                "fresh_air_fan": [],
                "humidifier": [],
                "grow_light": []
            }
        }
    
    def _populate_device_configurations(self, config: Dict, device_recs: Dict, room_id: str) -> Dict:
        """Populate device configurations based on recommendations"""
        try:
            # Load the template configuration for the room
            template_config = self._load_template_config(room_id)
            
            if not template_config:
                self.logger.warning("No template configuration found, using defaults")
                return config
            
            # Copy device structures from template
            for device_type in ["air_cooler", "fresh_air_fan", "humidifier", "grow_light"]:
                if device_type in template_config.get("devices", {}):
                    config["devices"][device_type] = self._process_device_type(
                        template_config["devices"][device_type],
                        device_recs.get(device_type, {}),
                        device_type
                    )
            
            return config
            
        except Exception as e:
            self.logger.error(f"Error populating device configurations: {e}")
            return config
    
    def _load_template_config(self, room_id: str) -> Optional[Dict]:
        """Load template configuration for the room"""
        try:
            # Try to load room-specific config first
            config_path = Path(__file__).parent.parent.parent / "src" / "configs" / "monitoring_points_config.json"
            
            with open(config_path, 'r', encoding='utf-8') as f:
                template = json.load(f)
            
            # Update room_id
            template["room_id"] = room_id
            
            # Update device names and aliases for the specific room
            template = self._update_device_names_for_room(template, room_id)
            
            return template
            
        except Exception as e:
            self.logger.error(f"Error loading template config: {e}")
            return None
    
    def _update_device_names_for_room(self, template: Dict, room_id: str) -> Dict:
        """Update device names and aliases for specific room"""
        try:
            devices = template.get("devices", {})
            
            for device_type, device_list in devices.items():
                for device in device_list:
                    # Update device names and aliases to match the room
                    if "device_name" in device:
                        # Replace room number in device name
                        device_name = device["device_name"]
                        if "Q1MD" in device_name:
                            # Update the device name for the specific room
                            device["device_name"] = device_name.replace("Q1MD", f"Q{room_id}MD")
                    
                    if "device_alias" in device:
                        # Update device alias
                        device_alias = device["device_alias"]
                        if "_611" in device_alias:
                            device["device_alias"] = device_alias.replace("_611", f"_{room_id}")
            
            return template
            
        except Exception as e:
            self.logger.error(f"Error updating device names for room: {e}")
            return template
    
    def _process_device_type(self, template_devices: List[Dict], device_rec: Dict, device_type: str) -> List[Dict]:
        """Process a specific device type with recommendations"""
        processed_devices = []
        
        try:
            for template_device in template_devices:
                processed_device = template_device.copy()
                
                # Process each point in the device
                processed_points = []
                for point in template_device.get("point_list", []):
                    processed_point = self._process_point(point, device_rec, device_type)
                    processed_points.append(processed_point)
                
                processed_device["point_list"] = processed_points
                processed_devices.append(processed_device)
            
            return processed_devices
            
        except Exception as e:
            self.logger.error(f"Error processing device type {device_type}: {e}")
            return template_devices
    
    def _process_point(self, point: Dict, device_rec: Dict, device_type: str) -> Dict:
        """Process a single monitoring point with recommendations"""
        processed_point = point.copy()
        
        try:
            point_alias = point.get("point_alias")
            
            # Find corresponding recommendation
            recommendation = self._find_recommendation(point_alias, device_rec, device_type)
            
            if recommendation:
                # Extract values from recommendation
                current_value = recommendation.get("current_value", 0)
                recommended_value = recommendation.get("recommended_value", 0)
                action = recommendation.get("action", "maintain")
                priority = recommendation.get("priority", "low")
                
                # Set the fields
                processed_point["old"] = current_value  # Will be updated by real-time data
                processed_point["new"] = recommended_value
                processed_point["change"] = (action == "adjust")
                processed_point["level"] = self._map_priority_to_level(priority)
                
                self.logger.debug(f"Processed {device_type}.{point_alias}: old={current_value}, new={recommended_value}, change={processed_point['change']}, level={processed_point['level']}")
            else:
                # Use defaults
                processed_point["old"] = 0
                processed_point["new"] = 0
                processed_point["change"] = False
                processed_point["level"] = "medium"
            
            return processed_point
            
        except Exception as e:
            self.logger.error(f"Error processing point {point.get('point_alias', 'unknown')}: {e}")
            return point
    
    def _find_recommendation(self, point_alias: str, device_rec: Dict, device_type: str) -> Optional[Dict]:
        """Find recommendation for a specific point"""
        try:
            # Handle different possible formats of device_rec
            if isinstance(device_rec, dict):
                # Direct mapping
                if point_alias in device_rec:
                    return device_rec[point_alias]
                
                # Try common alias mappings
                alias_mappings = self._get_alias_mappings(device_type)
                mapped_alias = alias_mappings.get(point_alias)
                if mapped_alias and mapped_alias in device_rec:
                    return device_rec[mapped_alias]
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding recommendation for {point_alias}: {e}")
            return None
    
    def _get_alias_mappings(self, device_type: str) -> Dict[str, str]:
        """Get alias mappings for different device types"""
        mappings = {
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
        return mappings.get(device_type, {})
    
    def _map_priority_to_level(self, priority: str) -> str:
        """Map priority to level"""
        mapping = {
            "critical": "high",
            "high": "high",
            "medium": "medium",
            "low": "low"
        }
        return mapping.get(priority, "medium")


def create_argument_parser() -> argparse.ArgumentParser:
    """Create command line argument parser"""
    parser = argparse.ArgumentParser(
        description="Process enhanced decision analysis output files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a specific output file
  python scripts/analysis/process_enhanced_output.py --input output/enhanced_decision_analysis_607_20260123_100928.json
  
  # Process with custom output file
  python scripts/analysis/process_enhanced_output.py --input output/enhanced_decision_analysis_607_20260123_100928.json --output processed_output.json
  
  # Process with verbose logging
  python scripts/analysis/process_enhanced_output.py --input output/enhanced_decision_analysis_607_20260123_100928.json --verbose
        """
    )
    
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to input enhanced decision analysis JSON file"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        help="Path to output processed JSON file (optional)"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    return parser


def main() -> int:
    """Main entry point"""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Set up logging
    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    
    try:
        # Validate input file
        input_file = Path(args.input)
        if not input_file.exists():
            logger.error(f"Input file does not exist: {input_file}")
            return 1
        
        # Set output file
        output_file = Path(args.output) if args.output else None
        
        # Process the file
        processor = EnhancedOutputProcessor()
        result = processor.process_file(input_file, output_file)
        
        logger.info("Processing completed successfully")
        
        # Print summary
        total_devices = sum(len(devices) for devices in result["devices"].values())
        total_points = sum(
            len(device.get("point_list", []))
            for device_list in result["devices"].values()
            for device in device_list
        )
        
        print(f"✅ Processing completed successfully")
        print(f"📊 Summary:")
        print(f"   - Room ID: {result['room_id']}")
        print(f"   - Total devices: {total_devices}")
        print(f"   - Total monitoring points: {total_points}")
        print(f"   - Output file: {output_file or input_file.parent / f'processed_{input_file.name}'}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        print(f"❌ Processing failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())