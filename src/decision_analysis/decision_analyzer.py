"""
Decision Analyzer Module

This is the main controller that orchestrates the entire decision analysis workflow.
It coordinates data extraction, CLIP matching, template rendering, LLM calling,
and output validation.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

from dynaconf import Dynaconf
from loguru import logger
from sqlalchemy import Engine

from decision_analysis.clip_matcher import CLIPMatcher
from decision_analysis.data_extractor import DataExtractor
from decision_analysis.data_models import DecisionOutput, MultiImageAnalysis
from decision_analysis.llm_client import LLMClient
from decision_analysis.output_handler import OutputHandler
from decision_analysis.template_renderer import TemplateRenderer


class DecisionAnalyzer:
    """
    Main decision analyzer controller

    Orchestrates the complete decision analysis workflow:
    1. Extract data from database
    2. Find similar historical cases
    3. Render decision prompt
    4. Call LLM for decision generation
    5. Validate and format output
    """

    def __init__(
        self,
        db_engine: Engine,
        settings: Dynaconf,
        static_config: Dict,
        template_path: str,
    ):
        """
        Initialize decision analyzer

        Initializes all sub-components:
        - DataExtractor: For extracting data from database
        - CLIPMatcher: For finding similar historical cases
        - TemplateRenderer: For rendering decision prompts
        - LLMClient: For calling LLM API
        - OutputHandler: For validating and formatting output

        Args:
            db_engine: SQLAlchemy database engine (pgsql_engine)
            settings: Dynaconf configuration object
            static_config: Static configuration dictionary
            template_path: Path to decision_prompt.jinja template

        Requirements: 12.1
        """
        logger.info("[DecisionAnalyzer] 正在初始化决策分析器...")

        # Import decision analysis configuration
        from global_const.const_config import DECISION_ANALYSIS_CONFIG

        self.db_engine = db_engine
        self.settings = settings
        self.static_config = static_config
        self.template_path = template_path
        self.decision_config = DECISION_ANALYSIS_CONFIG

        # Load monitoring points config
        try:
            config_path = (
                Path(__file__).parent.parent
                / "configs"
                / "monitoring_points_config.json"
            )
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    self.monitoring_points_config = json.load(f)
                logger.info(f"[DecisionAnalyzer] 已加载监控点配置: {config_path}")
            else:
                logger.warning(f"[DecisionAnalyzer] 未找到监控点配置: {config_path}")
                self.monitoring_points_config = {}
        except Exception as e:
            logger.error(f"[DecisionAnalyzer] 加载监控点配置失败: {e}")
            self.monitoring_points_config = {}

        # Initialize all components with error handling
        try:
            logger.debug("[DecisionAnalyzer] 正在初始化 DataExtractor...")
            self.data_extractor = DataExtractor(db_engine)

            logger.debug("[DecisionAnalyzer] 正在初始化 CLIPMatcher...")
            self.clip_matcher = CLIPMatcher(db_engine)

            logger.debug("[DecisionAnalyzer] 正在初始化 TemplateRenderer...")
            self.template_renderer = TemplateRenderer(
                template_path, static_config, self.monitoring_points_config
            )

            logger.debug("[DecisionAnalyzer] 正在初始化 LLMClient...")
            self.llm_client = LLMClient(settings)

            logger.debug("[DecisionAnalyzer] 正在初始化 OutputHandler...")
            self.output_handler = OutputHandler(
                static_config, self.monitoring_points_config
            )

            logger.debug("[DecisionAnalyzer] 正在初始化 DeviceConfigAdapter...")
            from decision_analysis.device_config_adapter import (
                create_device_config_adapter,
            )

            self.device_config_adapter = create_device_config_adapter()

            logger.info("[DecisionAnalyzer] 所有组件初始化成功")
            logger.info(
                f"[DecisionAnalyzer] 支持的设备类型: {self.device_config_adapter.get_supported_device_types()}"
            )

        except Exception as e:
            logger.error(f"[DecisionAnalyzer] 组件初始化失败: {e}", exc_info=True)
            raise

    def analyze(self, room_id: str, analysis_datetime: datetime) -> DecisionOutput:
        """
        Execute complete decision analysis workflow

        Orchestrates the complete workflow:
        1. Extract current state data, env stats, device changes
        2. Find similar historical cases using CLIP
        3. Render decision prompt template
        4. Call LLM to generate decision
        5. Validate and format output

        Each step includes error handling and logging. If a step fails,
        the system attempts to continue with degraded functionality.

        Args:
            room_id: Room number (607/608/611/612)
            analysis_datetime: Analysis timestamp

        Returns:
            DecisionOutput with complete decision recommendations

        Requirements: All requirements (integrated workflow)
        """
        start_time = time.time()

        logger.info("[DecisionAnalyzer] ==========================================")
        logger.info("[DecisionAnalyzer] 开始执行决策分析")
        logger.info(f"[DecisionAnalyzer] 库房编号: {room_id}")
        logger.info(f"[DecisionAnalyzer] 分析时间: {analysis_datetime}")
        logger.info("[DecisionAnalyzer] ==========================================")

        # Initialize metadata tracking
        metadata = {
            "data_sources": {},
            "similar_cases_count": 0,
            "avg_similarity_score": 0.0,
            "llm_model": self.settings.llama.model,
            "llm_response_time": 0.0,
            "total_processing_time": 0.0,
            "warnings": [],
            "errors": [],
        }

        # Variables to hold extracted data
        current_data = {}
        env_stats = None
        device_changes = None
        similar_cases = []
        rendered_prompt = ""
        llm_decision = {}

        # ====================================================================
        # STEP 1: Extract Data from Database
        # ====================================================================
        logger.info("[DecisionAnalyzer] 步骤 1: 从数据库提取数据...")
        step1_start = time.time()

        try:
            # Extract current embedding data
            logger.info("[DecisionAnalyzer] 正在提取当前图像和Embedding数据...")
            embedding_df = self.data_extractor.extract_current_embedding_data(
                room_id=room_id,
                target_datetime=analysis_datetime,
                time_window_days=7,
                growth_day_window=3,
            )

            if embedding_df.empty:
                error_msg = f"未找到库房 {room_id} 的Embedding数据"
                logger.error(f"[DecisionAnalyzer] {error_msg}")
                metadata["errors"].append(error_msg)
                metadata["warnings"].append("由于缺失数据，将使用降级策略")
            else:
                # Use the most recent record
                latest_record = embedding_df.iloc[0]

                # Extract environmental sensor status
                env_sensor_status = latest_record.get("env_sensor_status", {})

                # Build current_data dictionary
                current_data = {
                    "room_id": room_id,
                    "collection_datetime": latest_record.get("collection_datetime"),
                    "in_date": latest_record.get("in_date"),
                    "in_num": latest_record.get("in_num"),
                    "growth_day": latest_record.get("growth_day"),
                    "in_day_num": latest_record.get("growth_day"),  # Same as growth_day
                    "in_year": latest_record.get("in_date").year
                    if latest_record.get("in_date")
                    else None,
                    "in_month": latest_record.get("in_date").month
                    if latest_record.get("in_date")
                    else None,
                    "in_day": latest_record.get("in_date").day
                    if latest_record.get("in_date")
                    else None,
                    "temperature": env_sensor_status.get("temperature", 0.0),
                    "humidity": env_sensor_status.get("humidity", 0.0),
                    "co2": env_sensor_status.get("co2", 0.0),
                    "embedding": latest_record.get("embedding"),
                    "semantic_description": latest_record.get(
                        "semantic_description", ""
                    ),
                    "llama_description": latest_record.get("llama_description"),
                    "image_quality_score": latest_record.get("image_quality_score"),
                    "air_cooler_config": latest_record.get("air_cooler_config", {}),
                    "fresh_fan_config": latest_record.get("fresh_fan_config", {}),
                    "humidifier_config": latest_record.get("humidifier_config", {}),
                    "light_config": latest_record.get("light_config", {}),
                }

                metadata["data_sources"]["embedding_records"] = len(embedding_df)
                logger.info(
                    f"[DecisionAnalyzer] 提取到 {len(embedding_df)} 条Embedding记录, "
                    f"使用 {current_data['collection_datetime']} 的最新记录"
                )

                # Validate environmental parameters
                validation_warnings = self.data_extractor.validate_env_params(
                    embedding_df
                )
                if validation_warnings:
                    metadata["warnings"].extend(validation_warnings)

            # Extract environmental statistics using real-time data interface
            logger.info("[DecisionAnalyzer] 正在提取环境统计数据...")
            target_date = analysis_datetime.date()

            # 使用实时历史数据接口获取当天环境数据
            # 由于统计天级温湿度数据是批处理任务，只有昨天及以前的历史统计数据，
            # 无法获取当天的温湿度数据，因此当天的温湿度数据通过实时历史数据查询接口进行获取
            env_stats = self.data_extractor.extract_realtime_env_data(
                room_id=room_id, target_datetime=analysis_datetime
            )

            if env_stats.empty:
                warning_msg = f"库房 {room_id} 未找到环境统计数据"
                logger.error(f"[DecisionAnalyzer] 提取环境数据失败: {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["env_stats_records"] = len(env_stats)
                logger.info(
                    f"[DecisionAnalyzer] 环境数据提取完成，共获取 {len(env_stats)} 条统计记录"
                )

            # Extract device change records
            logger.info("[DecisionAnalyzer] 正在提取设备变更记录...")
            from datetime import timedelta

            start_time_changes = analysis_datetime - timedelta(days=7)
            device_changes = self.data_extractor.extract_device_changes(
                room_id=room_id,
                start_time=start_time_changes,
                end_time=analysis_datetime,
            )

            # Limit device changes to prevent prompt overflow
            MAX_DEVICE_CHANGES = 30
            original_count = len(device_changes)
            if original_count > MAX_DEVICE_CHANGES:
                device_changes = device_changes.head(MAX_DEVICE_CHANGES)
                warning_msg = f"设备变更记录从 {original_count} 条截断至 {MAX_DEVICE_CHANGES} 条以防止提示词溢出"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            if device_changes.empty:
                warning_msg = f"库房 {room_id} 在过去7天内未找到设备变更记录"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["device_change_records"] = len(device_changes)
                logger.info(
                    f"[DecisionAnalyzer] 设备变更记录提取完成，共获取 {len(device_changes)} 条记录"
                )

            step1_time = time.time() - step1_start
            logger.info(f"[DecisionAnalyzer] 步骤 1 完成，耗时 {step1_time:.2f}秒")

        except Exception as e:
            error_msg = f"数据提取失败: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Continue with empty data - will use fallback strategy

        # ====================================================================
        # STEP 2: Find Similar Historical Cases (CLIP Matching)
        # ====================================================================
        logger.info("[DecisionAnalyzer] 步骤 2: 查找相似历史案例 (CLIP匹配)...")
        step2_start = time.time()

        try:
            if (
                current_data
                and "embedding" in current_data
                and current_data["embedding"] is not None
            ):
                import numpy as np

                query_embedding = current_data["embedding"]

                # Ensure embedding is numpy array
                if not isinstance(query_embedding, np.ndarray):
                    query_embedding = np.array(query_embedding)

                similar_cases = self.clip_matcher.find_similar_cases(
                    query_embedding=query_embedding,
                    room_id=room_id,
                    in_date=current_data.get("in_date"),
                    growth_day=current_data.get("growth_day", 0),
                    top_k=3,
                    date_window_days=7,
                    growth_day_window=3,
                )

                if similar_cases:
                    metadata["similar_cases_count"] = len(similar_cases)
                    metadata["avg_similarity_score"] = sum(
                        case.similarity_score for case in similar_cases
                    ) / len(similar_cases)

                    logger.info(
                        f"[DecisionAnalyzer] 找到 {len(similar_cases)} 个相似案例, "
                        f"平均相似度: {metadata['avg_similarity_score']:.2f}%"
                    )

                    # Check for low confidence cases
                    low_confidence_cases = [
                        case for case in similar_cases if case.confidence_level == "low"
                    ]
                    if low_confidence_cases:
                        warning_msg = (
                            f"发现 {len(low_confidence_cases)} 个低置信度匹配 "
                            f"(相似度 < 20%)"
                        )
                        logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                        metadata["warnings"].append(warning_msg)
                else:
                    warning_msg = "未找到相似案例，将使用基于规则的策略"
                    logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                    metadata["warnings"].append(warning_msg)
            else:
                warning_msg = "无Embedding数据可用于CLIP匹配"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            step2_time = time.time() - step2_start
            logger.info(f"[DecisionAnalyzer] 步骤 2 完成，耗时 {step2_time:.2f}秒")

        except Exception as e:
            error_msg = f"CLIP匹配失败: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            metadata["warnings"].append("继续执行，不使用相似案例")
            # Continue without similar cases

        # ====================================================================
        # STEP 3: Render Decision Prompt Template
        # ====================================================================
        logger.info("[DecisionAnalyzer] 步骤 3: 渲染决策提示词模板...")
        step3_start = time.time()

        try:
            import pandas as pd

            # Ensure we have DataFrames (even if empty)
            if env_stats is None:
                env_stats = pd.DataFrame()
            if device_changes is None:
                device_changes = pd.DataFrame()

            rendered_prompt = self.template_renderer.render(
                current_data=current_data,
                env_stats=env_stats,
                device_changes=device_changes,
                similar_cases=similar_cases,
            )

            logger.info(
                f"[DecisionAnalyzer] 提示词模板渲染成功 "
                f"(长度: {len(rendered_prompt)} 字符)"
            )

            step3_time = time.time() - step3_start
            logger.info(f"[DecisionAnalyzer] 步骤 3 完成，耗时 {step3_time:.2f}秒")

        except Exception as e:
            error_msg = f"模板渲染失败: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Use a simple fallback prompt
            rendered_prompt = f"生成蘑菇房{room_id}的环境调控建议"
            metadata["warnings"].append("由于渲染错误，使用简化提示词")

        # ====================================================================
        # STEP 4: Call LLM to Generate Decision
        # ====================================================================
        logger.info("[DecisionAnalyzer] 步骤 4: 调用LLM生成决策...")
        step4_start = time.time()

        try:
            # Estimate prompt length and add to metadata
            prompt_length = len(rendered_prompt)
            prompt_tokens_estimate = (
                prompt_length // 4
            )  # Rough estimate: 1 token ≈ 4 chars

            logger.info(
                f"[DecisionAnalyzer] 提示词长度: {prompt_length} 字符 "
                f"(~{prompt_tokens_estimate} tokens)"
            )

            # Warn if prompt is very long
            if prompt_tokens_estimate > 3000:
                warning_msg = (
                    f"提示词过长 (~{prompt_tokens_estimate} tokens), "
                    "可能超出模型上下文窗口"
                )
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            llm_decision = self.llm_client.generate_decision(
                prompt=rendered_prompt,
                temperature=0.5,  # Lower temperature for more stable JSON output
                max_tokens=2048,  # Limit output to ensure complete JSON
            )

            step4_time = time.time() - step4_start
            metadata["llm_response_time"] = step4_time

            logger.info(f"[DecisionAnalyzer] 步骤 4 完成，耗时 {step4_time:.2f}秒")

            # Check if LLM returned fallback decision
            if llm_decision.get("status") == "fallback":
                warning_msg = f"LLM降级: {llm_decision.get('error_reason', '未知')}"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

                # Extract warnings from fallback decision
                if (
                    "metadata" in llm_decision
                    and "warnings" in llm_decision["metadata"]
                ):
                    metadata["warnings"].extend(llm_decision["metadata"]["warnings"])

        except Exception as e:
            error_msg = f"LLM调用失败: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Use fallback decision
            llm_decision = self.llm_client._get_fallback_decision(str(e))
            metadata["warnings"].append("由于LLM错误，使用降级决策")

        # ====================================================================
        # STEP 5: Validate and Format Output
        # ====================================================================
        logger.info("[DecisionAnalyzer] 步骤 5: 验证并格式化输出...")
        step5_start = time.time()

        try:
            decision_output = self.output_handler.validate_and_format(
                raw_decision=llm_decision, room_id=room_id
            )

            # Merge metadata
            decision_output.metadata.data_sources = metadata["data_sources"]
            decision_output.metadata.similar_cases_count = metadata[
                "similar_cases_count"
            ]
            decision_output.metadata.avg_similarity_score = metadata[
                "avg_similarity_score"
            ]
            decision_output.metadata.llm_model = metadata["llm_model"]
            decision_output.metadata.llm_response_time = metadata["llm_response_time"]

            # Add warnings and errors from all steps
            decision_output.metadata.warnings.extend(metadata["warnings"])
            decision_output.metadata.errors.extend(metadata["errors"])

            step5_time = time.time() - step5_start
            logger.info(f"[DecisionAnalyzer] 步骤 5 完成，耗时 {step5_time:.2f}秒")

        except Exception as e:
            error_msg = f"输出验证失败: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)

            # Create minimal error output
            from decision_analysis.data_models import (
                AirCoolerRecommendation,
                ControlStrategy,
                DecisionMetadata,
                DecisionOutput,
                DeviceRecommendations,
                FreshAirFanRecommendation,
                GrowLightRecommendation,
                HumidifierRecommendation,
                MonitoringPoints,
            )

            decision_output = DecisionOutput(
                status="error",
                room_id=room_id,
                analysis_time=analysis_datetime,
                strategy=ControlStrategy(
                    core_objective="系统错误，无法生成决策",
                    key_risk_points=metadata["errors"],
                ),
                device_recommendations=DeviceRecommendations(
                    air_cooler=AirCoolerRecommendation(
                        0, 0, 0, 0, 0, 0, 0, rationale=["系统错误"]
                    ),
                    fresh_air_fan=FreshAirFanRecommendation(
                        0, 0, 0, 0, 0, 0, rationale=["系统错误"]
                    ),
                    humidifier=HumidifierRecommendation(
                        0, 0, 0, rationale=["系统错误"]
                    ),
                    grow_light=GrowLightRecommendation(
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, rationale=["系统错误"]
                    ),
                ),
                monitoring_points=MonitoringPoints(),
                metadata=DecisionMetadata(
                    data_sources=metadata["data_sources"],
                    warnings=metadata["warnings"],
                    errors=metadata["errors"],
                ),
            )

        # ====================================================================
        # Final Summary
        # ====================================================================
        total_time = time.time() - start_time
        decision_output.metadata.total_processing_time = total_time

        logger.info("[DecisionAnalyzer] ==========================================")
        logger.info("[DecisionAnalyzer] 分析已完成")
        logger.info(f"[DecisionAnalyzer] 状态: {decision_output.status}")
        logger.info(f"[DecisionAnalyzer] 总耗时: {total_time:.2f}秒")
        logger.info(
            f"[DecisionAnalyzer] 数据源数量: {len(decision_output.metadata.data_sources)}"
        )
        logger.info(
            f"[DecisionAnalyzer] 相似案例数: {decision_output.metadata.similar_cases_count}"
        )
        logger.info(
            f"[DecisionAnalyzer] 警告数: {len(decision_output.metadata.warnings)}"
        )
        logger.info(
            f"[DecisionAnalyzer] 错误数: {len(decision_output.metadata.errors)}"
        )
        logger.info("[DecisionAnalyzer] ==========================================")

        return decision_output

    def analyze_enhanced(
        self, room_id: str, analysis_datetime: datetime
    ) -> "EnhancedDecisionOutput":
        """
        Execute enhanced decision analysis workflow with multi-image support

        This method implements the enhanced workflow that:
        1. Extracts multi-image data from the same room
        2. Aggregates image embeddings for comprehensive analysis
        3. Generates structured parameter adjustments
        4. Provides detailed risk assessments and priority levels

        Args:
            room_id: Room number (607/608/611/612)
            analysis_datetime: Analysis timestamp

        Returns:
            EnhancedDecisionOutput with structured parameter adjustments

        Requirements: Enhanced decision analysis with multi-image support
        """
        start_time = time.time()

        logger.info("[DecisionAnalyzer] ==========================================")
        logger.info("[DecisionAnalyzer] Starting ENHANCED decision analysis")
        logger.info(f"[DecisionAnalyzer] Room ID: {room_id}")
        logger.info(f"[DecisionAnalyzer] Analysis Time: {analysis_datetime}")
        logger.info("[DecisionAnalyzer] Multi-image aggregation: ENABLED")
        logger.info("[DecisionAnalyzer] ==========================================")

        # Initialize metadata tracking
        metadata = {
            "data_sources": {},
            "similar_cases_count": 0,
            "avg_similarity_score": 0.0,
            "llm_model": self.settings.llama.model,
            "llm_response_time": 0.0,
            "total_processing_time": 0.0,
            "warnings": [],
            "errors": [],
            "multi_image_count": 0,
            "image_aggregation_method": "weighted_average",
        }

        # Variables to hold extracted data
        current_data = {}
        env_stats = None
        device_changes = None
        similar_cases = []
        rendered_prompt = ""
        llm_decision = {}
        # 初始化默认的多图像分析对象
        multi_image_analysis = MultiImageAnalysis(
            total_images_analyzed=0,
            confidence_score=0.0,
            view_consistency="low",
            key_observations=["初始化默认值"],
        )

        # ====================================================================
        # STEP 1: Extract Multi-Image Data from Database
        # ====================================================================
        logger.info(
            "[DecisionAnalyzer] STEP 1: Extracting multi-image data from database..."
        )
        step1_start = time.time()

        try:
            # Extract current embedding data with multi-image aggregation
            logger.info("[DecisionAnalyzer] Extracting multi-image embedding data...")
            embedding_df = self.data_extractor.extract_embedding_data(
                room_id=room_id,
                target_datetime=analysis_datetime,
                time_window_days=7,
                growth_day_window=3,
                image_aggregation_window_minutes=self.decision_config[
                    "image_aggregation_window"
                ],
            )

            if embedding_df.empty:
                error_msg = f"No embedding data found for room {room_id}"
                logger.error(f"[DecisionAnalyzer] {error_msg}")
                metadata["errors"].append(error_msg)
                metadata["warnings"].append(
                    "Using fallback strategy due to missing data"
                )
            else:
                # Count images in the aggregation window
                metadata["multi_image_count"] = len(embedding_df)

                # Use the most recent record (which may contain aggregated data)
                latest_record = embedding_df.iloc[0]

                # Extract environmental sensor status
                env_sensor_status = latest_record.get("env_sensor_status", {})

                # Build current_data dictionary
                current_data = {
                    "room_id": room_id,
                    "collection_datetime": latest_record.get("collection_datetime"),
                    "in_date": latest_record.get("in_date"),
                    "in_num": latest_record.get("in_num"),
                    "growth_day": latest_record.get("growth_day"),
                    "in_day_num": latest_record.get("growth_day"),  # Same as growth_day
                    "in_year": latest_record.get("in_date").year
                    if latest_record.get("in_date")
                    else None,
                    "in_month": latest_record.get("in_date").month
                    if latest_record.get("in_date")
                    else None,
                    "in_day": latest_record.get("in_date").day
                    if latest_record.get("in_date")
                    else None,
                    "temperature": env_sensor_status.get("temperature", 0.0),
                    "humidity": env_sensor_status.get("humidity", 0.0),
                    "co2": env_sensor_status.get("co2", 0.0),
                    "embedding": latest_record.get("embedding"),
                    "semantic_description": latest_record.get(
                        "semantic_description", ""
                    ),
                    "llama_description": latest_record.get("llama_description"),
                    "image_quality_score": latest_record.get("image_quality_score"),
                    "air_cooler_config": latest_record.get("air_cooler_config", {}),
                    "fresh_fan_config": latest_record.get("fresh_fan_config", {}),
                    "humidifier_config": latest_record.get("humidifier_config", {}),
                    "light_config": latest_record.get("light_config", {}),
                }

                # Create multi-image analysis summary

                # Calculate consistency score once to avoid multiple calls
                consistency_score = self._calculate_image_consistency(embedding_df)
                view_consistency = (
                    "high"
                    if consistency_score >= 0.8
                    else "medium"
                    if consistency_score >= 0.6
                    else "low"
                )

                multi_image_analysis = MultiImageAnalysis(
                    total_images_analyzed=metadata["multi_image_count"],
                    image_quality_scores=[
                        float(row.get("image_quality_score", 0.0))
                        for _, row in embedding_df.iterrows()
                    ],
                    aggregation_method=metadata["image_aggregation_method"],
                    confidence_score=consistency_score,
                    view_consistency=view_consistency,
                    key_observations=[
                        f"Camera {i + 1}: Quality {score:.2f}"
                        for i, score in enumerate(
                            [
                                float(row.get("image_quality_score", 0.0))
                                for _, row in embedding_df.iterrows()
                            ]
                        )
                    ],
                )

                metadata["data_sources"]["embedding_records"] = len(embedding_df)
                logger.info(
                    f"[DecisionAnalyzer] Extracted {len(embedding_df)} embedding records from {metadata['multi_image_count']} images, "
                    f"using aggregated data from {current_data['collection_datetime']}"
                )

                # Validate environmental parameters
                validation_warnings = self.data_extractor.validate_env_params(
                    embedding_df
                )
                if validation_warnings:
                    metadata["warnings"].extend(validation_warnings)

            # Extract environmental daily statistics (same as before)
            logger.info("[DecisionAnalyzer] 正在提取环境统计数据...")
            target_date = analysis_datetime.date()

            # 使用实时历史数据接口获取当天环境数据
            # 由于统计天级温湿度数据是批处理任务，只有昨天及以前的历史统计数据，
            # 无法获取当天的温湿度统计数据，因此当天的温湿度数据通过实时历史数据查询接口进行获取
            env_stats = self.data_extractor.extract_realtime_env_data(
                room_id=room_id, target_datetime=analysis_datetime
            )

            if env_stats.empty:
                warning_msg = f"库房 {room_id} 未找到环境统计数据"
                logger.error(f"[DecisionAnalyzer] 提取环境数据失败: {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["env_stats_records"] = len(env_stats)
                logger.info(
                    f"[DecisionAnalyzer] 环境数据提取完成，共获取 {len(env_stats)} 条统计记录"
                )

            # Extract device change records (same as before)
            logger.info("[DecisionAnalyzer] 正在提取设备变更记录...")
            from datetime import timedelta

            start_time_changes = analysis_datetime - timedelta(days=7)
            device_changes = self.data_extractor.extract_device_changes(
                room_id=room_id,
                start_time=start_time_changes,
                end_time=analysis_datetime,
            )

            # Limit device changes to prevent prompt overflow
            MAX_DEVICE_CHANGES = 30
            original_count = len(device_changes)
            if original_count > MAX_DEVICE_CHANGES:
                device_changes = device_changes.head(MAX_DEVICE_CHANGES)
                warning_msg = f"设备变更记录从 {original_count} 条截断至 {MAX_DEVICE_CHANGES} 条以防止提示词溢出"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            if device_changes.empty:
                warning_msg = f"库房 {room_id} 在过去7天内未找到设备变更记录"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["device_change_records"] = len(device_changes)
                logger.info(
                    f"[DecisionAnalyzer] 设备变更记录提取完成，共获取 {len(device_changes)} 条记录"
                )

            step1_time = time.time() - step1_start
            logger.info(f"[DecisionAnalyzer] STEP 1 completed in {step1_time:.2f}s")

        except Exception as e:
            error_msg = f"Multi-image data extraction failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Continue with empty data - will use fallback strategy

        # ====================================================================
        # STEP 2: Find Similar Historical Cases with Multi-Image Boost
        # ====================================================================
        logger.info(
            "[DecisionAnalyzer] STEP 2: Finding similar cases with multi-image boost..."
        )
        step2_start = time.time()

        try:
            if (
                current_data
                and "embedding" in current_data
                and current_data["embedding"] is not None
            ):
                import numpy as np

                query_embedding = current_data["embedding"]

                # Ensure embedding is numpy array
                if not isinstance(query_embedding, np.ndarray):
                    query_embedding = np.array(query_embedding)

                # Use enhanced CLIP matcher with multi-image support
                similar_cases = self.clip_matcher.find_similar_cases_multi_image(
                    query_embedding=query_embedding,
                    room_id=room_id,
                    in_date=current_data.get("in_date"),
                    growth_day=current_data.get("growth_day", 0),
                    top_k=3,
                    date_window_days=7,
                    growth_day_window=3,
                    multi_image_boost=True,
                    image_count=metadata["multi_image_count"],
                )

                if similar_cases:
                    metadata["similar_cases_count"] = len(similar_cases)
                    metadata["avg_similarity_score"] = sum(
                        case.similarity_score for case in similar_cases
                    ) / len(similar_cases)

                    logger.info(
                        f"[DecisionAnalyzer] Found {len(similar_cases)} similar cases with multi-image boost, "
                        f"avg similarity: {metadata['avg_similarity_score']:.2f}%"
                    )

                    # Check for low confidence cases
                    low_confidence_cases = [
                        case for case in similar_cases if case.confidence_level == "low"
                    ]
                    if low_confidence_cases:
                        warning_msg = (
                            f"Found {len(low_confidence_cases)} low confidence matches "
                            f"(similarity < 20%)"
                        )
                        logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                        metadata["warnings"].append(warning_msg)
                else:
                    warning_msg = "No similar cases found, will use rule-based strategy"
                    logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                    metadata["warnings"].append(warning_msg)
            else:
                warning_msg = "No embedding data available for CLIP matching"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            step2_time = time.time() - step2_start
            logger.info(f"[DecisionAnalyzer] STEP 2 completed in {step2_time:.2f}s")

        except Exception as e:
            error_msg = f"Enhanced CLIP matching failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            metadata["warnings"].append("Continuing without similar cases")
            # Continue without similar cases

        # ====================================================================
        # STEP 3: Render Enhanced Decision Prompt Template
        # ====================================================================
        logger.info("[DecisionAnalyzer] STEP 3: Rendering enhanced decision prompt...")
        step3_start = time.time()

        try:
            import pandas as pd

            # Ensure we have DataFrames (even if empty)
            if env_stats is None:
                env_stats = pd.DataFrame()
            if device_changes is None:
                device_changes = pd.DataFrame()

            # Add multi-image context to the prompt rendering
            rendered_prompt = self.template_renderer.render_enhanced(
                current_data=current_data,
                env_stats=env_stats,
                device_changes=device_changes,
                similar_cases=similar_cases,
                multi_image_analysis=multi_image_analysis,
            )

            logger.info(
                f"[DecisionAnalyzer] Rendered enhanced prompt successfully "
                f"(length: {len(rendered_prompt)} chars, multi-image context included)"
            )

            step3_time = time.time() - step3_start
            logger.info(f"[DecisionAnalyzer] STEP 3 completed in {step3_time:.2f}s")

        except Exception as e:
            error_msg = f"Enhanced template rendering failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Fallback to regular rendering
            try:
                rendered_prompt = self.template_renderer.render(
                    current_data=current_data,
                    env_stats=env_stats,
                    device_changes=device_changes,
                    similar_cases=similar_cases,
                )
                metadata["warnings"].append(
                    "Using regular prompt due to enhanced rendering error"
                )
            except Exception:
                rendered_prompt = f"生成蘑菇房{room_id}的环境调控建议（多图像综合分析）"
                metadata["warnings"].append(
                    "Using simplified prompt due to rendering errors"
                )

        # ====================================================================
        # STEP 4: Call LLM for Enhanced Decision Generation
        # ====================================================================
        logger.info(
            "[DecisionAnalyzer] STEP 4: Calling LLM for enhanced decision generation..."
        )
        step4_start = time.time()

        try:
            # Estimate prompt length and add to metadata
            prompt_length = len(rendered_prompt)
            prompt_tokens_estimate = (
                prompt_length // 4
            )  # Rough estimate: 1 token ≈ 4 chars

            logger.info(
                f"[DecisionAnalyzer] Enhanced prompt length: {prompt_length} chars "
                f"(~{prompt_tokens_estimate} tokens)"
            )

            # Warn if prompt is very long
            if prompt_tokens_estimate > 3000:
                warning_msg = (
                    f"Enhanced prompt is very long (~{prompt_tokens_estimate} tokens), "
                    "may exceed model context window"
                )
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

            llm_decision = self.llm_client.generate_enhanced_decision(
                prompt=rendered_prompt,
                temperature=0.3,  # Lower temperature for more structured output
                max_tokens=3072,  # Increased for enhanced output format
            )

            step4_time = time.time() - step4_start
            metadata["llm_response_time"] = step4_time

            logger.info(f"[DecisionAnalyzer] STEP 4 completed in {step4_time:.2f}s")

            # Check if LLM returned fallback decision
            if llm_decision.get("status") == "fallback":
                warning_msg = (
                    f"LLM fallback: {llm_decision.get('error_reason', 'Unknown')}"
                )
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)

                # Extract warnings from fallback decision
                if (
                    "metadata" in llm_decision
                    and "warnings" in llm_decision["metadata"]
                ):
                    metadata["warnings"].extend(llm_decision["metadata"]["warnings"])

        except Exception as e:
            error_msg = f"Enhanced LLM call failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Use fallback decision
            llm_decision = self.llm_client._get_enhanced_fallback_decision(str(e))
            metadata["warnings"].append(
                "Using enhanced fallback decision due to LLM error"
            )

        # ====================================================================
        # STEP 5: Validate and Format Enhanced Output
        # ====================================================================
        logger.info(
            "[DecisionAnalyzer] STEP 5: Validating and formatting enhanced output..."
        )
        step5_start = time.time()

        try:
            # First, validate and format the raw LLM output
            enhanced_decision_output = self.output_handler.validate_and_format_enhanced(
                raw_decision=llm_decision,
                room_id=room_id,
                multi_image_analysis=multi_image_analysis,
            )

            # ====================================================================
            # STEP 5.1: Device Configuration Adaptation
            # ====================================================================
            logger.info(
                "[DecisionAnalyzer] STEP 5.1: Adapting output to device configuration..."
            )

            try:
                # Convert enhanced decision output to dictionary for adaptation
                decision_dict = {"device_recommendations": {}}

                # Extract device recommendations from enhanced output
                if hasattr(enhanced_decision_output, "device_recommendations"):
                    device_recs = enhanced_decision_output.device_recommendations
                    logger.debug(
                        f"[DecisionAnalyzer] Found device_recommendations: {type(device_recs)}"
                    )

                    # Convert air cooler recommendations
                    if hasattr(device_recs, "air_cooler") and device_recs.air_cooler:
                        air_cooler = device_recs.air_cooler
                        decision_dict["device_recommendations"]["air_cooler"] = {}
                        logger.debug(
                            f"[DecisionAnalyzer] Processing air_cooler: {type(air_cooler)}"
                        )

                        # Map enhanced recommendations to configuration format
                        air_cooler_mappings = {
                            "tem_set": "temp_set",
                            "tem_diff_set": "temp_diffset",
                            "cyc_on_off": "cyc_on_off",
                            "cyc_on_time": "cyc_on_time",
                            "cyc_off_time": "cyc_off_time",
                            "ar_on_off": "air_on_off",
                            "hum_on_off": "hum_on_off",
                        }

                        for attr_name, point_alias in air_cooler_mappings.items():
                            if hasattr(air_cooler, attr_name):
                                param_adj = getattr(air_cooler, attr_name)
                                logger.debug(
                                    f"[DecisionAnalyzer] Processing {attr_name}: {type(param_adj)}"
                                )
                                if hasattr(param_adj, "recommended_value"):
                                    value = param_adj.recommended_value
                                    decision_dict["device_recommendations"][
                                        "air_cooler"
                                    ][point_alias] = value
                                    logger.debug(
                                        f"[DecisionAnalyzer] Mapped {attr_name} -> {point_alias}: {value}"
                                    )
                                else:
                                    logger.warning(
                                        f"[DecisionAnalyzer] ParameterAdjustment {attr_name} missing recommended_value"
                                    )
                            else:
                                logger.debug(
                                    f"[DecisionAnalyzer] air_cooler missing attribute: {attr_name}"
                                )

                    # Convert fresh air fan recommendations
                    if (
                        hasattr(device_recs, "fresh_air_fan")
                        and device_recs.fresh_air_fan
                    ):
                        fresh_air = device_recs.fresh_air_fan
                        decision_dict["device_recommendations"]["fresh_air_fan"] = {}
                        logger.debug(
                            f"[DecisionAnalyzer] Processing fresh_air_fan: {type(fresh_air)}"
                        )

                        fresh_air_mappings = {
                            "model": "mode",
                            "control": "control",
                            "co2_on": "co2_on",
                            "co2_off": "co2_off",
                            "on": "on",
                            "off": "off",
                        }

                        for attr_name, point_alias in fresh_air_mappings.items():
                            if hasattr(fresh_air, attr_name):
                                param_adj = getattr(fresh_air, attr_name)
                                if hasattr(param_adj, "recommended_value"):
                                    value = param_adj.recommended_value
                                    decision_dict["device_recommendations"][
                                        "fresh_air_fan"
                                    ][point_alias] = value
                                    logger.debug(
                                        f"[DecisionAnalyzer] Mapped {attr_name} -> {point_alias}: {value}"
                                    )

                    # Convert humidifier recommendations
                    if hasattr(device_recs, "humidifier") and device_recs.humidifier:
                        humidifier = device_recs.humidifier
                        decision_dict["device_recommendations"]["humidifier"] = {}
                        logger.debug(
                            f"[DecisionAnalyzer] Processing humidifier: {type(humidifier)}"
                        )

                        humidifier_mappings = {
                            "model": "mode",
                            "on": "on",
                            "off": "off",
                        }

                        for attr_name, point_alias in humidifier_mappings.items():
                            if hasattr(humidifier, attr_name):
                                param_adj = getattr(humidifier, attr_name)
                                if hasattr(param_adj, "recommended_value"):
                                    value = param_adj.recommended_value
                                    decision_dict["device_recommendations"][
                                        "humidifier"
                                    ][point_alias] = value
                                    logger.debug(
                                        f"[DecisionAnalyzer] Mapped {attr_name} -> {point_alias}: {value}"
                                    )

                    # Convert grow light recommendations
                    if hasattr(device_recs, "grow_light") and device_recs.grow_light:
                        grow_light = device_recs.grow_light
                        decision_dict["device_recommendations"]["grow_light"] = {}
                        logger.debug(
                            f"[DecisionAnalyzer] Processing grow_light: {type(grow_light)}"
                        )

                        grow_light_mappings = {
                            "model": "model",
                            "on_mset": "on_mset",
                            "off_mset": "off_mset",
                            "on_off_1": "on_off1",
                            "on_off_2": "on_off2",
                            "on_off_3": "on_off3",
                            "on_off_4": "on_off4",
                            "choose_1": "choose1",
                            "choose_2": "choose2",
                            "choose_3": "choose3",
                            "choose_4": "choose4",
                        }

                        for attr_name, point_alias in grow_light_mappings.items():
                            if hasattr(grow_light, attr_name):
                                param_adj = getattr(grow_light, attr_name)
                                if hasattr(param_adj, "recommended_value"):
                                    value = param_adj.recommended_value
                                    decision_dict["device_recommendations"][
                                        "grow_light"
                                    ][point_alias] = value
                                    logger.debug(
                                        f"[DecisionAnalyzer] Mapped {attr_name} -> {point_alias}: {value}"
                                    )

                logger.info(
                    f"[DecisionAnalyzer] Extracted device recommendations: {list(decision_dict['device_recommendations'].keys())}"
                )

                # Adapt decision output to match device configuration
                adapted_output, adaptation_warnings = (
                    self.device_config_adapter.adapt_decision_output(
                        decision_dict, room_id
                    )
                )

                # Add adaptation warnings to metadata
                metadata["warnings"].extend(adaptation_warnings)

                # Log adaptation results
                adapted_device_count = len(
                    adapted_output.get("device_recommendations", {})
                )
                logger.info(
                    f"[DecisionAnalyzer] Device configuration adaptation completed: "
                    f"{adapted_device_count} device types adapted, {len(adaptation_warnings)} warnings"
                )

                # Add device configuration metadata to enhanced output
                if hasattr(enhanced_decision_output, "metadata"):
                    enhanced_decision_output.metadata.device_config_metadata = (
                        adapted_output.get("device_config_metadata", {})
                    )

            except Exception as e:
                error_msg = f"Device configuration adaptation failed: {str(e)}"
                logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
                metadata["warnings"].append(error_msg)
                # Continue with original output if adaptation fails

            # Merge metadata
            enhanced_decision_output.metadata.data_sources = metadata["data_sources"]
            enhanced_decision_output.metadata.similar_cases_count = metadata[
                "similar_cases_count"
            ]
            enhanced_decision_output.metadata.avg_similarity_score = metadata[
                "avg_similarity_score"
            ]
            enhanced_decision_output.metadata.llm_model = metadata["llm_model"]
            enhanced_decision_output.metadata.llm_response_time = metadata[
                "llm_response_time"
            ]

            # Add enhanced metadata
            enhanced_decision_output.metadata.multi_image_count = metadata[
                "multi_image_count"
            ]
            enhanced_decision_output.metadata.image_aggregation_method = metadata[
                "image_aggregation_method"
            ]

            # Add warnings and errors from all steps
            enhanced_decision_output.metadata.warnings.extend(metadata["warnings"])
            enhanced_decision_output.metadata.errors.extend(metadata["errors"])

            step5_time = time.time() - step5_start
            logger.info(f"[DecisionAnalyzer] STEP 5 completed in {step5_time:.2f}s")

        except Exception as e:
            error_msg = f"Enhanced output validation failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)

            # Create minimal error output
            enhanced_decision_output = (
                self.output_handler._create_error_enhanced_output(
                    room_id=room_id, errors=metadata["errors"]
                )
            )
            enhanced_decision_output.metadata.warnings = metadata["warnings"]

        # ====================================================================
        # Final Summary
        # ====================================================================
        total_time = time.time() - start_time
        enhanced_decision_output.metadata.total_processing_time = total_time

        logger.info("[DecisionAnalyzer] ==========================================")
        logger.info("[DecisionAnalyzer] Enhanced analysis completed")
        logger.info(f"[DecisionAnalyzer] Status: {enhanced_decision_output.status}")
        logger.info(f"[DecisionAnalyzer] Total time: {total_time:.2f}s")
        logger.info(
            f"[DecisionAnalyzer] Multi-image count: {metadata['multi_image_count']}"
        )
        logger.info(
            f"[DecisionAnalyzer] Data sources: {len(enhanced_decision_output.metadata.data_sources)}"
        )
        logger.info(
            f"[DecisionAnalyzer] Similar cases: {enhanced_decision_output.metadata.similar_cases_count}"
        )
        logger.info(
            f"[DecisionAnalyzer] Warnings: {len(enhanced_decision_output.metadata.warnings)}"
        )
        logger.info(
            f"[DecisionAnalyzer] Errors: {len(enhanced_decision_output.metadata.errors)}"
        )
        logger.info("[DecisionAnalyzer] ==========================================")

        return enhanced_decision_output

    def _map_parameter_to_point_alias(
        self, device_type: str, parameter_name: str
    ) -> str:
        """
        Map enhanced decision parameter names to device configuration point aliases

        Args:
            device_type: Type of device (air_cooler, fresh_air_fan, etc.)
            parameter_name: Parameter name from enhanced decision output

        Returns:
            Point alias from device configuration or None if not found
        """
        # Mapping from enhanced decision parameter names to configuration point aliases
        parameter_mappings = {
            "air_cooler": {
                "tem_set": "temp_set",
                "tem_diff_set": "temp_diffset",
                "cyc_on_off": "cyc_on_off",
                "cyc_on_time": "cyc_on_time",
                "cyc_off_time": "cyc_off_time",
                "ar_on_off": "air_on_off",
                "hum_on_off": "hum_on_off",
                "on_off": "on_off",
            },
            "fresh_air_fan": {
                "model": "mode",
                "control": "control",
                "co2_on": "co2_on",
                "co2_off": "co2_off",
                "on": "on",
                "off": "off",
            },
            "humidifier": {"model": "mode", "on": "on", "off": "off"},
            "grow_light": {
                "model": "model",
                "on_mset": "on_mset",
                "off_mset": "off_mset",
                "on_off_1": "on_off1",
                "on_off_2": "on_off2",
                "on_off_3": "on_off3",
                "on_off_4": "on_off4",
                "choose_1": "choose1",
                "choose_2": "choose2",
                "choose_3": "choose3",
                "choose_4": "choose4",
            },
        }

        device_mapping = parameter_mappings.get(device_type, {})
        point_alias = device_mapping.get(parameter_name)

        if point_alias:
            # Verify that this point alias is supported in the configuration
            supported_points = self.device_config_adapter.get_supported_points(
                device_type
            )
            if point_alias in supported_points:
                return point_alias
            else:
                logger.debug(
                    f"[DecisionAnalyzer] Point alias '{point_alias}' not supported for device type '{device_type}'"
                )
                return None
        else:
            logger.debug(
                f"[DecisionAnalyzer] No mapping found for parameter '{parameter_name}' in device type '{device_type}'"
            )
            return None

    def _calculate_image_consistency(self, embedding_df) -> float:
        """
        Calculate camera IP-based image consistency score using pgvector database optimization

        This enhanced function implements camera IP-based image consistency calculation:
        1. Groups images by camera IP (collection_ip field) to get latest image from each camera
        2. Excludes current batch images (same in_date) to ensure historical comparison
        3. Performs cross-camera similarity matching to avoid intra-camera temporal comparison
        4. Selects top-5 similar images from different cameras for each query vector
        5. Uses pgvector optimization with IVFFlat indices and cosine distance operators

        Args:
            embedding_df: DataFrame with image embeddings containing 'embedding', 'room_id',
                         'in_date', 'collection_ip', 'collection_datetime' columns

        Returns:
            Consistency score between 0.0 and 1.0 (higher means more consistent across cameras)

        Performance Benefits:
            - Utilizes database-level vector operations (pgvector)
            - Leverages optimized vector indices (IVFFlat) for faster similarity search
            - Uses PostgreSQL's native cosine distance operator (<=>)
            - Reduces CPU computation overhead compared to manual calculations
        """
        consistency_start_time = time.time()

        try:
            if len(embedding_df) < 2:
                logger.info(
                    "[DecisionAnalyzer] Single image found, returning perfect consistency"
                )
                return 1.0

            import numpy as np
            from sqlalchemy import text
            from sqlalchemy.orm import sessionmaker

            # Create database session
            Session = sessionmaker(bind=self.db_engine)
            session = Session()

            try:
                # Extract room_id and current batch date from embedding_df
                room_id = (
                    embedding_df.iloc[0].get("room_id")
                    if not embedding_df.empty
                    else None
                )
                current_in_date = (
                    embedding_df.iloc[0].get("in_date")
                    if not embedding_df.empty
                    else None
                )

                if not room_id:
                    logger.warning(
                        "[DecisionAnalyzer] No room_id found in embedding data, using fallback"
                    )
                    return self._calculate_image_consistency_fallback(embedding_df)

                logger.info(
                    f"[DecisionAnalyzer] Starting camera IP-based consistency calculation for room {room_id}"
                )

                # ====================================================================
                # STEP 1: Get latest image from each camera IP in current room
                # ====================================================================
                logger.info(
                    "[DecisionAnalyzer] Step 1: Getting latest images from each camera IP..."
                )

                # Query to get the latest image from each camera IP in the current room
                # Exclude current batch (same in_date) to ensure historical comparison
                camera_query = text("""
                    WITH latest_by_camera AS (
                        SELECT 
                            collection_ip,
                            embedding,
                            collection_datetime,
                            in_date,
                            ROW_NUMBER() OVER (
                                PARTITION BY collection_ip 
                                ORDER BY collection_datetime DESC
                            ) as rn
                        FROM mushroom_embedding 
                        WHERE room_id = :room_id 
                        AND embedding IS NOT NULL 
                        AND collection_ip IS NOT NULL
                        AND (:current_in_date IS NULL OR in_date != :current_in_date)
                    )
                    SELECT 
                        collection_ip,
                        embedding,
                        collection_datetime,
                        in_date
                    FROM latest_by_camera 
                    WHERE rn = 1
                    ORDER BY collection_ip
                """)

                camera_results = session.execute(
                    camera_query,
                    {"room_id": room_id, "current_in_date": current_in_date},
                ).fetchall()

                if not camera_results:
                    logger.warning(
                        f"[DecisionAnalyzer] No historical camera data found for room {room_id}, using fallback"
                    )
                    return self._calculate_image_consistency_fallback(embedding_df)

                camera_ips = [row[0] for row in camera_results]
                camera_embeddings = []

                logger.info(
                    f"[DecisionAnalyzer] Found {len(camera_results)} cameras: {camera_ips}"
                )

                # Process camera embeddings
                for row in camera_results:
                    collection_ip, embedding, collection_datetime, in_date = row

                    if embedding is not None:
                        if isinstance(embedding, str):
                            # Handle string representation of array
                            embedding = (
                                eval(embedding)
                                if embedding.startswith("[")
                                else embedding
                            )
                        if not isinstance(embedding, (list, np.ndarray)):
                            continue
                        if isinstance(embedding, np.ndarray):
                            embedding = embedding.tolist()

                        camera_embeddings.append(
                            {
                                "collection_ip": collection_ip,
                                "embedding": embedding,
                                "collection_datetime": collection_datetime,
                                "in_date": in_date,
                                "embedding_dim": len(embedding),
                            }
                        )

                if len(camera_embeddings) < 2:
                    logger.warning(
                        f"[DecisionAnalyzer] Insufficient camera embeddings ({len(camera_embeddings)}), using fallback"
                    )
                    return self._calculate_image_consistency_fallback(embedding_df)

                logger.info(
                    f"[DecisionAnalyzer] Processing {len(camera_embeddings)} camera query vectors"
                )
                for cam_data in camera_embeddings:
                    logger.debug(
                        f"  - Camera {cam_data['collection_ip']}: {cam_data['embedding_dim']}D vector from {cam_data['collection_datetime']}"
                    )

                # ====================================================================
                # STEP 2: Exclude current batch images from comparison database
                # ====================================================================
                logger.info(
                    "[DecisionAnalyzer] Step 2: Preparing historical comparison database..."
                )

                # Count total and filtered images for logging
                total_count_query = text("""
                    SELECT COUNT(*) FROM mushroom_embedding 
                    WHERE room_id = :room_id AND embedding IS NOT NULL
                """)
                total_count = session.execute(
                    total_count_query, {"room_id": room_id}
                ).scalar()

                filtered_count_query = text("""
                    SELECT COUNT(*) FROM mushroom_embedding 
                    WHERE room_id = :room_id 
                    AND embedding IS NOT NULL 
                    AND (:current_in_date IS NULL OR in_date != :current_in_date)
                """)
                filtered_count = session.execute(
                    filtered_count_query,
                    {"room_id": room_id, "current_in_date": current_in_date},
                ).scalar()

                excluded_count = total_count - filtered_count
                logger.info(
                    f"[DecisionAnalyzer] Historical database: {filtered_count} images (excluded {excluded_count} current batch images)"
                )

                # ====================================================================
                # STEP 3: Cross-camera similarity matching
                # ====================================================================
                logger.info(
                    "[DecisionAnalyzer] Step 3: Performing cross-camera similarity matching..."
                )

                all_similarity_scores = []
                camera_match_stats = {}

                for i, cam_data in enumerate(camera_embeddings):
                    camera_ip = cam_data["collection_ip"]
                    embedding = cam_data["embedding"]

                    logger.debug(
                        f"[DecisionAnalyzer] Processing camera {camera_ip} ({i + 1}/{len(camera_embeddings)})"
                    )

                    # Convert embedding to string format for SQL
                    embedding_str = "[" + ",".join(map(str, embedding)) + "]"

                    # Query for top-5 most similar images from OTHER cameras
                    # Exclude same camera to avoid intra-camera temporal comparison
                    similarity_query = text(f"""
                        SELECT 
                            collection_ip,
                            collection_datetime,
                            in_date,
                            GREATEST(0, 1 - (embedding <=> '{embedding_str}'::vector)) as similarity_score
                        FROM mushroom_embedding 
                        WHERE room_id = :room_id
                        AND embedding IS NOT NULL
                        AND collection_ip IS NOT NULL
                        AND collection_ip != :camera_ip
                        AND (:current_in_date IS NULL OR in_date != :current_in_date)
                        ORDER BY embedding <=> '{embedding_str}'::vector
                        LIMIT 5
                    """)

                    similarity_results = session.execute(
                        similarity_query,
                        {
                            "room_id": room_id,
                            "camera_ip": camera_ip,
                            "current_in_date": current_in_date,
                        },
                    ).fetchall()

                    if similarity_results:
                        camera_similarities = [
                            float(row[3]) for row in similarity_results
                        ]
                        camera_avg_similarity = np.mean(camera_similarities)
                        all_similarity_scores.extend(camera_similarities)

                        # Track statistics for this camera
                        matched_cameras = list(
                            set([row[0] for row in similarity_results])
                        )
                        camera_match_stats[camera_ip] = {
                            "matches_found": len(similarity_results),
                            "avg_similarity": camera_avg_similarity,
                            "matched_cameras": matched_cameras,
                            "similarity_range": [
                                min(camera_similarities),
                                max(camera_similarities),
                            ],
                        }

                        logger.debug(
                            f"  - Found {len(similarity_results)} matches, avg similarity: {camera_avg_similarity:.3f}"
                        )
                    else:
                        logger.warning(f"  - No matches found for camera {camera_ip}")
                        camera_match_stats[camera_ip] = {
                            "matches_found": 0,
                            "avg_similarity": 0.0,
                            "matched_cameras": [],
                            "similarity_range": [0.0, 0.0],
                        }

                # ====================================================================
                # STEP 4: Calculate overall consistency score
                # ====================================================================
                logger.info(
                    "[DecisionAnalyzer] Step 4: Calculating overall consistency score..."
                )

                if all_similarity_scores:
                    overall_consistency = float(np.mean(all_similarity_scores))

                    # Ensure score is in valid range
                    overall_consistency = max(0.0, min(1.0, overall_consistency))

                    # Calculate statistics
                    similarity_std = float(np.std(all_similarity_scores))
                    similarity_min = float(np.min(all_similarity_scores))
                    similarity_max = float(np.max(all_similarity_scores))

                    # Log detailed statistics
                    total_matches = sum(
                        stats["matches_found"] for stats in camera_match_stats.values()
                    )
                    successful_cameras = sum(
                        1
                        for stats in camera_match_stats.values()
                        if stats["matches_found"] > 0
                    )

                    logger.info(
                        "[DecisionAnalyzer] Cross-camera consistency analysis completed:"
                    )
                    logger.info(f"  - Cameras processed: {len(camera_embeddings)}")
                    logger.info(
                        f"  - Successful camera matches: {successful_cameras}/{len(camera_embeddings)}"
                    )
                    logger.info(f"  - Total similarity pairs: {total_matches}")
                    logger.info(f"  - Overall consistency: {overall_consistency:.3f}")
                    logger.info(
                        f"  - Similarity distribution: min={similarity_min:.3f}, max={similarity_max:.3f}, std={similarity_std:.3f}"
                    )

                    # Log per-camera statistics
                    for camera_ip, stats in camera_match_stats.items():
                        if stats["matches_found"] > 0:
                            logger.debug(
                                f"  - Camera {camera_ip}: {stats['matches_found']} matches, "
                                f"avg={stats['avg_similarity']:.3f}, "
                                f"range=[{stats['similarity_range'][0]:.3f}, {stats['similarity_range'][1]:.3f}], "
                                f"matched_cameras={stats['matched_cameras']}"
                            )

                    return overall_consistency
                else:
                    logger.warning(
                        "[DecisionAnalyzer] No similarity scores calculated, using default consistency"
                    )
                    return 0.5  # Default moderate consistency

            finally:
                session.close()

        except Exception as e:
            consistency_time = time.time() - consistency_start_time
            logger.warning(
                f"[DecisionAnalyzer] Failed to calculate camera IP-based consistency (time: {consistency_time:.2f}s): {e}"
            )
            logger.debug(f"[DecisionAnalyzer] Error details: {str(e)}", exc_info=True)
            # Fallback to manual calculation if database query fails
            return self._calculate_image_consistency_fallback(embedding_df)

        finally:
            consistency_time = time.time() - consistency_start_time
            logger.info(
                f"[DecisionAnalyzer] Camera IP-based consistency calculation completed in {consistency_time:.2f}s"
            )

    def _calculate_image_consistency_fallback(self, embedding_df) -> float:
        """
        Fallback method for image consistency calculation using manual cosine similarity

        This method is used when the pgvector-optimized calculation fails, providing
        a reliable backup using traditional in-memory similarity computation.

        Args:
            embedding_df: DataFrame with image embeddings

        Returns:
            Consistency score between 0.0 and 1.0
        """
        try:
            if len(embedding_df) < 2:
                return 1.0  # Single image is perfectly consistent

            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity

            embeddings = []
            for _, row in embedding_df.iterrows():
                embedding = row.get("embedding")
                if embedding is not None:
                    if not isinstance(embedding, np.ndarray):
                        embedding = np.array(embedding)
                    embeddings.append(embedding)

            if len(embeddings) < 2:
                return 1.0

            # Calculate pairwise cosine similarities
            similarities = []
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    sim = cosine_similarity([embeddings[i]], [embeddings[j]])[0][0]
                    similarities.append(sim)

            # Return average similarity as consistency score
            # Ensure the result is between 0.0 and 1.0
            avg_similarity = float(np.mean(similarities))
            return max(0.0, min(1.0, avg_similarity))

        except Exception as e:
            logger.warning(
                f"[DecisionAnalyzer] Failed to calculate image consistency (fallback): {e}"
            )
            return 0.5  # Default moderate consistency
