"""
Output Handler Module

This module validates and formats decision outputs, ensuring all device
parameters comply with static_config.json specifications.
"""

from datetime import datetime
from typing import Any, Dict, List, Tuple

from loguru import logger

from decision_analysis.data_models import (
    AirCoolerRecommendation,
    ControlStrategy,
    DecisionMetadata,
    DecisionOutput,
    DeviceRecommendations,
    EnhancedAirCoolerRecommendation,
    EnhancedDecisionOutput,
    EnhancedDeviceRecommendations,
    EnhancedFreshAirFanRecommendation,
    EnhancedGrowLightRecommendation,
    EnhancedHumidifierRecommendation,
    FreshAirFanRecommendation,
    GrowLightRecommendation,
    HumidifierRecommendation,
    MonitoringPoints,
    MultiImageAnalysis,
    ParameterAdjustment,
    RiskAssessment,
)


class OutputHandler:
    """
    Output handler for decision validation and formatting
    
    Validates device parameters against static_config.json and formats
    the final decision output.
    """
    
    def __init__(self, static_config: Dict):
        """
        Initialize output handler
        
        Args:
            static_config: Static configuration dictionary
            
        Requirements: 8.1
        """
        self.static_config = static_config
        logger.info("[OutputHandler] Initialized")
    
    def validate_and_format(
        self,
        raw_decision: Dict,
        room_id: str
    ) -> DecisionOutput:
        """
        Validate and format decision output
        
        Validates:
        - Structure completeness
        - Device parameter validity
        - Enumeration values
        - Value ranges
        
        Args:
            raw_decision: Raw decision from LLM
            room_id: Room number
            
        Returns:
            Validated and formatted DecisionOutput
            
        Requirements: 8.1, 8.4, 8.5
        """
        logger.info("[OutputHandler] Validating and formatting decision output")
        
        warnings = []
        errors = []
        
        # Validate structure completeness
        required_keys = ['strategy', 'device_recommendations', 'monitoring_points']
        for key in required_keys:
            if key not in raw_decision:
                error_msg = f"Missing required key: {key}"
                logger.error(f"[OutputHandler] {error_msg}")
                errors.append(error_msg)
        
        # If critical structure is missing, return error status
        if errors:
            return DecisionOutput(
                status="error",
                room_id=room_id,
                analysis_time=datetime.now(),
                strategy=ControlStrategy(core_objective="结构验证失败"),
                device_recommendations=DeviceRecommendations(
                    air_cooler=AirCoolerRecommendation(0, 0, 0, 0, 0, 0, 0),
                    fresh_air_fan=FreshAirFanRecommendation(0, 0, 0, 0, 0, 0),
                    humidifier=HumidifierRecommendation(0, 0, 0),
                    grow_light=GrowLightRecommendation(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                ),
                monitoring_points=MonitoringPoints(),
                metadata=DecisionMetadata(errors=errors)
            )
        
        # Extract and validate strategy
        strategy_data = raw_decision.get('strategy', {})
        strategy = ControlStrategy(
            core_objective=strategy_data.get('core_objective', ''),
            priority_ranking=strategy_data.get('priority_ranking', []),
            key_risk_points=strategy_data.get('key_risk_points', [])
        )
        
        # Extract and validate device recommendations
        device_recs = raw_decision.get('device_recommendations', {})
        
        # Validate and correct air cooler parameters
        air_cooler_params = device_recs.get('air_cooler', {})
        # Filter out None values before validation
        air_cooler_params = {k: v for k, v in air_cooler_params.items() if v is not None}
        is_valid, validation_errors = self._validate_device_params('air_cooler', air_cooler_params)
        if not is_valid:
            logger.warning(f"[OutputHandler] Air cooler validation errors: {validation_errors}")
            warnings.extend(validation_errors)
            air_cooler_params, correction_warnings = self._correct_invalid_params('air_cooler', air_cooler_params)
            warnings.extend(correction_warnings)
        
        air_cooler = AirCoolerRecommendation(
            tem_set=float(air_cooler_params.get('tem_set', 15.0)),
            tem_diff_set=float(air_cooler_params.get('tem_diff_set', 2.0)),
            cyc_on_off=int(air_cooler_params.get('cyc_on_off', 0)),
            cyc_on_time=int(air_cooler_params.get('cyc_on_time', 10)),
            cyc_off_time=int(air_cooler_params.get('cyc_off_time', 10)),
            ar_on_off=int(air_cooler_params.get('ar_on_off', 0)),
            hum_on_off=int(air_cooler_params.get('hum_on_off', 0)),
            rationale=air_cooler_params.get('rationale', [])
        )
        
        # Validate and correct fresh air fan parameters
        fresh_air_params = device_recs.get('fresh_air_fan', {})
        # Filter out None values before validation
        fresh_air_params = {k: v for k, v in fresh_air_params.items() if v is not None}
        is_valid, validation_errors = self._validate_device_params('fresh_air_fan', fresh_air_params)
        if not is_valid:
            logger.warning(f"[OutputHandler] Fresh air fan validation errors: {validation_errors}")
            warnings.extend(validation_errors)
            fresh_air_params, correction_warnings = self._correct_invalid_params('fresh_air_fan', fresh_air_params)
            warnings.extend(correction_warnings)
        
        fresh_air_fan = FreshAirFanRecommendation(
            model=int(fresh_air_params.get('model', 0)),
            control=int(fresh_air_params.get('control', 0)),
            co2_on=int(fresh_air_params.get('co2_on', 1000)),
            co2_off=int(fresh_air_params.get('co2_off', 800)),
            on=int(fresh_air_params.get('on', 10)),
            off=int(fresh_air_params.get('off', 10)),
            rationale=fresh_air_params.get('rationale', [])
        )
        
        # Validate and correct humidifier parameters
        humidifier_params = device_recs.get('humidifier', {})
        # Filter out None values before validation
        humidifier_params = {k: v for k, v in humidifier_params.items() if v is not None}
        is_valid, validation_errors = self._validate_device_params('humidifier', humidifier_params)
        if not is_valid:
            logger.warning(f"[OutputHandler] Humidifier validation errors: {validation_errors}")
            warnings.extend(validation_errors)
            humidifier_params, correction_warnings = self._correct_invalid_params('humidifier', humidifier_params)
            warnings.extend(correction_warnings)
        
        humidifier = HumidifierRecommendation(
            model=int(humidifier_params.get('model', 0)),
            on=int(humidifier_params.get('on', 90)),
            off=int(humidifier_params.get('off', 85)),
            left_right_strategy=humidifier_params.get('left_right_strategy', ''),
            rationale=humidifier_params.get('rationale', [])
        )
        
        # Validate and correct grow light parameters
        grow_light_params = device_recs.get('grow_light', {})
        # Filter out None values before validation
        grow_light_params = {k: v for k, v in grow_light_params.items() if v is not None}
        is_valid, validation_errors = self._validate_device_params('grow_light', grow_light_params)
        if not is_valid:
            logger.warning(f"[OutputHandler] Grow light validation errors: {validation_errors}")
            warnings.extend(validation_errors)
            grow_light_params, correction_warnings = self._correct_invalid_params('grow_light', grow_light_params)
            warnings.extend(correction_warnings)
        
        grow_light = GrowLightRecommendation(
            model=int(grow_light_params.get('model', 0)),
            on_mset=int(grow_light_params.get('on_mset', 60)),
            off_mset=int(grow_light_params.get('off_mset', 60)),
            on_off_1=int(grow_light_params.get('on_off_1', 0)),
            choose_1=int(grow_light_params.get('choose_1', 0)),
            on_off_2=int(grow_light_params.get('on_off_2', 0)),
            choose_2=int(grow_light_params.get('choose_2', 0)),
            on_off_3=int(grow_light_params.get('on_off_3', 0)),
            choose_3=int(grow_light_params.get('choose_3', 0)),
            on_off_4=int(grow_light_params.get('on_off_4', 0)),
            choose_4=int(grow_light_params.get('choose_4', 0)),
            rationale=grow_light_params.get('rationale', [])
        )
        
        device_recommendations = DeviceRecommendations(
            air_cooler=air_cooler,
            fresh_air_fan=fresh_air_fan,
            humidifier=humidifier,
            grow_light=grow_light
        )
        
        # Extract monitoring points
        monitoring_data = raw_decision.get('monitoring_points', {})
        monitoring_points = MonitoringPoints(
            key_time_periods=monitoring_data.get('key_time_periods', []),
            warning_thresholds=monitoring_data.get('warning_thresholds', {}),
            emergency_measures=monitoring_data.get('emergency_measures', [])
        )
        
        # Create metadata
        metadata = DecisionMetadata(
            warnings=warnings,
            errors=errors
        )
        
        # Determine status
        status = "success" if not errors else "error"
        
        logger.info(f"[OutputHandler] Validation complete: status={status}, warnings={len(warnings)}, errors={len(errors)}")
        
        return DecisionOutput(
            status=status,
            room_id=room_id,
            analysis_time=datetime.now(),
            strategy=strategy,
            device_recommendations=device_recommendations,
            monitoring_points=monitoring_points,
            metadata=metadata
        )
    
    def _validate_device_params(
        self,
        device_type: str,
        params: Dict
    ) -> Tuple[bool, List[str]]:
        """
        Validate device parameters against static_config
        
        Checks:
        - Enumeration values are valid
        - Numeric values are within range
        - Required fields are present
        
        Args:
            device_type: Device type (air_cooler, fresh_air_fan, etc.)
            params: Parameter dictionary
            
        Returns:
            Tuple of (is_valid, error_messages)
            
        Requirements: 8.2, 11.3, 11.4, 11.5
        """
        errors = []
        
        # Get device configuration from static_config
        device_config = self.static_config.get('mushroom', {}).get('datapoint', {}).get(device_type)
        if not device_config:
            errors.append(f"Device type '{device_type}' not found in static_config")
            return False, errors
        
        point_list = device_config.get('point_list', [])
        
        # Build a map of point_alias to point config for easy lookup
        point_map = {}
        for point in point_list:
            point_alias = point.get('point_alias', '')
            if point_alias:
                point_map[point_alias] = point
        
        # Validate each parameter
        for param_name, param_value in params.items():
            # Skip non-parameter fields
            if param_name in ['rationale', 'left_right_strategy']:
                continue
            
            # Check if parameter exists in config
            if param_name not in point_map:
                # Not all params need to be in config (e.g., derived params)
                continue
            
            point_config = point_map[param_name]
            
            # Validate enumeration values
            if 'enum' in point_config:
                enum_values = point_config['enum']
                # Convert param_value to string for comparison
                param_str = str(int(param_value)) if isinstance(param_value, (int, float)) else str(param_value)
                
                if param_str not in enum_values:
                    valid_values = ', '.join(enum_values.keys())
                    errors.append(
                        f"{device_type}.{param_name}: Invalid enum value '{param_value}'. "
                        f"Valid values: {valid_values}"
                    )
        
        # Device-specific range validations
        if device_type == 'air_cooler':
            # Temperature setpoint: typically 5-25°C
            if 'tem_set' in params:
                tem_set = params['tem_set']
                if not (5 <= tem_set <= 25):
                    errors.append(f"air_cooler.tem_set: Value {tem_set} out of range [5, 25]")
            
            # Temperature difference: typically 0.5-5°C
            if 'tem_diff_set' in params:
                tem_diff = params['tem_diff_set']
                if not (0.5 <= tem_diff <= 5):
                    errors.append(f"air_cooler.tem_diff_set: Value {tem_diff} out of range [0.5, 5]")
            
            # Cycle times: typically 1-60 minutes
            if 'cyc_on_time' in params:
                cyc_on = params['cyc_on_time']
                if not (1 <= cyc_on <= 60):
                    errors.append(f"air_cooler.cyc_on_time: Value {cyc_on} out of range [1, 60]")
            
            if 'cyc_off_time' in params:
                cyc_off = params['cyc_off_time']
                if not (1 <= cyc_off <= 60):
                    errors.append(f"air_cooler.cyc_off_time: Value {cyc_off} out of range [1, 60]")
        
        elif device_type == 'fresh_air_fan':
            # CO2 thresholds: typically 400-5000 ppm
            if 'co2_on' in params:
                co2_on = params['co2_on']
                if not (400 <= co2_on <= 5000):
                    errors.append(f"fresh_air_fan.co2_on: Value {co2_on} out of range [400, 5000]")
            
            if 'co2_off' in params:
                co2_off = params['co2_off']
                if not (400 <= co2_off <= 5000):
                    errors.append(f"fresh_air_fan.co2_off: Value {co2_off} out of range [400, 5000]")
            
            # CO2_on should be greater than CO2_off
            if 'co2_on' in params and 'co2_off' in params:
                if params['co2_on'] <= params['co2_off']:
                    errors.append(
                        f"fresh_air_fan: co2_on ({params['co2_on']}) must be greater than "
                        f"co2_off ({params['co2_off']})"
                    )
            
            # Time settings: typically 1-120 minutes
            if 'on' in params:
                on_time = params['on']
                if not (1 <= on_time <= 120):
                    errors.append(f"fresh_air_fan.on: Value {on_time} out of range [1, 120]")
            
            if 'off' in params:
                off_time = params['off']
                if not (1 <= off_time <= 120):
                    errors.append(f"fresh_air_fan.off: Value {off_time} out of range [1, 120]")
        
        elif device_type == 'humidifier':
            # Humidity thresholds: 0-100%
            if 'on' in params:
                hum_on = params['on']
                if not (0 <= hum_on <= 100):
                    errors.append(f"humidifier.on: Value {hum_on} out of range [0, 100]")
            
            if 'off' in params:
                hum_off = params['off']
                if not (0 <= hum_off <= 100):
                    errors.append(f"humidifier.off: Value {hum_off} out of range [0, 100]")
            
            # On threshold should be less than off threshold (turn on when humidity drops)
            if 'on' in params and 'off' in params:
                if params['on'] >= params['off']:
                    errors.append(
                        f"humidifier: on ({params['on']}) should be less than "
                        f"off ({params['off']})"
                    )
        
        elif device_type == 'grow_light':
            # Time settings: typically 1-1440 minutes (24 hours)
            if 'on_mset' in params:
                on_mset = params['on_mset']
                if not (1 <= on_mset <= 1440):
                    errors.append(f"grow_light.on_mset: Value {on_mset} out of range [1, 1440]")
            
            if 'off_mset' in params:
                off_mset = params['off_mset']
                if not (1 <= off_mset <= 1440):
                    errors.append(f"grow_light.off_mset: Value {off_mset} out of range [1, 1440]")
        
        is_valid = len(errors) == 0
        return is_valid, errors
    
    def _correct_invalid_params(
        self,
        device_type: str,
        params: Dict
    ) -> Tuple[Dict, List[str]]:
        """
        Correct invalid parameters
        
        Corrections:
        - Clamp out-of-range values to boundaries
        - Replace invalid enums with closest valid value
        - Fill missing fields with defaults
        
        Args:
            device_type: Device type
            params: Parameter dictionary
            
        Returns:
            Tuple of (corrected_params, warning_messages)
            
        Requirements: 8.3
        """
        corrected = params.copy()
        warnings = []
        
        # Get device configuration from static_config
        device_config = self.static_config.get('mushroom', {}).get('datapoint', {}).get(device_type)
        if not device_config:
            return corrected, warnings
        
        point_list = device_config.get('point_list', [])
        
        # Build a map of point_alias to point config
        point_map = {}
        for point in point_list:
            point_alias = point.get('point_alias', '')
            if point_alias:
                point_map[point_alias] = point
        
        # Correct enumeration values
        for param_name, param_value in params.items():
            # Skip non-parameter fields
            if param_name in ['rationale', 'left_right_strategy']:
                continue
            
            if param_name not in point_map:
                continue
            
            point_config = point_map[param_name]
            
            # Correct invalid enum values
            if 'enum' in point_config:
                enum_values = point_config['enum']
                param_str = str(int(param_value)) if isinstance(param_value, (int, float)) else str(param_value)
                
                if param_str not in enum_values:
                    # Use the first valid enum value as default
                    default_value = int(list(enum_values.keys())[0])
                    corrected[param_name] = default_value
                    warnings.append(
                        f"{device_type}.{param_name}: Corrected invalid value '{param_value}' to '{default_value}'"
                    )
        
        # Device-specific range corrections
        if device_type == 'air_cooler':
            # Clamp temperature setpoint to [5, 25]
            if 'tem_set' in corrected:
                original = corrected['tem_set']
                corrected['tem_set'] = max(5.0, min(25.0, float(original)))
                if corrected['tem_set'] != original:
                    warnings.append(
                        f"air_cooler.tem_set: Clamped {original} to {corrected['tem_set']} [5, 25]"
                    )
            else:
                corrected['tem_set'] = 15.0
                warnings.append("air_cooler.tem_set: Missing field, set to default 15.0")
            
            # Clamp temperature difference to [0.5, 5]
            if 'tem_diff_set' in corrected:
                original = corrected['tem_diff_set']
                corrected['tem_diff_set'] = max(0.5, min(5.0, float(original)))
                if corrected['tem_diff_set'] != original:
                    warnings.append(
                        f"air_cooler.tem_diff_set: Clamped {original} to {corrected['tem_diff_set']} [0.5, 5]"
                    )
            else:
                corrected['tem_diff_set'] = 2.0
                warnings.append("air_cooler.tem_diff_set: Missing field, set to default 2.0")
            
            # Clamp cycle times to [1, 60]
            if 'cyc_on_time' in corrected:
                original = corrected['cyc_on_time']
                corrected['cyc_on_time'] = max(1, min(60, int(original)))
                if corrected['cyc_on_time'] != original:
                    warnings.append(
                        f"air_cooler.cyc_on_time: Clamped {original} to {corrected['cyc_on_time']} [1, 60]"
                    )
            else:
                corrected['cyc_on_time'] = 10
                warnings.append("air_cooler.cyc_on_time: Missing field, set to default 10")
            
            if 'cyc_off_time' in corrected:
                original = corrected['cyc_off_time']
                corrected['cyc_off_time'] = max(1, min(60, int(original)))
                if corrected['cyc_off_time'] != original:
                    warnings.append(
                        f"air_cooler.cyc_off_time: Clamped {original} to {corrected['cyc_off_time']} [1, 60]"
                    )
            else:
                corrected['cyc_off_time'] = 10
                warnings.append("air_cooler.cyc_off_time: Missing field, set to default 10")
            
            # Ensure enum fields have defaults
            for field, default in [('cyc_on_off', 0), ('ar_on_off', 0), ('hum_on_off', 0)]:
                if field not in corrected:
                    corrected[field] = default
                    warnings.append(f"air_cooler.{field}: Missing field, set to default {default}")
        
        elif device_type == 'fresh_air_fan':
            # Clamp CO2 thresholds to [400, 5000]
            if 'co2_on' in corrected:
                original = corrected['co2_on']
                corrected['co2_on'] = max(400, min(5000, int(original)))
                if corrected['co2_on'] != original:
                    warnings.append(
                        f"fresh_air_fan.co2_on: Clamped {original} to {corrected['co2_on']} [400, 5000]"
                    )
            else:
                corrected['co2_on'] = 1000
                warnings.append("fresh_air_fan.co2_on: Missing field, set to default 1000")
            
            if 'co2_off' in corrected:
                original = corrected['co2_off']
                corrected['co2_off'] = max(400, min(5000, int(original)))
                if corrected['co2_off'] != original:
                    warnings.append(
                        f"fresh_air_fan.co2_off: Clamped {original} to {corrected['co2_off']} [400, 5000]"
                    )
            else:
                corrected['co2_off'] = 800
                warnings.append("fresh_air_fan.co2_off: Missing field, set to default 800")
            
            # Ensure co2_on > co2_off
            if corrected['co2_on'] <= corrected['co2_off']:
                corrected['co2_on'] = corrected['co2_off'] + 200
                warnings.append(
                    f"fresh_air_fan: Adjusted co2_on to {corrected['co2_on']} to be greater than co2_off"
                )
            
            # Clamp time settings to [1, 120]
            if 'on' in corrected:
                original = corrected['on']
                corrected['on'] = max(1, min(120, int(original)))
                if corrected['on'] != original:
                    warnings.append(
                        f"fresh_air_fan.on: Clamped {original} to {corrected['on']} [1, 120]"
                    )
            else:
                corrected['on'] = 10
                warnings.append("fresh_air_fan.on: Missing field, set to default 10")
            
            if 'off' in corrected:
                original = corrected['off']
                corrected['off'] = max(1, min(120, int(original)))
                if corrected['off'] != original:
                    warnings.append(
                        f"fresh_air_fan.off: Clamped {original} to {corrected['off']} [1, 120]"
                    )
            else:
                corrected['off'] = 10
                warnings.append("fresh_air_fan.off: Missing field, set to default 10")
            
            # Ensure enum fields have defaults
            for field, default in [('model', 0), ('control', 0)]:
                if field not in corrected:
                    corrected[field] = default
                    warnings.append(f"fresh_air_fan.{field}: Missing field, set to default {default}")
        
        elif device_type == 'humidifier':
            # Clamp humidity thresholds to [0, 100]
            if 'on' in corrected:
                original = corrected['on']
                corrected['on'] = max(0, min(100, int(original)))
                if corrected['on'] != original:
                    warnings.append(
                        f"humidifier.on: Clamped {original} to {corrected['on']} [0, 100]"
                    )
            else:
                corrected['on'] = 85
                warnings.append("humidifier.on: Missing field, set to default 85")
            
            if 'off' in corrected:
                original = corrected['off']
                corrected['off'] = max(0, min(100, int(original)))
                if corrected['off'] != original:
                    warnings.append(
                        f"humidifier.off: Clamped {original} to {corrected['off']} [0, 100]"
                    )
            else:
                corrected['off'] = 90
                warnings.append("humidifier.off: Missing field, set to default 90")
            
            # Ensure on < off (turn on when humidity drops below threshold)
            if corrected['on'] >= corrected['off']:
                corrected['off'] = corrected['on'] + 5
                if corrected['off'] > 100:
                    corrected['off'] = 100
                    corrected['on'] = 95
                warnings.append(
                    f"humidifier: Adjusted thresholds to on={corrected['on']}, off={corrected['off']}"
                )
            
            # Ensure enum field has default
            if 'model' not in corrected:
                corrected['model'] = 0
                warnings.append("humidifier.model: Missing field, set to default 0")
        
        elif device_type == 'grow_light':
            # Clamp time settings to [1, 1440]
            if 'on_mset' in corrected:
                original = corrected['on_mset']
                corrected['on_mset'] = max(1, min(1440, int(original)))
                if corrected['on_mset'] != original:
                    warnings.append(
                        f"grow_light.on_mset: Clamped {original} to {corrected['on_mset']} [1, 1440]"
                    )
            else:
                corrected['on_mset'] = 60
                warnings.append("grow_light.on_mset: Missing field, set to default 60")
            
            if 'off_mset' in corrected:
                original = corrected['off_mset']
                corrected['off_mset'] = max(1, min(1440, int(original)))
                if corrected['off_mset'] != original:
                    warnings.append(
                        f"grow_light.off_mset: Clamped {original} to {corrected['off_mset']} [1, 1440]"
                    )
            else:
                corrected['off_mset'] = 60
                warnings.append("grow_light.off_mset: Missing field, set to default 60")
            
            # Ensure enum fields have defaults
            for field, default in [('model', 0), ('on_off_1', 0), ('choose_1', 0),
                                   ('on_off_2', 0), ('choose_2', 0), ('on_off_3', 0),
                                   ('choose_3', 0), ('on_off_4', 0), ('choose_4', 0)]:
                if field not in corrected:
                    corrected[field] = default
                    warnings.append(f"grow_light.{field}: Missing field, set to default {default}")
        
        return corrected, warnings
    
    def validate_and_format_enhanced(
        self,
        raw_decision: Dict,
        room_id: str,
        multi_image_analysis: MultiImageAnalysis = None
    ) -> EnhancedDecisionOutput:
        """
        Validate and format enhanced decision output with parameter adjustments
        
        Validates:
        - Structure completeness
        - Parameter adjustment format
        - Action types (maintain/adjust/monitor)
        - Risk assessments
        - Priority and urgency levels
        
        Args:
            raw_decision: Raw decision from LLM
            room_id: Room number
            multi_image_analysis: Multi-image analysis results
            
        Returns:
            Validated and formatted EnhancedDecisionOutput
            
        Requirements: Enhanced decision analysis with multi-image support
        """
        logger.info("[OutputHandler] Validating and formatting enhanced decision output")
        
        warnings = []
        errors = []
        
        # Validate structure completeness
        required_keys = ['strategy', 'device_recommendations', 'monitoring_points']
        for key in required_keys:
            if key not in raw_decision:
                error_msg = f"Missing required key: {key}"
                logger.error(f"[OutputHandler] {error_msg}")
                errors.append(error_msg)
        
        # If critical structure is missing, return error status
        if errors:
            return self._create_error_enhanced_output(room_id, errors)
        
        # Extract and validate strategy
        strategy_data = raw_decision.get('strategy', {})
        strategy = ControlStrategy(
            core_objective=strategy_data.get('core_objective', ''),
            priority_ranking=strategy_data.get('priority_ranking', []),
            key_risk_points=strategy_data.get('key_risk_points', [])
        )
        
        # Extract and validate enhanced device recommendations
        device_recs = raw_decision.get('device_recommendations', {})
        
        # Validate and create enhanced air cooler recommendations
        air_cooler_params = device_recs.get('air_cooler', {})
        enhanced_air_cooler = self._validate_enhanced_air_cooler(air_cooler_params, warnings, errors)
        
        # Validate and create enhanced fresh air fan recommendations
        fresh_air_params = device_recs.get('fresh_air_fan', {})
        enhanced_fresh_air = self._validate_enhanced_fresh_air_fan(fresh_air_params, warnings, errors)
        
        # Validate and create enhanced humidifier recommendations
        humidifier_params = device_recs.get('humidifier', {})
        enhanced_humidifier = self._validate_enhanced_humidifier(humidifier_params, warnings, errors)
        
        # Validate and create enhanced grow light recommendations
        grow_light_params = device_recs.get('grow_light', {})
        enhanced_grow_light = self._validate_enhanced_grow_light(grow_light_params, warnings, errors)
        
        enhanced_device_recommendations = EnhancedDeviceRecommendations(
            air_cooler=enhanced_air_cooler,
            fresh_air_fan=enhanced_fresh_air,
            humidifier=enhanced_humidifier,
            grow_light=enhanced_grow_light
        )
        
        # Extract monitoring points
        monitoring_data = raw_decision.get('monitoring_points', {})
        monitoring_points = MonitoringPoints(
            key_time_periods=monitoring_data.get('key_time_periods', []),
            warning_thresholds=monitoring_data.get('warning_thresholds', {}),
            emergency_measures=monitoring_data.get('emergency_measures', [])
        )
        
        # Create metadata
        metadata = DecisionMetadata(
            warnings=warnings,
            errors=errors
        )
        
        # Determine status
        status = "success" if not errors else "error"
        
        logger.info(f"[OutputHandler] Enhanced validation complete: status={status}, warnings={len(warnings)}, errors={len(errors)}")
        
        return EnhancedDecisionOutput(
            status=status,
            room_id=room_id,
            analysis_time=datetime.now(),
            strategy=strategy,
            device_recommendations=enhanced_device_recommendations,
            monitoring_points=monitoring_points,
            multi_image_analysis=multi_image_analysis,
            metadata=metadata
        )
    
    def _validate_enhanced_air_cooler(
        self,
        params: Dict,
        warnings: List[str],
        errors: List[str]
    ) -> EnhancedAirCoolerRecommendation:
        """Validate and create enhanced air cooler recommendation"""
        # Extract parameter adjustments
        tem_set_adj = self._extract_parameter_adjustment(params.get('tem_set', {}), 'tem_set', 'air_cooler')
        tem_diff_set_adj = self._extract_parameter_adjustment(params.get('tem_diff_set', {}), 'tem_diff_set', 'air_cooler')
        cyc_on_off_adj = self._extract_parameter_adjustment(params.get('cyc_on_off', {}), 'cyc_on_off', 'air_cooler')
        cyc_on_time_adj = self._extract_parameter_adjustment(params.get('cyc_on_time', {}), 'cyc_on_time', 'air_cooler')
        cyc_off_time_adj = self._extract_parameter_adjustment(params.get('cyc_off_time', {}), 'cyc_off_time', 'air_cooler')
        ar_on_off_adj = self._extract_parameter_adjustment(params.get('ar_on_off', {}), 'ar_on_off', 'air_cooler')
        hum_on_off_adj = self._extract_parameter_adjustment(params.get('hum_on_off', {}), 'hum_on_off', 'air_cooler')
        
        return EnhancedAirCoolerRecommendation(
            tem_set=tem_set_adj,
            tem_diff_set=tem_diff_set_adj,
            cyc_on_off=cyc_on_off_adj,
            cyc_on_time=cyc_on_time_adj,
            cyc_off_time=cyc_off_time_adj,
            ar_on_off=ar_on_off_adj,
            hum_on_off=hum_on_off_adj,
            rationale=params.get('rationale', [])
        )
    
    def _validate_enhanced_fresh_air_fan(
        self,
        params: Dict,
        warnings: List[str],
        errors: List[str]
    ) -> EnhancedFreshAirFanRecommendation:
        """Validate and create enhanced fresh air fan recommendation"""
        model_adj = self._extract_parameter_adjustment(params.get('model', {}), 'model', 'fresh_air_fan')
        control_adj = self._extract_parameter_adjustment(params.get('control', {}), 'control', 'fresh_air_fan')
        co2_on_adj = self._extract_parameter_adjustment(params.get('co2_on', {}), 'co2_on', 'fresh_air_fan')
        co2_off_adj = self._extract_parameter_adjustment(params.get('co2_off', {}), 'co2_off', 'fresh_air_fan')
        on_adj = self._extract_parameter_adjustment(params.get('on', {}), 'on', 'fresh_air_fan')
        off_adj = self._extract_parameter_adjustment(params.get('off', {}), 'off', 'fresh_air_fan')
        
        return EnhancedFreshAirFanRecommendation(
            model=model_adj,
            control=control_adj,
            co2_on=co2_on_adj,
            co2_off=co2_off_adj,
            on=on_adj,
            off=off_adj,
            rationale=params.get('rationale', [])
        )
    
    def _validate_enhanced_humidifier(
        self,
        params: Dict,
        warnings: List[str],
        errors: List[str]
    ) -> EnhancedHumidifierRecommendation:
        """Validate and create enhanced humidifier recommendation"""
        model_adj = self._extract_parameter_adjustment(params.get('model', {}), 'model', 'humidifier')
        on_adj = self._extract_parameter_adjustment(params.get('on', {}), 'on', 'humidifier')
        off_adj = self._extract_parameter_adjustment(params.get('off', {}), 'off', 'humidifier')
        
        return EnhancedHumidifierRecommendation(
            model=model_adj,
            on=on_adj,
            off=off_adj,
            left_right_strategy=params.get('left_right_strategy', ''),
            rationale=params.get('rationale', [])
        )
    
    def _validate_enhanced_grow_light(
        self,
        params: Dict,
        warnings: List[str],
        errors: List[str]
    ) -> EnhancedGrowLightRecommendation:
        """Validate and create enhanced grow light recommendation"""
        model_adj = self._extract_parameter_adjustment(params.get('model', {}), 'model', 'grow_light')
        on_mset_adj = self._extract_parameter_adjustment(params.get('on_mset', {}), 'on_mset', 'grow_light')
        off_mset_adj = self._extract_parameter_adjustment(params.get('off_mset', {}), 'off_mset', 'grow_light')
        on_off_1_adj = self._extract_parameter_adjustment(params.get('on_off_1', {}), 'on_off_1', 'grow_light')
        choose_1_adj = self._extract_parameter_adjustment(params.get('choose_1', {}), 'choose_1', 'grow_light')
        on_off_2_adj = self._extract_parameter_adjustment(params.get('on_off_2', {}), 'on_off_2', 'grow_light')
        choose_2_adj = self._extract_parameter_adjustment(params.get('choose_2', {}), 'choose_2', 'grow_light')
        on_off_3_adj = self._extract_parameter_adjustment(params.get('on_off_3', {}), 'on_off_3', 'grow_light')
        choose_3_adj = self._extract_parameter_adjustment(params.get('choose_3', {}), 'choose_3', 'grow_light')
        on_off_4_adj = self._extract_parameter_adjustment(params.get('on_off_4', {}), 'on_off_4', 'grow_light')
        choose_4_adj = self._extract_parameter_adjustment(params.get('choose_4', {}), 'choose_4', 'grow_light')
        
        return EnhancedGrowLightRecommendation(
            model=model_adj,
            on_mset=on_mset_adj,
            off_mset=off_mset_adj,
            on_off_1=on_off_1_adj,
            choose_1=choose_1_adj,
            on_off_2=on_off_2_adj,
            choose_2=choose_2_adj,
            on_off_3=on_off_3_adj,
            choose_3=choose_3_adj,
            on_off_4=on_off_4_adj,
            choose_4=choose_4_adj,
            rationale=params.get('rationale', [])
        )
    
    def _extract_parameter_adjustment(
        self,
        param_data: Dict,
        param_name: str,
        device_type: str
    ) -> ParameterAdjustment:
        """Extract and validate parameter adjustment from LLM output"""
        if not isinstance(param_data, dict):
            # Fallback for simple value format
            return ParameterAdjustment(
                current_value=param_data if param_data is not None else 0,
                recommended_value=param_data if param_data is not None else 0,
                action="maintain",
                change_reason="使用当前值",
                priority="low",
                urgency="routine",
                risk_assessment=RiskAssessment(
                    adjustment_risk="low",
                    no_action_risk="low",
                    impact_scope="无影响"
                )
            )
        
        # Extract values with defaults
        current_value = param_data.get('current_value', 0)
        recommended_value = param_data.get('recommended_value', current_value)
        action = param_data.get('action', 'maintain')
        change_reason = param_data.get('change_reason', '无调整说明')
        priority = param_data.get('priority', 'low')
        urgency = param_data.get('urgency', 'routine')
        
        # Extract risk assessment
        risk_data = param_data.get('risk_assessment', {})
        risk_assessment = RiskAssessment(
            adjustment_risk=risk_data.get('adjustment_risk', 'low'),
            no_action_risk=risk_data.get('no_action_risk', 'low'),
            impact_scope=risk_data.get('impact_scope', '无影响')
        )
        
        # Validate action type
        valid_actions = ['maintain', 'adjust', 'monitor']
        if action not in valid_actions:
            logger.warning(f"[OutputHandler] Invalid action '{action}' for {device_type}.{param_name}, using 'maintain'")
            action = 'maintain'
        
        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'critical']
        if priority not in valid_priorities:
            logger.warning(f"[OutputHandler] Invalid priority '{priority}' for {device_type}.{param_name}, using 'low'")
            priority = 'low'
        
        # Validate urgency
        valid_urgencies = ['immediate', 'within_hour', 'within_day', 'routine']
        if urgency not in valid_urgencies:
            logger.warning(f"[OutputHandler] Invalid urgency '{urgency}' for {device_type}.{param_name}, using 'routine'")
            urgency = 'routine'
        
        return ParameterAdjustment(
            current_value=current_value,
            recommended_value=recommended_value,
            action=action,
            change_reason=change_reason,
            priority=priority,
            urgency=urgency,
            risk_assessment=risk_assessment
        )
    
    def _create_error_enhanced_output(
        self,
        room_id: str,
        errors: List[str]
    ) -> EnhancedDecisionOutput:
        """Create error enhanced decision output"""
        return EnhancedDecisionOutput(
            status="error",
            room_id=room_id,
            analysis_time=datetime.now(),
            strategy=ControlStrategy(core_objective="结构验证失败"),
            device_recommendations=EnhancedDeviceRecommendations(
                air_cooler=EnhancedAirCoolerRecommendation(
                    tem_set=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    tem_diff_set=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    cyc_on_off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    cyc_on_time=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    cyc_off_time=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    ar_on_off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    hum_on_off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    rationale=["系统错误"]
                ),
                fresh_air_fan=EnhancedFreshAirFanRecommendation(
                    model=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    control=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    co2_on=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    co2_off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    rationale=["系统错误"]
                ),
                humidifier=EnhancedHumidifierRecommendation(
                    model=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    off=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    rationale=["系统错误"]
                ),
                grow_light=EnhancedGrowLightRecommendation(
                    model=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on_mset=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    off_mset=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on_off_1=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    choose_1=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on_off_2=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    choose_2=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on_off_3=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    choose_3=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    on_off_4=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    choose_4=ParameterAdjustment(0, 0, "maintain", "系统错误", "low", "routine", RiskAssessment("low", "low", "无影响")),
                    rationale=["系统错误"]
                )
            ),
            monitoring_points=MonitoringPoints(),
            metadata=DecisionMetadata(errors=errors)
        )
