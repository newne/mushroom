"""
Real-time Data Populator Module

This module provides functionality to dynamically populate the 'old' fields in monitoring points
configuration with real-time data from the database. It uses pandas vectorized operations
for efficient data matching and population.

Key Features:
- Real-time data fetching from PostgreSQL database
- Efficient pandas-based data matching using device_name and point_name combinations
- Safe type conversion with error handling
- Comprehensive logging and statistics reporting
- Performance optimization with vectorized operations
- Graceful degradation when data is unavailable
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

import pandas as pd
from loguru import logger

from global_const.global_const import create_get_data, static_settings
from utils.dataframe_utils import get_all_device_configs


class RealtimeDataPopulator:
    """
    Real-time data populator for monitoring points configuration
    
    This class handles the dynamic population of 'old' fields in monitoring points
    configuration by fetching real-time data from the database and matching it
    with device_name and point_name combinations.
    """
    
    def __init__(self):
        """Initialize the real-time data populator"""
        self.get_data = create_get_data()
        self.static_settings = static_settings
        
        # Performance tracking
        self._stats = {
            "total_points": 0,
            "successful_matches": 0,
            "failed_matches": 0,
            "type_conversions": 0,
            "default_values_used": 0,
            "query_time": 0.0,
            "processing_time": 0.0
        }
        
        logger.debug("[RealtimeDataPopulator] Initialized real-time data populator")
    
    def populate_old_fields(
        self,
        monitoring_points_config: Dict,
        room_id: str,
        time_window_minutes: int = 10
    ) -> Tuple[Dict, Dict]:
        """
        Populate 'old' fields in monitoring points configuration with real-time data
        
        Args:
            monitoring_points_config: Monitoring points configuration dictionary
            room_id: Room ID for filtering data
            time_window_minutes: Time window for fetching recent data (default: 10 minutes)
            
        Returns:
            Tuple of (updated_config, statistics)
        """
        start_time = time.time()
        
        logger.info(f"[RealtimeDataPopulator] Starting real-time data population for room {room_id}")
        logger.debug(f"[RealtimeDataPopulator] Time window: {time_window_minutes} minutes")
        
        # Reset statistics
        self._reset_stats()
        
        try:
            # Step 1: Extract device-point combinations from monitoring config
            device_point_combinations = self._extract_device_point_combinations(monitoring_points_config)
            self._stats["total_points"] = len(device_point_combinations)
            
            if device_point_combinations.empty:
                logger.warning("[RealtimeDataPopulator] No device-point combinations found in config")
                return monitoring_points_config, self._get_stats()
            
            logger.info(f"[RealtimeDataPopulator] Found {len(device_point_combinations)} device-point combinations")
            
            # Step 2: Fetch real-time data from database
            realtime_data = self._fetch_realtime_data(device_point_combinations, time_window_minutes)
            
            if realtime_data.empty:
                logger.warning("[RealtimeDataPopulator] No real-time data available, using default values")
                updated_config = self._apply_default_values(monitoring_points_config)
                return updated_config, self._get_stats()
            
            logger.info(f"[RealtimeDataPopulator] Fetched {len(realtime_data)} real-time data records")
            
            # Step 3: Match and populate data using pandas vectorized operations
            updated_config = self._populate_with_realtime_data(
                monitoring_points_config, 
                realtime_data, 
                device_point_combinations
            )
            
            # Step 4: Calculate final statistics
            self._stats["processing_time"] = time.time() - start_time
            
            logger.info(
                f"[RealtimeDataPopulator] Data population completed in {self._stats['processing_time']:.2f}s - "
                f"Success: {self._stats['successful_matches']}/{self._stats['total_points']}, "
                f"Failed: {self._stats['failed_matches']}, "
                f"Defaults: {self._stats['default_values_used']}"
            )
            
            return updated_config, self._get_stats()
            
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error during data population: {e}")
            # Return original config with error statistics
            self._stats["processing_time"] = time.time() - start_time
            self._stats["failed_matches"] = self._stats["total_points"]
            return monitoring_points_config, self._get_stats()
    
    def _extract_device_point_combinations(self, config: Dict) -> pd.DataFrame:
        """
        Extract device-point combinations from monitoring points configuration
        
        Args:
            config: Monitoring points configuration
            
        Returns:
            DataFrame with device_name, point_name, device_type, device_alias, point_alias columns
        """
        combinations = []
        
        try:
            devices = config.get("devices", {})
            
            for device_type, device_list in devices.items():
                for device in device_list:
                    device_name = device.get("device_name")
                    device_alias = device.get("device_alias")
                    
                    for point in device.get("point_list", []):
                        point_name = point.get("point_name")
                        point_alias = point.get("point_alias")
                        
                        if device_name and point_name:
                            combinations.append({
                                "device_name": device_name,
                                "point_name": point_name,
                                "device_type": device_type,
                                "device_alias": device_alias,
                                "point_alias": point_alias,
                                "change_type": point.get("change_type"),
                                "current_old_value": point.get("old")
                            })
            
            df = pd.DataFrame(combinations)
            logger.debug(f"[RealtimeDataPopulator] Extracted {len(df)} device-point combinations")
            return df
            
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error extracting device-point combinations: {e}")
            return pd.DataFrame()
    
    def _fetch_realtime_data(self, device_point_df: pd.DataFrame, time_window_minutes: int) -> pd.DataFrame:
        """
        Fetch real-time data from database for the specified device-point combinations
        使用data_preprocessing.py中的query_realtime_data方法
        
        Args:
            device_point_df: DataFrame with device-point combinations
            time_window_minutes: Time window for fetching recent data
            
        Returns:
            DataFrame with real-time data
        """
        query_start = time.time()
        
        try:
            # 使用data_preprocessing.py中的方法获取实时数据
            from utils.data_preprocessing import query_realtime_data
            
            # 准备查询DataFrame，需要包含device_name, point_name, device_alias, point_alias列
            query_df = device_point_df[["device_name", "point_name", "device_alias", "point_alias"]].copy()
            
            logger.debug(f"[RealtimeDataPopulator] Querying real-time data for {len(query_df)} points using data_preprocessing.query_realtime_data")
            
            # 使用data_preprocessing中的query_realtime_data方法
            realtime_df = query_realtime_data(query_df)
            
            self._stats["query_time"] = time.time() - query_start
            
            if realtime_df is None or realtime_df.empty:
                logger.warning("[RealtimeDataPopulator] No real-time data returned from query_realtime_data")
                return pd.DataFrame()
            
            # query_realtime_data返回的DataFrame已经包含了device_name, point_name和v列
            # 重命名v列为value以保持一致性
            if 'v' in realtime_df.columns:
                realtime_df = realtime_df.rename(columns={'v': 'value'})
            
            # 添加时间戳
            realtime_df["timestamp"] = datetime.now()
            
            # 选择相关列
            result_columns = ["device_name", "point_name", "value", "timestamp"]
            if 'device_alias' in realtime_df.columns:
                result_columns.append("device_alias")
            if 'point_alias' in realtime_df.columns:
                result_columns.append("point_alias")
            
            result_df = realtime_df[result_columns].copy()
            
            logger.info(f"[RealtimeDataPopulator] Successfully fetched {len(result_df)} real-time records using query_realtime_data")
            return result_df
                
        except ImportError as e:
            logger.error(f"[RealtimeDataPopulator] Failed to import query_realtime_data: {e}")
            self._stats["query_time"] = time.time() - query_start
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error fetching real-time data: {e}")
            self._stats["query_time"] = time.time() - query_start
            return pd.DataFrame()
    
    def _populate_with_realtime_data(
        self, 
        config: Dict, 
        realtime_data: pd.DataFrame, 
        device_point_df: pd.DataFrame
    ) -> Dict:
        """
        Populate monitoring points configuration with real-time data using pandas vectorized operations
        
        Args:
            config: Original monitoring points configuration
            realtime_data: Real-time data from database
            device_point_df: Device-point combinations DataFrame
            
        Returns:
            Updated configuration with populated 'old' fields
        """
        try:
            # Create a mapping DataFrame by merging device_point_df with realtime_data
            merged_df = device_point_df.merge(
                realtime_data,
                on=["device_name", "point_name"],
                how="left"
            )
            
            # Create a lookup dictionary for fast access
            # Key: (device_name, point_name), Value: real-time value
            value_lookup = {}
            
            for _, row in merged_df.iterrows():
                key = (row["device_name"], row["point_name"])
                value = row.get("value")
                
                if pd.notna(value):
                    # Apply type conversion based on change_type
                    converted_value = self._convert_value_by_type(value, row.get("change_type"))
                    value_lookup[key] = converted_value
                    self._stats["successful_matches"] += 1
                else:
                    # Use current old value or default
                    default_value = self._get_default_value(row.get("change_type"))
                    current_old = row.get("current_old_value")
                    value_lookup[key] = current_old if current_old is not None else default_value
                    self._stats["failed_matches"] += 1
                    if current_old is None:
                        self._stats["default_values_used"] += 1
            
            # Update the configuration using the lookup dictionary
            updated_config = self._update_config_with_lookup(config, value_lookup)
            
            logger.debug(
                f"[RealtimeDataPopulator] Updated configuration with {len(value_lookup)} value mappings"
            )
            
            return updated_config
            
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error populating with real-time data: {e}")
            return config
    
    def _update_config_with_lookup(self, config: Dict, value_lookup: Dict) -> Dict:
        """
        Update configuration using the value lookup dictionary
        
        Args:
            config: Original configuration
            value_lookup: Dictionary mapping (device_name, point_name) to values
            
        Returns:
            Updated configuration
        """
        updated_config = config.copy()
        
        try:
            devices = updated_config.get("devices", {})
            
            for device_type, device_list in devices.items():
                for device in device_list:
                    device_name = device.get("device_name")
                    
                    for point in device.get("point_list", []):
                        point_name = point.get("point_name")
                        
                        if device_name and point_name:
                            key = (device_name, point_name)
                            if key in value_lookup:
                                point["old"] = value_lookup[key]
                                logger.debug(
                                    f"[RealtimeDataPopulator] Updated {device_name}::{point_name} "
                                    f"old value to {value_lookup[key]}"
                                )
            
            return updated_config
            
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error updating config with lookup: {e}")
            return config
    
    def _convert_value_by_type(self, value: Any, change_type: str) -> Union[int, float, str]:
        """
        Convert value based on the change_type with safe type conversion
        
        Args:
            value: Raw value from database
            change_type: Type of the parameter (analog_value, digital_on_off, enum_state)
            
        Returns:
            Converted value
        """
        try:
            if change_type == "analog_value":
                # Convert to float for analog values
                converted = float(value)
                self._stats["type_conversions"] += 1
                return converted
            elif change_type == "digital_on_off":
                # Convert to integer (0 or 1) for digital values
                if isinstance(value, str):
                    if value.lower() in ["true", "1", "on", "开启"]:
                        converted = 1
                    elif value.lower() in ["false", "0", "off", "关闭"]:
                        converted = 0
                    else:
                        converted = int(float(value))
                else:
                    converted = int(float(value))
                self._stats["type_conversions"] += 1
                return converted
            elif change_type == "enum_state":
                # Convert to integer for enum states
                converted = int(float(value))
                self._stats["type_conversions"] += 1
                return converted
            else:
                # Default: try to convert to appropriate type
                if isinstance(value, (int, float)):
                    return value
                else:
                    # Try numeric conversion first
                    try:
                        if '.' in str(value):
                            return float(value)
                        else:
                            return int(value)
                    except (ValueError, TypeError):
                        return str(value)
                        
        except (ValueError, TypeError) as e:
            logger.warning(
                f"[RealtimeDataPopulator] Type conversion failed for value {value} "
                f"(type: {change_type}): {e}, using default"
            )
            return self._get_default_value(change_type)
    
    def _get_default_value(self, change_type: str) -> Union[int, float]:
        """
        Get default value based on change_type
        
        Args:
            change_type: Type of the parameter
            
        Returns:
            Default value
        """
        defaults = {
            "analog_value": 0.0,
            "digital_on_off": 0,
            "enum_state": 0
        }
        return defaults.get(change_type, 0)
    
    def _apply_default_values(self, config: Dict) -> Dict:
        """
        Apply default values to all 'old' fields when real-time data is unavailable
        
        Args:
            config: Original configuration
            
        Returns:
            Configuration with default values
        """
        updated_config = config.copy()
        
        try:
            devices = updated_config.get("devices", {})
            
            for device_type, device_list in devices.items():
                for device in device_list:
                    for point in device.get("point_list", []):
                        change_type = point.get("change_type")
                        if point.get("old") is None:
                            point["old"] = self._get_default_value(change_type)
                            self._stats["default_values_used"] += 1
            
            logger.info(f"[RealtimeDataPopulator] Applied default values to {self._stats['default_values_used']} points")
            return updated_config
            
        except Exception as e:
            logger.error(f"[RealtimeDataPopulator] Error applying default values: {e}")
            return config
    
    def _reset_stats(self):
        """Reset statistics counters"""
        self._stats = {
            "total_points": 0,
            "successful_matches": 0,
            "failed_matches": 0,
            "type_conversions": 0,
            "default_values_used": 0,
            "query_time": 0.0,
            "processing_time": 0.0
        }
    
    def _get_stats(self) -> Dict:
        """Get current statistics"""
        stats = self._stats.copy()
        
        # Calculate success rate
        if stats["total_points"] > 0:
            stats["success_rate"] = (stats["successful_matches"] / stats["total_points"]) * 100
        else:
            stats["success_rate"] = 0.0
        
        return stats


def populate_monitoring_points_old_fields(
    monitoring_points_config: Dict,
    room_id: str,
    time_window_minutes: int = 10
) -> Tuple[Dict, Dict]:
    """
    Convenience function to populate 'old' fields in monitoring points configuration
    
    Args:
        monitoring_points_config: Monitoring points configuration dictionary
        room_id: Room ID for filtering data
        time_window_minutes: Time window for fetching recent data (default: 10 minutes)
        
    Returns:
        Tuple of (updated_config, statistics)
    """
    populator = RealtimeDataPopulator()
    return populator.populate_old_fields(monitoring_points_config, room_id, time_window_minutes)


# Example usage and testing functions
def test_realtime_data_population(room_id: str = "611"):
    """
    Test function for real-time data population
    
    Args:
        room_id: Room ID to test with
    """
    logger.info(f"[RealtimeDataPopulator] Testing real-time data population for room {room_id}")
    
    # Load sample monitoring points config
    from pathlib import Path
    config_path = Path(__file__).parent.parent / "configs" / "monitoring_points_config.json"
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            sample_config = json.load(f)
        
        # Update room_id in config
        sample_config["room_id"] = room_id
        
        # Populate old fields
        updated_config, stats = populate_monitoring_points_old_fields(sample_config, room_id)
        
        # Print results
        logger.info("[RealtimeDataPopulator] Test Results:")
        logger.info(f"  Total Points: {stats['total_points']}")
        logger.info(f"  Successful Matches: {stats['successful_matches']}")
        logger.info(f"  Failed Matches: {stats['failed_matches']}")
        logger.info(f"  Success Rate: {stats['success_rate']:.1f}%")
        logger.info(f"  Query Time: {stats['query_time']:.2f}s")
        logger.info(f"  Processing Time: {stats['processing_time']:.2f}s")
        
        return updated_config, stats
        
    except Exception as e:
        logger.error(f"[RealtimeDataPopulator] Test failed: {e}")
        return None, None


if __name__ == "__main__":
    # Run test
    test_realtime_data_population("611")