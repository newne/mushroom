"""
Template Renderer Module

This module is responsible for rendering the decision prompt template using Python format strings.
It maps extracted data to template variables and formats device configurations
according to static_config.json definitions.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

from decision_analysis.data_models import SimilarCase


class TemplateRenderer:
    """
    Template renderer for decision prompts
    
    Renders Jinja2 templates with extracted data, mapping database values
    to template variables and formatting device configurations.
    """
    
    def __init__(self, template_path: str, static_config: Dict, monitoring_points_config: Dict = None):
        """
        Initialize template renderer
        
        Args:
            template_path: Path to decision_prompt.jinja template file
            static_config: Static configuration dictionary from static_config.json
            monitoring_points_config: Monitoring points configuration dictionary
            
        Requirements: 6.1, 6.2
        """
        self.template_path = Path(template_path)
        self.static_config = static_config
        self.monitoring_points_config = monitoring_points_config
        
        # Load template file
        if not self.template_path.exists():
            raise FileNotFoundError(
                f"Template file not found: {self.template_path}"
            )
        
        # Read template content (using Python format strings, not Jinja2)
        with open(self.template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
        
        # Escape literal braces that aren't template variables
        # A valid variable name for our purposes is ASCII-only: starts with letter/underscore,
        # followed by letters, digits, or underscores
        import re
        
        def escape_literal_braces(match):
            content = match.group(1)
            # Check if it's a valid ASCII identifier (our template variables)
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', content):
                return match.group(0)  # Keep as is - it's a variable
            else:
                # Escape by doubling the braces - it's literal text
                return '{{' + content + '}}'
        
        self.template_content = re.sub(r'\{([^}]+)\}', escape_literal_braces, template_content)
        
        # Build enum mapping cache for faster lookups
        self._build_enum_cache()
        
        logger.info(f"[TemplateRenderer] Initialized with template: {template_path}")
    
    def _build_enum_cache(self):
        """Build cache of enum mappings for faster lookup"""
        self.enum_cache = {}
        if not self.monitoring_points_config:
            logger.warning("[TemplateRenderer] No monitoring points config provided")
            return
            
        logger.debug(f"[TemplateRenderer] Building enum cache from config with keys: {list(self.monitoring_points_config.keys())}")
        
        for device_id, device_info in self.monitoring_points_config.items():
            # Skip non-device keys like "room_id"
            if not isinstance(device_info, dict) or "point_list" not in device_info:
                continue
                
            device_type = device_info.get("device_type")
            
            self.enum_cache[device_type] = {}
            point_list = device_info.get("point_list", [])
            
            for point in point_list:
                point_alias = point.get("point_alias", "")
                if "enum" in point:
                    self.enum_cache[device_type][point_alias] = point["enum"]
        
        logger.debug(f"[TemplateRenderer] Built enum cache for {len(self.enum_cache)} device types")
    
    def render(
        self,
        current_data: Dict,
        env_stats: pd.DataFrame,
        device_changes: pd.DataFrame,
        similar_cases: List[SimilarCase]
    ) -> str:
        """
        Render decision prompt template
        
        Args:
            current_data: Current state data dictionary
            env_stats: Environmental statistics DataFrame
            device_changes: Device change records DataFrame
            similar_cases: List of similar cases
            
        Returns:
            Rendered prompt text
            
        Requirements: 6.3, 6.4, 6.5
        """
        logger.info("[TemplateRenderer] Rendering decision prompt template")
        
        try:
            # Map data to template variables
            template_vars = self._map_variables(
                current_data=current_data,
                env_stats=env_stats,
                device_changes=device_changes,
                similar_cases=similar_cases
            )
            
            # Render template using Python format strings
            rendered_text = self.template_content.format(**template_vars)
            
            logger.info(
                f"[TemplateRenderer] Successfully rendered template "
                f"(length: {len(rendered_text)} chars)"
            )
            
            return rendered_text
            
        except KeyError as e:
            logger.error(f"[TemplateRenderer] Missing template variable: {e}")
            raise
        except Exception as e:
            logger.error(f"[TemplateRenderer] Unexpected error during rendering: {e}")
            raise
    
    def _map_variables(
        self,
        current_data: Dict,
        env_stats: pd.DataFrame,
        device_changes: pd.DataFrame,
        similar_cases: List[SimilarCase]
    ) -> Dict:
        """
        Map data to template variables
        
        Maps:
        - Current state data to template variables
        - Device configurations to template variables
        - Similar cases to case1/case2/case3 variables
        - Historical statistics to formatted text
        
        Args:
            current_data: Current state data
            env_stats: Environmental statistics
            device_changes: Device changes
            similar_cases: Similar cases
            
        Returns:
            Dictionary of template variables
            
        Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
        """
        logger.debug("[TemplateRenderer] Mapping data to template variables")
        
        variables = {}
        
        # 1. Map current environment data
        variables.update(self._map_current_environment(current_data))
        
        # 2. Map device configurations
        variables.update(self._map_device_configs(current_data))
        
        # 3. Map similar cases
        variables.update(self._map_similar_cases(similar_cases))
        
        # 4. Map historical data
        variables["historical_data"] = self._format_historical_data(
            env_stats, device_changes
        )
        
        # 5. Add placeholder for documentation examples
        variables["xxx"] = "变量名"  # Placeholder used in template documentation
        
        logger.debug(f"[TemplateRenderer] Mapped {len(variables)} template variables")
        
        return variables
    
    def _map_current_environment(self, current_data: Dict) -> Dict:
        """Map current environment data to template variables"""
        now = datetime.now()
        
        # Determine season based on month
        month = now.month
        if month in [3, 4, 5]:
            season = "春"
        elif month in [6, 7, 8]:
            season = "夏"
        elif month in [9, 10, 11]:
            season = "秋"
        else:
            season = "冬"
        
        # Calculate warning thresholds based on current values
        current_temp = current_data.get("temperature", 16)
        current_humidity = current_data.get("humidity", 85)
        current_co2 = current_data.get("co2", 1200)
        
        return {
            # System configuration
            "room_id": current_data.get("room_id", "数据缺失"),
            "current_datetime": now.strftime("%Y-%m-%d %H:%M"),
            "current_hour": now.hour,
            "season": season,
            
            # Current environment
            "current_temp": current_temp,
            "current_humidity": current_humidity,
            "current_co2": current_co2,
            "growth_stage": current_data.get("semantic_description", "数据缺失"),
            
            # Batch information
            "in_year": current_data.get("in_year", "数据缺失"),
            "in_month": current_data.get("in_month", "数据缺失"),
            "in_day": current_data.get("in_day", "数据缺失"),
            "in_day_num": current_data.get("in_day_num", "数据缺失"),
            "in_num": current_data.get("in_num", "数据缺失"),
            
            # Warning thresholds (calculated based on current values)
            "temp_warning": current_temp + 2 if isinstance(current_temp, (int, float)) else "数据缺失",
            "humidity_warning": current_humidity - 5 if isinstance(current_humidity, (int, float)) else "数据缺失",
            "co2_warning": current_co2 + 300 if isinstance(current_co2, (int, float)) else "数据缺失",
        }
    
    def _map_device_configs(self, current_data: Dict) -> Dict:
        """Map device configurations to template variables"""
        variables = {}
        
        room_id = current_data.get("room_id", "")
        
        # Get device aliases for this room
        variables.update(self._get_device_aliases(room_id))
        
        # Map air cooler configuration
        air_cooler_config = current_data.get("air_cooler_config", {})
        formatted_air_cooler = self._format_device_config(air_cooler_config, "air_cooler")
        variables.update({
            "air_cooler_status": formatted_air_cooler.get("status", "数据缺失"),
            "air_cooler_temp_set": formatted_air_cooler.get("temp_set", "数据缺失"),
            "air_cooler_temp_diff": formatted_air_cooler.get("temp_diffset", "数据缺失"),
            "air_cooler_cyc_mode": formatted_air_cooler.get("cyc_on_off", "数据缺失"),
        })
        
        # Map fresh air fan configuration
        fresh_fan_config = current_data.get("fresh_fan_config", {})
        formatted_fresh_fan = self._format_device_config(fresh_fan_config, "fresh_air_fan")
        variables.update({
            "fresh_air_mode": formatted_fresh_fan.get("mode", "数据缺失"),
            "fresh_air_control": formatted_fresh_fan.get("control", "数据缺失"),
            "fresh_air_co2_on": formatted_fresh_fan.get("co2_on", "数据缺失"),
            "fresh_air_co2_off": formatted_fresh_fan.get("co2_off", "数据缺失"),
            "fresh_air_time_on": formatted_fresh_fan.get("on", "数据缺失"),
            "fresh_air_time_off": formatted_fresh_fan.get("off", "数据缺失"),
        })
        
        # Map humidifier configuration
        humidifier_config = current_data.get("humidifier_config", {})
        formatted_humidifier = self._format_device_config(humidifier_config, "humidifier")
        variables.update({
            "humidifier_mode": formatted_humidifier.get("mode", "数据缺失"),
            "humidifier_on": formatted_humidifier.get("on", "数据缺失"),
            "humidifier_off": formatted_humidifier.get("off", "数据缺失"),
        })
        
        # Map grow light configuration
        light_config = current_data.get("light_config", {})
        formatted_light = self._format_device_config(light_config, "grow_light")
        variables.update({
            "grow_light_model": formatted_light.get("model", "数据缺失"),
            "grow_light_on_time": formatted_light.get("on_mset", "数据缺失"),
            "grow_light_off_time": formatted_light.get("off_mset", "数据缺失"),
            "grow_light_config": self._format_light_config(formatted_light),
        })
        
        return variables
    
    def _get_device_aliases(self, room_id: str) -> Dict:
        """Get device aliases for a specific room"""
        datapoint = self.static_config.get("mushroom", {}).get("datapoint", {})
        
        aliases = {
            "air_cooler_alias": "冷风机",
            "fresh_air_fan_alias": "新风机",
            "left_humidifier_alias": "左加湿器",
            "right_humidifier_alias": "右加湿器",
            "grow_light_alias": "补光灯",
        }
        
        # Try to get specific aliases from config
        for device_type in ["air_cooler", "fresh_air_fan", "humidifier", "grow_light"]:
            device_config = datapoint.get(device_type, {})
            device_list = device_config.get("device_list", [])
            
            for device in device_list:
                device_alias = device.get("device_alias", "")
                if room_id in device_alias:
                    remark = device.get("remark", "")
                    
                    if device_type == "air_cooler":
                        aliases["air_cooler_alias"] = remark
                    elif device_type == "fresh_air_fan":
                        aliases["fresh_air_fan_alias"] = remark
                    elif device_type == "humidifier":
                        if "left" in device_alias.lower() or "左" in remark:
                            aliases["left_humidifier_alias"] = remark
                        elif "right" in device_alias.lower() or "右" in remark:
                            aliases["right_humidifier_alias"] = remark
                    elif device_type == "grow_light":
                        aliases["grow_light_alias"] = remark
        
        return aliases
    
    def _format_light_config(self, formatted_light: Dict) -> str:
        """Format grow light configuration as readable text"""
        config_parts = []
        
        for i in range(1, 5):
            on_off = formatted_light.get(f"on_off{i}", "")
            choose = formatted_light.get(f"choose{i}", "")
            
            if on_off and choose:
                config_parts.append(f"{i}#{on_off}/{choose}")
        
        return ", ".join(config_parts) if config_parts else "数据缺失"
    
    def _map_similar_cases(self, similar_cases: List[SimilarCase]) -> Dict:
        """Map similar cases to case1/case2/case3 variables"""
        variables = {}
        
        # Add summary variables for top case
        if similar_cases:
            top_case = similar_cases[0]
            variables.update({
                "top_case_id": f"案例1",
                "similarity_top": f"{top_case.similarity_score:.1f}",
                "case_temp": top_case.temperature,
                "temp_deviation": "待计算",  # Placeholder for LLM to fill
                "summary_of_cases": "参考Top-3案例的CO2控制策略",  # Placeholder
            })
        else:
            variables.update({
                "top_case_id": "无",
                "similarity_top": "0",
                "case_temp": "数据缺失",
                "temp_deviation": "数据缺失",
                "summary_of_cases": "无相似案例",
            })
        
        # Ensure we have exactly 3 cases (pad with empty data if needed)
        for i in range(1, 4):
            if i <= len(similar_cases):
                case = similar_cases[i - 1]
                prefix = f"case{i}"
                
                variables.update({
                    f"similarity_{i}": f"{case.similarity_score:.1f}",
                    f"{prefix}_room": case.room_id,
                    f"{prefix}_stage": case.growth_day,
                    f"{prefix}_temp": case.temperature,
                    f"{prefix}_humidity": case.humidity,
                    f"{prefix}_co2": case.co2,
                    f"{prefix}_air_cooler_params": self._format_case_params(
                        case.air_cooler_params, "air_cooler"
                    ),
                    f"{prefix}_fresh_air_params": self._format_case_params(
                        case.fresh_air_params, "fresh_air_fan"
                    ),
                    f"{prefix}_humidifier_params": self._format_case_params(
                        case.humidifier_params, "humidifier"
                    ),
                    f"{prefix}_grow_light_params": self._format_case_params(
                        case.grow_light_params, "grow_light"
                    ),
                    f"{prefix}_yield": "数据缺失",  # Yield data not available yet
                })
            else:
                # Pad with missing data
                prefix = f"case{i}"
                variables.update({
                    f"similarity_{i}": "0.0",
                    f"{prefix}_room": "数据缺失",
                    f"{prefix}_stage": "数据缺失",
                    f"{prefix}_temp": "数据缺失",
                    f"{prefix}_humidity": "数据缺失",
                    f"{prefix}_co2": "数据缺失",
                    f"{prefix}_air_cooler_params": "数据缺失",
                    f"{prefix}_fresh_air_params": "数据缺失",
                    f"{prefix}_humidifier_params": "数据缺失",
                    f"{prefix}_grow_light_params": "数据缺失",
                    f"{prefix}_yield": "数据缺失",
                })
        
        return variables
    
    def _format_case_params(self, params: Dict, device_type: str) -> str:
        """Format case parameters as readable text"""
        if not params:
            return "数据缺失"
        
        formatted = self._format_device_config(params, device_type)
        
        # Create a concise summary of key parameters
        if device_type == "air_cooler":
            return f"温度{formatted.get('temp_set', '?')}℃, 温差{formatted.get('temp_diffset', '?')}℃"
        elif device_type == "fresh_air_fan":
            return f"模式{formatted.get('mode', '?')}, CO2阈值{formatted.get('co2_on', '?')}/{formatted.get('co2_off', '?')}ppm"
        elif device_type == "humidifier":
            return f"模式{formatted.get('mode', '?')}, 湿度阈值{formatted.get('on', '?')}/{formatted.get('off', '?')}%"
        elif device_type == "grow_light":
            return f"模式{formatted.get('model', '?')}, 时长{formatted.get('on_mset', '?')}/{formatted.get('off_mset', '?')}分钟"
        
        return str(formatted)
    
    def _format_historical_data(
        self, 
        env_stats: pd.DataFrame, 
        device_changes: pd.DataFrame
    ) -> str:
        """Format historical data as readable text"""
        lines = []
        
        # Format environmental statistics
        if not env_stats.empty:
            lines.append("**环境统计数据:**")
            
            for _, row in env_stats.iterrows():
                date_str = row.get("stat_date", "")
                day_num = row.get("in_day_num", "")
                
                temp_median = row.get("temp_median", None)
                humidity_median = row.get("humidity_median", None)
                co2_median = row.get("co2_median", None)
                
                if pd.notna(temp_median) and pd.notna(humidity_median):
                    co2_str = f"{co2_median:.0f}" if pd.notna(co2_median) else "?"
                    lines.append(
                        f"- {date_str} (进库第{day_num}天): "
                        f"温度{temp_median:.1f}℃, 湿度{humidity_median:.1f}%, "
                        f"CO2 {co2_str}ppm"
                    )
        
        # Format device changes
        if not device_changes.empty:
            lines.append("\n**设备变更记录:**")
            
            # Group by device type
            for device_type in device_changes["device_type"].unique():
                device_df = device_changes[device_changes["device_type"] == device_type]
                lines.append(f"- {device_type}:")
                
                for _, row in device_df.head(3).iterrows():  # Show top 3 changes
                    change_time = row.get("change_time", "")
                    point_name = row.get("point_name", "")
                    prev_val = row.get("previous_value", "")
                    curr_val = row.get("current_value", "")
                    
                    lines.append(
                        f"  * {change_time}: {point_name} "
                        f"{prev_val} → {curr_val}"
                    )
        
        if not lines:
            return "暂无历史数据"
        
        return "\n".join(lines)
    
    def _format_device_config(self, config: Dict, device_type: str) -> Dict:
        """
        Format device configuration, mapping enumeration values
        
        Maps numeric enumeration values to readable text based on
        static_config.json definitions.
        
        Args:
            config: Device configuration JSON
            device_type: Device type (air_cooler, fresh_air_fan, etc.)
            
        Returns:
            Formatted configuration dictionary
            
        Requirements: 5.2, 11.3
        """
        if not config:
            return {}
        
        formatted = {}
        enum_map = self.enum_cache.get(device_type, {})
        
        for key, value in config.items():
            # Check if this field has an enum mapping
            if key in enum_map:
                # Convert numeric value to string for lookup
                value_str = str(int(value)) if isinstance(value, (int, float)) else str(value)
                
                # Map to readable text
                formatted[key] = enum_map[key].get(value_str, value)
            else:
                # Keep original value for non-enum fields
                formatted[key] = value
        
        return formatted
    
    def render_enhanced(
        self,
        current_data: Dict,
        env_stats: pd.DataFrame,
        device_changes: pd.DataFrame,
        similar_cases: List[SimilarCase],
        multi_image_analysis: Optional["MultiImageAnalysis"] = None
    ) -> str:
        """
        Render enhanced decision prompt template with multi-image context
        
        This enhanced version includes multi-image analysis context in the prompt,
        providing information about image aggregation and consistency.
        
        Args:
            current_data: Current state data dictionary
            env_stats: Environmental statistics DataFrame
            device_changes: Device change records DataFrame
            similar_cases: List of similar cases
            multi_image_analysis: Multi-image analysis results
            
        Returns:
            Rendered enhanced prompt text with multi-image context
            
        Requirements: Enhanced decision analysis with multi-image support
        """
        logger.info("[TemplateRenderer] Rendering enhanced decision prompt template with multi-image context")
        
        try:
            # Map data to template variables (same as regular render)
            template_vars = self._map_variables(
                current_data=current_data,
                env_stats=env_stats,
                device_changes=device_changes,
                similar_cases=similar_cases
            )
            
            # Add multi-image context if available
            if multi_image_analysis:
                template_vars.update(self._map_multi_image_context(multi_image_analysis))
            else:
                # Add default multi-image context
                template_vars.update({
                    "multi_image_count": 1,
                    "image_aggregation_info": "单图像分析",
                    "image_consistency_info": "N/A",
                    "camera_coverage_info": "单相机视角"
                })
            
            # Add dynamic device sections
            template_vars.update({
                "device_status_section": self._generate_device_status_section(current_data),
                "device_constraints_section": self._generate_device_constraints_section()
            })
            
            # Render template using Python format strings
            rendered_text = self.template_content.format(**template_vars)
            
            logger.info(
                f"[TemplateRenderer] Successfully rendered enhanced template "
                f"(length: {len(rendered_text)} chars, multi-image: {multi_image_analysis is not None})"
            )
            
            return rendered_text
            
        except KeyError as e:
            logger.error(f"[TemplateRenderer] Missing template variable in enhanced render: {e}")
            # Fallback to regular render
            logger.warning("[TemplateRenderer] Falling back to regular render")
            return self.render(current_data, env_stats, device_changes, similar_cases)
        except Exception as e:
            logger.error(f"[TemplateRenderer] Unexpected error during enhanced rendering: {e}")
            raise
    
    def _map_multi_image_context(self, multi_image_analysis: "MultiImageAnalysis") -> Dict:
        """
        Map multi-image analysis to template variables
        
        Args:
            multi_image_analysis: Multi-image analysis results
            
        Returns:
            Dictionary of multi-image template variables
        """
        logger.debug("[TemplateRenderer] Mapping multi-image context to template variables")
        
        # Format image aggregation information
        aggregation_info = (
            f"聚合了{multi_image_analysis.total_images_analyzed}张图像 "
            f"(方法: {multi_image_analysis.aggregation_method})"
        )
        
        # Format consistency information
        consistency_score = multi_image_analysis.confidence_score
        if consistency_score >= 0.8:
            consistency_level = "高"
        elif consistency_score >= 0.6:
            consistency_level = "中"
        else:
            consistency_level = "低"
        
        consistency_info = f"图像一致性: {consistency_level} (分数: {consistency_score:.2f})"
        
        # Format view consistency information
        view_consistency_map = {"high": "高", "medium": "中", "low": "低"}
        view_consistency_cn = view_consistency_map.get(multi_image_analysis.view_consistency, "未知")
        camera_coverage = f"视角一致性: {view_consistency_cn}"
        
        # Format quality information
        quality_scores = multi_image_analysis.image_quality_scores
        if quality_scores:
            avg_quality = sum(quality_scores) / len(quality_scores)
            min_quality = min(quality_scores)
            max_quality = max(quality_scores)
            quality_info = f"图像质量: 平均{avg_quality:.2f}, 范围[{min_quality:.2f}, {max_quality:.2f}]"
        else:
            quality_info = "图像质量: 未评估"
        
        return {
            "multi_image_count": multi_image_analysis.total_images_analyzed,
            "image_aggregation_info": aggregation_info,
            "image_consistency_info": consistency_info,
            "camera_coverage_info": camera_coverage,
            "image_quality_info": quality_info
        }

    def _generate_device_status_section(self, current_data: Dict) -> str:
        """Generate dynamic device status section"""
        if not self.monitoring_points_config:
            return "设备状态配置缺失"
        
        lines = []
        
        config_key_map = {
            "air_cooler": "air_cooler_config",
            "fresh_air_fan": "fresh_fan_config",
            "humidifier": "humidifier_config",
            "grow_light": "light_config"
        }
        
        for device_id, device_info in self.monitoring_points_config.items():
            if not isinstance(device_info, dict) or "point_list" not in device_info:
                continue
                
            device_type = device_info.get("device_type")
            device_alias = device_info.get("device_alias", device_id)
            point_list = device_info.get("point_list", [])
            
            config_key = config_key_map.get(device_type)
            device_config = current_data.get(config_key, {})
            
            # Format status string
            status_parts = []
            for point in point_list:
                alias = point.get("point_alias")
                remark = point.get("remark", alias)
                
                # Try to find value in device_config
                # device_config keys might match point_alias
                val = device_config.get(alias)
                if val is None:
                     # Try mapping aliases if needed, but for now assume consistency
                     val = "未知"
                
                # Enum mapping
                enum_mapping = point.get("enum_mapping")
                if enum_mapping and val != "未知":
                    val_str = str(int(val)) if isinstance(val, (int, float)) else str(val)
                    val = enum_mapping.get(val_str, val)
                
                status_parts.append(f"{remark}: {val}")
            
            lines.append(f"   - {device_alias} ({device_type}):")
            lines.append(f"     {', '.join(status_parts)}")
            
        return "\n".join(lines)

    def _generate_device_constraints_section(self) -> str:
        """Generate dynamic device constraints section"""
        if not self.monitoring_points_config:
            return "设备约束配置缺失"
            
        lines = []
        
        for device_id, device_info in self.monitoring_points_config.items():
            if not isinstance(device_info, dict) or "point_list" not in device_info:
                continue

            device_type = device_info.get("device_type")
            device_alias = device_info.get("device_alias", device_id)
            point_list = device_info.get("point_list", [])

            lines.append(f"- **{device_alias} ({device_type})**:")
            for point in point_list:
                alias = point.get("point_alias")
                remark = point.get("remark", alias)
                change_type = point.get("change_type", "unknown")
                
                constraint_desc = f"{remark}"
                if change_type == "enum_state":
                    enum_mapping = point.get("enum_mapping", {})
                    if enum_mapping:
                        options = "/".join([f"{k}={v}" for k, v in enum_mapping.items()])
                        constraint_desc += f" (可选: {options})"
                elif change_type == "analog_value":
                    threshold = point.get("threshold")
                    if threshold:
                        constraint_desc += f" (阈值: {threshold})"
                
                lines.append(f"  - {alias}: {constraint_desc}")
            lines.append("  - rationale: 判断依据数组（3-5条字符串）")
            lines.append("") # Empty line for spacing
            
        return "\n".join(lines)
