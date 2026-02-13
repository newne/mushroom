"""
Data Models for Decision Analysis Module

This module defines all data structures used in the decision analysis workflow,
including input data models, output data models, and configuration models.
All models use Python dataclasses for type safety and clarity.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np


# ============================================================================
# Input Data Models
# ============================================================================


@dataclass
class CurrentStateData:
    """
    Current state data extracted from MushroomImageEmbedding table

    Attributes:
        room_id: Room number (607/608/611/612)
        collection_datetime: Data collection timestamp
        in_date: Batch entry date
        in_num: Batch entry number
        growth_day: Days since inoculation
        temperature: Current temperature (°C)
        humidity: Current humidity (%)
        co2: Current CO2 concentration (ppm)
        embedding: Image embedding vector (512 dimensions)
        semantic_description: CLIP-optimized growth stage description
        llama_description: LLaMA-generated description (optional)
        image_quality_score: Image quality score 0-100 (optional)
        air_cooler_config: Air cooler device configuration JSON
        fresh_fan_config: Fresh air fan device configuration JSON
        humidifier_config: Humidifier device configuration JSON
        light_config: Grow light device configuration JSON
    """

    room_id: str
    collection_datetime: datetime
    in_date: date
    in_num: int
    growth_day: int

    # Environmental sensor status
    temperature: float
    humidity: float
    co2: float

    # Image embedding
    embedding: np.ndarray  # shape: (512,)
    semantic_description: str
    llama_description: Optional[str] = None
    image_quality_score: Optional[float] = None

    # Device configurations
    air_cooler_config: Dict = field(default_factory=dict)
    fresh_fan_config: Dict = field(default_factory=dict)
    humidifier_config: Dict = field(default_factory=dict)
    light_config: Dict = field(default_factory=dict)


@dataclass
class EnvStatsData:
    """
    Environmental statistics data from MushroomEnvDailyStats table

    Attributes:
        room_id: Room number
        stat_date: Statistics date
        in_day_num: Days since batch entry
        is_growth_phase: Whether in active growth phase
        temp_median: Median temperature (°C)
        temp_min: Minimum temperature (°C)
        temp_max: Maximum temperature (°C)
        temp_q25: 25th percentile temperature (°C)
        temp_q75: 75th percentile temperature (°C)
        humidity_median: Median humidity (%)
        humidity_min: Minimum humidity (%)
        humidity_max: Maximum humidity (%)
        humidity_q25: 25th percentile humidity (%)
        humidity_q75: 75th percentile humidity (%)
        co2_median: Median CO2 concentration (ppm)
        co2_min: Minimum CO2 concentration (ppm)
        co2_max: Maximum CO2 concentration (ppm)
        co2_q25: 25th percentile CO2 concentration (ppm)
        co2_q75: 75th percentile CO2 concentration (ppm)
    """

    room_id: str
    stat_date: date
    in_day_num: int
    is_growth_phase: bool

    # Temperature statistics
    temp_median: Optional[float] = None
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    temp_q25: Optional[float] = None
    temp_q75: Optional[float] = None

    # Humidity statistics
    humidity_median: Optional[float] = None
    humidity_min: Optional[float] = None
    humidity_max: Optional[float] = None
    humidity_q25: Optional[float] = None
    humidity_q75: Optional[float] = None

    # CO2 statistics
    co2_median: Optional[float] = None
    co2_min: Optional[float] = None
    co2_max: Optional[float] = None
    co2_q25: Optional[float] = None
    co2_q75: Optional[float] = None


@dataclass
class DeviceChangeRecord:
    """
    Device setpoint change record from DeviceSetpointChange table

    Attributes:
        room_id: Room number
        device_type: Device type (e.g., air_cooler, fresh_air_fan)
        device_name: Device name/code
        point_name: Measurement point name
        change_time: Timestamp of the change
        previous_value: Value before change
        current_value: Value after change
        change_type: Type of change (increase/decrease/toggle)
        in_date: Batch entry date
        growth_day: Days since batch entry
        in_num: Batch entry number
        batch_id: Batch identifier
    """

    room_id: str
    device_type: str
    device_name: str
    point_name: str
    change_time: datetime
    previous_value: float
    current_value: float
    change_type: str
    in_date: Optional[date] = None
    growth_day: Optional[int] = None
    in_num: Optional[int] = None
    batch_id: Optional[str] = None


@dataclass
class SimilarCase:
    """
    Similar historical case found by CLIP matching

    Attributes:
        similarity_score: Similarity score 0-100
        confidence_level: Confidence level (high/medium/low)
        room_id: Room number of the historical case
        growth_day: Growth day of the historical case
        collection_time: Collection timestamp
        temperature: Temperature at collection time (°C)
        humidity: Humidity at collection time (%)
        co2: CO2 concentration at collection time (ppm)
        air_cooler_params: Air cooler parameters
        fresh_air_params: Fresh air fan parameters
        humidifier_params: Humidifier parameters
        grow_light_params: Grow light parameters
    """

    similarity_score: float  # 0-100
    confidence_level: str  # "high" | "medium" | "low"

    room_id: str
    growth_day: int
    collection_time: datetime

    # Environmental parameters
    temperature: float
    humidity: float
    co2: float

    # Device configurations
    air_cooler_params: Dict = field(default_factory=dict)
    fresh_air_params: Dict = field(default_factory=dict)
    humidifier_params: Dict = field(default_factory=dict)
    grow_light_params: Dict = field(default_factory=dict)


# ============================================================================
# Enhanced Output Data Models for Optimized Decision Analysis
# ============================================================================


@dataclass
class RiskAssessment:
    """
    Risk assessment for parameter adjustment

    Attributes:
        adjustment_risk: Risk level of making the adjustment
        no_action_risk: Risk level of not making the adjustment
        impact_scope: Scope of impact (e.g., temperature_stability, growth_rate)
        mitigation_measures: Suggested risk mitigation measures
    """

    adjustment_risk: str  # "low" | "medium" | "high" | "critical"
    no_action_risk: str  # "low" | "medium" | "high" | "critical"
    impact_scope: str
    mitigation_measures: List[str] = field(default_factory=list)


@dataclass
class ParameterAdjustment:
    """
    Individual parameter adjustment recommendation

    Attributes:
        current_value: Current parameter value
        recommended_value: Recommended parameter value
        action: Action to take ("maintain" | "adjust" | "monitor")
        change_reason: Detailed reason for the change/maintenance
        priority: Priority level ("low" | "medium" | "high" | "critical")
        urgency: Urgency level ("immediate" | "within_hour" | "within_day" | "routine")
        risk_assessment: Risk assessment for this parameter
        monitoring_threshold: Threshold for monitoring (if action is "monitor")
    """

    current_value: float
    recommended_value: float
    action: str  # "maintain" | "adjust" | "monitor"
    change_reason: str
    priority: str  # "low" | "medium" | "high" | "critical"
    urgency: str  # "immediate" | "within_hour" | "within_day" | "routine"
    risk_assessment: Optional[RiskAssessment] = None
    monitoring_threshold: Optional[Dict[str, float]] = None


@dataclass
class DynamicDeviceRecommendation:
    """
    Dynamic device parameter recommendations
    """

    parameters: Dict[str, ParameterAdjustment] = field(default_factory=dict)
    rationale: List[str] = field(default_factory=list)
    multi_image_analysis: str = ""


@dataclass
class EnhancedDeviceRecommendations:
    """
    Enhanced device parameter recommendations with detailed adjustment info
    """

    # Dynamic dictionary of devices: device_type -> DynamicDeviceRecommendation
    devices: Dict[str, DynamicDeviceRecommendation] = field(default_factory=dict)

    # Backward compatibility properties (optional, if needed for existing code access)
    @property
    def air_cooler(self) -> Optional[DynamicDeviceRecommendation]:
        return self.devices.get("air_cooler")

    @property
    def fresh_air_fan(self) -> Optional[DynamicDeviceRecommendation]:
        return self.devices.get("fresh_air_fan")

    @property
    def humidifier(self) -> Optional[DynamicDeviceRecommendation]:
        return self.devices.get("humidifier")

    @property
    def grow_light(self) -> Optional[DynamicDeviceRecommendation]:
        return self.devices.get("grow_light")


@dataclass
class MultiImageAnalysis:
    """
    Multi-image analysis results

    Attributes:
        total_images_analyzed: Total number of images analyzed
        image_quality_scores: Quality scores for each image
        aggregation_method: Method used to aggregate multiple images
        confidence_score: Confidence score of the multi-image analysis
        view_consistency: Consistency between different camera views
        key_observations: Key observations from multiple views
    """

    total_images_analyzed: int
    image_quality_scores: List[float] = field(default_factory=list)
    aggregation_method: str = "weighted_average"
    confidence_score: float = 0.0
    view_consistency: str = "high"  # "high" | "medium" | "low"
    key_observations: List[str] = field(default_factory=list)


@dataclass
class EnhancedDecisionOutput:
    """
    Enhanced decision output with detailed parameter adjustments
    """

    status: str  # "success" | "error"
    room_id: str
    analysis_time: datetime
    strategy: "ControlStrategy"
    device_recommendations: EnhancedDeviceRecommendations
    monitoring_points: "MonitoringPoints"
    multi_image_analysis: MultiImageAnalysis
    metadata: "DecisionMetadata"


@dataclass
class AirCoolerRecommendation:
    """
    Air cooler parameter recommendations

    Attributes:
        tem_set: Temperature setpoint (°C)
        tem_diff_set: Temperature difference setpoint (°C)
        cyc_on_off: Cycle on/off (0/1)
        cyc_on_time: Cycle on time (minutes)
        cyc_off_time: Cycle off time (minutes)
        ar_on_off: Fresh air linkage (0/1)
        hum_on_off: Humidifier linkage (0/1)
        rationale: List of reasoning statements
    """

    tem_set: float
    tem_diff_set: float
    cyc_on_off: int
    cyc_on_time: int
    cyc_off_time: int
    ar_on_off: int
    hum_on_off: int
    rationale: List[str] = field(default_factory=list)


@dataclass
class FreshAirFanRecommendation:
    """
    Fresh air fan parameter recommendations

    Attributes:
        model: Mode (0:off, 1:auto, 2:manual)
        control: Control method (0:time, 1:CO2)
        co2_on: CO2 start threshold (ppm)
        co2_off: CO2 stop threshold (ppm)
        on: On time (minutes, for time control mode)
        off: Off time (minutes, for time control mode)
        rationale: List of reasoning statements
    """

    model: int
    control: int
    co2_on: int
    co2_off: int
    on: int
    off: int
    rationale: List[str] = field(default_factory=list)


@dataclass
class HumidifierRecommendation:
    """
    Humidifier parameter recommendations

    Attributes:
        model: Mode (0:off, 1:auto, 2:manual)
        on: Start humidity threshold (%)
        off: Stop humidity threshold (%)
        left_right_strategy: Left/right side strategy description
        rationale: List of reasoning statements
    """

    model: int
    on: int
    off: int
    left_right_strategy: str = ""
    rationale: List[str] = field(default_factory=list)


@dataclass
class GrowLightRecommendation:
    """
    Grow light parameter recommendations

    Attributes:
        model: Mode (0:off, 1:auto, 2:manual)
        on_mset: On duration (minutes)
        off_mset: Off duration (minutes)
        on_off_1: Light #1 on/off (0/1)
        choose_1: Light #1 color selection (0:white, 1:blue)
        on_off_2: Light #2 on/off (0/1)
        choose_2: Light #2 color selection (0:white, 1:blue)
        on_off_3: Light #3 on/off (0/1)
        choose_3: Light #3 color selection (0:white, 1:blue)
        on_off_4: Light #4 on/off (0/1)
        choose_4: Light #4 color selection (0:white, 1:blue)
        rationale: List of reasoning statements
    """

    model: int
    on_mset: int
    off_mset: int
    on_off_1: int
    choose_1: int
    on_off_2: int
    choose_2: int
    on_off_3: int
    choose_3: int
    on_off_4: int
    choose_4: int
    rationale: List[str] = field(default_factory=list)


@dataclass
class DeviceRecommendations:
    """
    All device parameter recommendations

    Attributes:
        air_cooler: Air cooler recommendations
        fresh_air_fan: Fresh air fan recommendations
        humidifier: Humidifier recommendations
        grow_light: Grow light recommendations
    """

    air_cooler: AirCoolerRecommendation
    fresh_air_fan: FreshAirFanRecommendation
    humidifier: HumidifierRecommendation
    grow_light: GrowLightRecommendation


@dataclass
class ControlStrategy:
    """
    Overall control strategy

    Attributes:
        core_objective: Core objective of the control strategy
        priority_ranking: Priority ranking of control actions
        key_risk_points: Key risk points to monitor
    """

    core_objective: str
    priority_ranking: List[str] = field(default_factory=list)
    key_risk_points: List[str] = field(default_factory=list)


@dataclass
class MonitoringPoints:
    """
    24-hour monitoring points

    Attributes:
        key_time_periods: Key time periods to monitor
        warning_thresholds: Warning thresholds for parameters
        emergency_measures: Emergency measures to take
    """

    key_time_periods: List[str] = field(default_factory=list)
    warning_thresholds: Dict[str, float] = field(default_factory=dict)
    emergency_measures: List[str] = field(default_factory=list)


@dataclass
class DecisionMetadata:
    """
    Decision metadata

    Attributes:
        data_sources: Data sources and record counts
        similar_cases_count: Number of similar cases found
        avg_similarity_score: Average similarity score
        llm_model: LLM model used
        llm_response_time: LLM response time (seconds)
        total_processing_time: Total processing time (seconds)
        warnings: List of warning messages
        errors: List of error messages
        device_config_metadata: Device configuration adaptation metadata
        multi_image_count: Number of images analyzed (for enhanced analysis)
        image_aggregation_method: Method used for image aggregation (for enhanced analysis)
    """

    data_sources: Dict[str, int] = field(default_factory=dict)
    similar_cases_count: int = 0
    avg_similarity_score: float = 0.0
    llm_model: str = ""
    llm_response_time: float = 0.0
    total_processing_time: float = 0.0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    device_config_metadata: Dict = field(default_factory=dict)
    multi_image_count: int = 0
    image_aggregation_method: str = ""


@dataclass
class DecisionOutput:
    """
    Complete decision output

    Attributes:
        status: Status (success/error)
        room_id: Room number
        analysis_time: Analysis timestamp
        strategy: Overall control strategy
        device_recommendations: Device parameter recommendations
        monitoring_points: Monitoring points
        metadata: Decision metadata
    """

    status: str  # "success" | "error"
    room_id: str
    analysis_time: datetime
    strategy: ControlStrategy
    device_recommendations: DeviceRecommendations
    monitoring_points: MonitoringPoints
    metadata: DecisionMetadata


# ============================================================================
# Configuration Data Models
# ============================================================================


@dataclass
class DevicePointConfig:
    """
    Device measurement point configuration

    Attributes:
        point_name: Point name/code
        point_alias: Point display name
        remark: Description/remark
        enum: Enumeration value mapping (optional)
        value_range: Value range (min, max) (optional)
    """

    point_name: str
    point_alias: str
    remark: str
    enum: Optional[Dict[str, str]] = None
    value_range: Optional[tuple] = None


@dataclass
class DeviceConfig:
    """
    Device configuration

    Attributes:
        device_type: Device type
        device_name: Device name/code
        device_alias: Device display name
        remark: Description/remark
        point_list: List of measurement point configurations
    """

    device_type: str
    device_name: str
    device_alias: str
    remark: str
    point_list: List[DevicePointConfig] = field(default_factory=list)
