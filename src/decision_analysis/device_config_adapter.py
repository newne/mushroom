"""
Device Configuration Adapter Module

This module provides functionality to adapt decision analysis output to match
the actual device configurations defined in monitoring_points_config.json.
It ensures that generated recommendations only include devices and control points
that exist in the configuration files.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger


class DeviceConfigAdapter:
    """
    Adapter for matching decision output with actual device configurations
    
    This class loads device configurations from monitoring_points_config.json
    and provides methods to validate and filter decision recommendations
    to ensure they match the actual controllable devices and points.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize device configuration adapter
        
        Args:
            config_path: Path to monitoring_points_config.json file
        """
        if config_path is None:
            # Default path relative to src directory
            config_path = Path(__file__).parent.parent / "configs" / "monitoring_points_config.json"
        
        self.config_path = Path(config_path)
        self.device_configs = {}
        self.room_devices = {}
        self.device_points = {}
        
        self._load_device_configs()
        logger.info(f"[DeviceConfigAdapter] Initialized with config: {self.config_path}")
    
    def _load_device_configs(self) -> None:
        """Load device configurations from JSON file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Extract room_id and device configurations
            room_id = config_data.get("room_id", "unknown")
            devices = config_data.get("devices", {})
            
            # Parse device configurations from new structure
            for device_type, device_list in devices.items():
                for device_config in device_list:
                    device_name = device_config.get("device_name")
                    device_alias = device_config.get("device_alias")
                    point_list = device_config.get("point_list", [])
                    
                    if device_name and device_type:
                        # Store device configuration
                        self.device_configs[device_name] = {
                            "device_type": device_type,
                            "device_alias": device_alias,
                            "point_list": point_list,
                            "room_id": room_id
                        }
                        
                        # Build room -> devices mapping
                        if room_id not in self.room_devices:
                            self.room_devices[room_id] = {}
                        
                        if device_type not in self.room_devices[room_id]:
                            self.room_devices[room_id][device_type] = []
                        
                        self.room_devices[room_id][device_type].append({
                            "device_name": device_name,
                            "device_alias": device_alias,
                            "point_list": point_list
                        })
                        
                        # Build device_type -> points mapping
                        if device_type not in self.device_points:
                            self.device_points[device_type] = set()
                        
                        for point in point_list:
                            point_alias = point.get("point_alias")
                            if point_alias:
                                self.device_points[device_type].add(point_alias)
            
            logger.info(f"[DeviceConfigAdapter] Loaded {len(self.device_configs)} devices for room {room_id}")
            logger.debug(f"[DeviceConfigAdapter] Device types: {list(self.device_points.keys())}")
            
        except Exception as e:
            logger.error(f"[DeviceConfigAdapter] Failed to load device config: {e}")
            raise
    
    def get_supported_device_types(self) -> List[str]:
        """Get list of supported device types"""
        return list(self.device_points.keys())
    
    def get_supported_points(self, device_type: str) -> Set[str]:
        """Get supported control points for a device type"""
        return self.device_points.get(device_type, set())
    
    def get_room_devices(self, room_id: str) -> Dict[str, List[Dict]]:
        """Get devices configured for a specific room"""
        return self.room_devices.get(room_id, {})
    
    def validate_device_recommendation(self, device_type: str, recommendations: Dict) -> Tuple[Dict, List[str]]:
        """
        Validate and filter device recommendations against configuration
        
        Args:
            device_type: Type of device (air_cooler, fresh_air_fan, etc.)
            recommendations: Dictionary of parameter recommendations
            
        Returns:
            Tuple of (filtered_recommendations, warnings)
        """
        warnings = []
        filtered_recommendations = {}
        
        if device_type not in self.device_points:
            warning = f"Device type '{device_type}' not found in configuration"
            logger.warning(f"[DeviceConfigAdapter] {warning}")
            warnings.append(warning)
            return {}, warnings
        
        supported_points = self.device_points[device_type]
        
        for point_alias, value in recommendations.items():
            if point_alias in supported_points:
                filtered_recommendations[point_alias] = value
            else:
                warning = f"Control point '{point_alias}' not supported for device type '{device_type}'"
                logger.debug(f"[DeviceConfigAdapter] {warning}")
                warnings.append(warning)
        
        logger.debug(f"[DeviceConfigAdapter] Filtered {device_type}: {len(filtered_recommendations)}/{len(recommendations)} points")
        
        return filtered_recommendations, warnings
    
    def get_point_config(self, device_type: str, point_alias: str) -> Optional[Dict]:
        """
        Get configuration for a specific control point
        
        Args:
            device_type: Type of device
            point_alias: Alias of the control point
            
        Returns:
            Point configuration dictionary or None if not found
        """
        for device_name, device_config in self.device_configs.items():
            if device_config["device_type"] == device_type:
                for point in device_config["point_list"]:
                    if point.get("point_alias") == point_alias:
                        return point
        
        return None
    
    def create_monitoring_point_output(self, device_type: str, point_alias: str, 
                                     old_value: float, new_value: float, 
                                     priority_level: str = "medium") -> Optional[Dict]:
        """
        Create monitoring point output in the format expected by monitoring_points_config.json
        
        Args:
            device_type: Type of device
            point_alias: Alias of the control point
            old_value: Current/old value
            new_value: Recommended new value
            priority_level: Priority level (high/medium/low)
            
        Returns:
            Monitoring point output dictionary or None if point not found
        """
        point_config = self.get_point_config(device_type, point_alias)
        if not point_config:
            return None
        
        # Determine if change is needed
        change_needed = False
        threshold = point_config.get("threshold")
        
        if threshold is not None:
            # For analog values, check if change exceeds threshold
            change_needed = abs(new_value - old_value) >= threshold
        else:
            # For digital/enum values, any difference is a change
            change_needed = new_value != old_value
        
        return {
            "device_type": device_type,
            "point_alias": point_alias,
            "point_name": point_config.get("point_name"),
            "remark": point_config.get("remark"),
            "change_type": point_config.get("change_type"),
            "threshold": threshold,
            "enum_mapping": point_config.get("enum_mapping"),
            "change": change_needed,
            "old": old_value,
            "new": new_value,
            "level": priority_level
        }
    
    def adapt_decision_output(self, decision_output: Dict, room_id: str) -> Tuple[Dict, List[str]]:
        """
        Adapt complete decision output to match device configuration
        
        Args:
            decision_output: Raw decision output from LLM
            room_id: Target room ID
            
        Returns:
            Tuple of (adapted_output, warnings)
        """
        warnings = []
        adapted_output = decision_output.copy()
        
        # Get device recommendations from output
        device_recommendations = decision_output.get("device_recommendations", {})
        adapted_recommendations = {}
        
        # Process each device type
        for device_type, recommendations in device_recommendations.items():
            if isinstance(recommendations, dict):
                filtered_recs, device_warnings = self.validate_device_recommendation(
                    device_type, recommendations
                )
                
                if filtered_recs:
                    adapted_recommendations[device_type] = filtered_recs
                
                warnings.extend(device_warnings)
        
        # Update adapted output
        adapted_output["device_recommendations"] = adapted_recommendations
        
        # Add configuration metadata
        adapted_output["device_config_metadata"] = {
            "config_source": str(self.config_path),
            "supported_device_types": self.get_supported_device_types(),
            "room_id": room_id,
            "adaptation_warnings": len(warnings)
        }
        
        logger.info(f"[DeviceConfigAdapter] Adapted decision output for room {room_id}: "
                   f"{len(adapted_recommendations)} device types, {len(warnings)} warnings")
        
        return adapted_output, warnings


def create_device_config_adapter(config_path: Optional[str] = None) -> DeviceConfigAdapter:
    """
    Factory function to create DeviceConfigAdapter instance
    
    Args:
        config_path: Optional path to configuration file
        
    Returns:
        DeviceConfigAdapter instance
    """
    return DeviceConfigAdapter(config_path)