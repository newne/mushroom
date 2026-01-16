"""
Decision Analyzer Module

This is the main controller that orchestrates the entire decision analysis workflow.
It coordinates data extraction, CLIP matching, template rendering, LLM calling,
and output validation.
"""

import time
from datetime import datetime
from typing import Dict

from dynaconf import Dynaconf
from loguru import logger
from sqlalchemy import Engine

from decision_analysis.clip_matcher import CLIPMatcher
from decision_analysis.data_extractor import DataExtractor
from decision_analysis.data_models import DecisionOutput
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
        template_path: str
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
        logger.info("[DecisionAnalyzer] Initializing decision analyzer...")
        
        self.db_engine = db_engine
        self.settings = settings
        self.static_config = static_config
        self.template_path = template_path
        
        # Initialize all components with error handling
        try:
            logger.debug("[DecisionAnalyzer] Initializing DataExtractor...")
            self.data_extractor = DataExtractor(db_engine)
            
            logger.debug("[DecisionAnalyzer] Initializing CLIPMatcher...")
            self.clip_matcher = CLIPMatcher(db_engine)
            
            logger.debug("[DecisionAnalyzer] Initializing TemplateRenderer...")
            self.template_renderer = TemplateRenderer(template_path, static_config)
            
            logger.debug("[DecisionAnalyzer] Initializing LLMClient...")
            self.llm_client = LLMClient(settings)
            
            logger.debug("[DecisionAnalyzer] Initializing OutputHandler...")
            self.output_handler = OutputHandler(static_config)
            
            logger.info("[DecisionAnalyzer] Successfully initialized all components")
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalyzer] Failed to initialize components: {e}",
                exc_info=True
            )
            raise
    
    def analyze(
        self,
        room_id: str,
        analysis_datetime: datetime
    ) -> DecisionOutput:
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
        
        logger.info(
            f"[DecisionAnalyzer] =========================================="
        )
        logger.info(
            f"[DecisionAnalyzer] Starting decision analysis"
        )
        logger.info(
            f"[DecisionAnalyzer] Room ID: {room_id}"
        )
        logger.info(
            f"[DecisionAnalyzer] Analysis Time: {analysis_datetime}"
        )
        logger.info(
            f"[DecisionAnalyzer] =========================================="
        )
        
        # Initialize metadata tracking
        metadata = {
            "data_sources": {},
            "similar_cases_count": 0,
            "avg_similarity_score": 0.0,
            "llm_model": self.settings.llama.model,
            "llm_response_time": 0.0,
            "total_processing_time": 0.0,
            "warnings": [],
            "errors": []
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
        logger.info("[DecisionAnalyzer] STEP 1: Extracting data from database...")
        step1_start = time.time()
        
        try:
            # Extract current embedding data
            logger.info("[DecisionAnalyzer] Extracting current embedding data...")
            embedding_df = self.data_extractor.extract_current_embedding_data(
                room_id=room_id,
                target_datetime=analysis_datetime,
                time_window_days=7,
                growth_day_window=3
            )
            
            if embedding_df.empty:
                error_msg = f"No embedding data found for room {room_id}"
                logger.error(f"[DecisionAnalyzer] {error_msg}")
                metadata["errors"].append(error_msg)
                metadata["warnings"].append("Using fallback strategy due to missing data")
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
                    "in_year": latest_record.get("in_date").year if latest_record.get("in_date") else None,
                    "in_month": latest_record.get("in_date").month if latest_record.get("in_date") else None,
                    "in_day": latest_record.get("in_date").day if latest_record.get("in_date") else None,
                    "temperature": env_sensor_status.get("temperature", 0.0),
                    "humidity": env_sensor_status.get("humidity", 0.0),
                    "co2": env_sensor_status.get("co2", 0.0),
                    "embedding": latest_record.get("embedding"),
                    "semantic_description": latest_record.get("semantic_description", ""),
                    "llama_description": latest_record.get("llama_description"),
                    "image_quality_score": latest_record.get("image_quality_score"),
                    "air_cooler_config": latest_record.get("air_cooler_config", {}),
                    "fresh_fan_config": latest_record.get("fresh_fan_config", {}),
                    "humidifier_config": latest_record.get("humidifier_config", {}),
                    "light_config": latest_record.get("light_config", {})
                }
                
                metadata["data_sources"]["embedding_records"] = len(embedding_df)
                logger.info(
                    f"[DecisionAnalyzer] Extracted {len(embedding_df)} embedding records, "
                    f"using latest from {current_data['collection_datetime']}"
                )
                
                # Validate environmental parameters
                validation_warnings = self.data_extractor.validate_env_params(embedding_df)
                if validation_warnings:
                    metadata["warnings"].extend(validation_warnings)
            
            # Extract environmental daily statistics
            logger.info("[DecisionAnalyzer] Extracting environmental statistics...")
            target_date = analysis_datetime.date()
            env_stats = self.data_extractor.extract_env_daily_stats(
                room_id=room_id,
                target_date=target_date,
                days_range=1
            )
            
            if env_stats.empty:
                warning_msg = f"No environmental statistics found for room {room_id}"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["env_stats_records"] = len(env_stats)
                logger.info(
                    f"[DecisionAnalyzer] Extracted {len(env_stats)} environmental stat records"
                )
            
            # Extract device change records
            logger.info("[DecisionAnalyzer] Extracting device change records...")
            from datetime import timedelta
            start_time_changes = analysis_datetime - timedelta(days=7)
            device_changes = self.data_extractor.extract_device_changes(
                room_id=room_id,
                start_time=start_time_changes,
                end_time=analysis_datetime
            )
            
            # Limit device changes to prevent prompt overflow
            MAX_DEVICE_CHANGES = 30
            original_count = len(device_changes)
            if original_count > MAX_DEVICE_CHANGES:
                device_changes = device_changes.head(MAX_DEVICE_CHANGES)
                warning_msg = (
                    f"Device changes truncated from {original_count} to {MAX_DEVICE_CHANGES} "
                    f"records to prevent prompt overflow"
                )
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            
            if device_changes.empty:
                warning_msg = f"No device changes found for room {room_id} in the past 7 days"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            else:
                metadata["data_sources"]["device_change_records"] = len(device_changes)
                logger.info(
                    f"[DecisionAnalyzer] Extracted {len(device_changes)} device change records"
                )
            
            step1_time = time.time() - step1_start
            logger.info(f"[DecisionAnalyzer] STEP 1 completed in {step1_time:.2f}s")
            
        except Exception as e:
            error_msg = f"Data extraction failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Continue with empty data - will use fallback strategy
        
        # ====================================================================
        # STEP 2: Find Similar Historical Cases (CLIP Matching)
        # ====================================================================
        logger.info("[DecisionAnalyzer] STEP 2: Finding similar historical cases...")
        step2_start = time.time()
        
        try:
            if current_data and "embedding" in current_data and current_data["embedding"] is not None:
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
                    growth_day_window=3
                )
                
                if similar_cases:
                    metadata["similar_cases_count"] = len(similar_cases)
                    metadata["avg_similarity_score"] = sum(
                        case.similarity_score for case in similar_cases
                    ) / len(similar_cases)
                    
                    logger.info(
                        f"[DecisionAnalyzer] Found {len(similar_cases)} similar cases, "
                        f"avg similarity: {metadata['avg_similarity_score']:.2f}%"
                    )
                    
                    # Check for low confidence cases
                    low_confidence_cases = [
                        case for case in similar_cases 
                        if case.confidence_level == "low"
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
            error_msg = f"CLIP matching failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            metadata["warnings"].append("Continuing without similar cases")
            # Continue without similar cases
        
        # ====================================================================
        # STEP 3: Render Decision Prompt Template
        # ====================================================================
        logger.info("[DecisionAnalyzer] STEP 3: Rendering decision prompt...")
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
                similar_cases=similar_cases
            )
            
            logger.info(
                f"[DecisionAnalyzer] Rendered prompt successfully "
                f"(length: {len(rendered_prompt)} chars)"
            )
            
            step3_time = time.time() - step3_start
            logger.info(f"[DecisionAnalyzer] STEP 3 completed in {step3_time:.2f}s")
            
        except Exception as e:
            error_msg = f"Template rendering failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Use a simple fallback prompt
            rendered_prompt = f"生成蘑菇房{room_id}的环境调控建议"
            metadata["warnings"].append("Using simplified prompt due to rendering error")
        
        # ====================================================================
        # STEP 4: Call LLM to Generate Decision
        # ====================================================================
        logger.info("[DecisionAnalyzer] STEP 4: Calling LLM for decision generation...")
        step4_start = time.time()
        
        try:
            # Estimate prompt length and add to metadata
            prompt_length = len(rendered_prompt)
            prompt_tokens_estimate = prompt_length // 4  # Rough estimate: 1 token ≈ 4 chars
            
            logger.info(
                f"[DecisionAnalyzer] Prompt length: {prompt_length} chars "
                f"(~{prompt_tokens_estimate} tokens)"
            )
            
            # Warn if prompt is very long
            if prompt_tokens_estimate > 3000:
                warning_msg = (
                    f"Prompt is very long (~{prompt_tokens_estimate} tokens), "
                    "may exceed model context window"
                )
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
            
            llm_decision = self.llm_client.generate_decision(
                prompt=rendered_prompt,
                temperature=0.5,  # Lower temperature for more stable JSON output
                max_tokens=2048   # Limit output to ensure complete JSON
            )
            
            step4_time = time.time() - step4_start
            metadata["llm_response_time"] = step4_time
            
            logger.info(f"[DecisionAnalyzer] STEP 4 completed in {step4_time:.2f}s")
            
            # Check if LLM returned fallback decision
            if llm_decision.get("status") == "fallback":
                warning_msg = f"LLM fallback: {llm_decision.get('error_reason', 'Unknown')}"
                logger.warning(f"[DecisionAnalyzer] {warning_msg}")
                metadata["warnings"].append(warning_msg)
                
                # Extract warnings from fallback decision
                if "metadata" in llm_decision and "warnings" in llm_decision["metadata"]:
                    metadata["warnings"].extend(llm_decision["metadata"]["warnings"])
            
        except Exception as e:
            error_msg = f"LLM call failed: {str(e)}"
            logger.error(f"[DecisionAnalyzer] {error_msg}", exc_info=True)
            metadata["errors"].append(error_msg)
            # Use fallback decision
            llm_decision = self.llm_client._get_fallback_decision(str(e))
            metadata["warnings"].append("Using fallback decision due to LLM error")
        
        # ====================================================================
        # STEP 5: Validate and Format Output
        # ====================================================================
        logger.info("[DecisionAnalyzer] STEP 5: Validating and formatting output...")
        step5_start = time.time()
        
        try:
            decision_output = self.output_handler.validate_and_format(
                raw_decision=llm_decision,
                room_id=room_id
            )
            
            # Merge metadata
            decision_output.metadata.data_sources = metadata["data_sources"]
            decision_output.metadata.similar_cases_count = metadata["similar_cases_count"]
            decision_output.metadata.avg_similarity_score = metadata["avg_similarity_score"]
            decision_output.metadata.llm_model = metadata["llm_model"]
            decision_output.metadata.llm_response_time = metadata["llm_response_time"]
            
            # Add warnings and errors from all steps
            decision_output.metadata.warnings.extend(metadata["warnings"])
            decision_output.metadata.errors.extend(metadata["errors"])
            
            step5_time = time.time() - step5_start
            logger.info(f"[DecisionAnalyzer] STEP 5 completed in {step5_time:.2f}s")
            
        except Exception as e:
            error_msg = f"Output validation failed: {str(e)}"
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
                    key_risk_points=metadata["errors"]
                ),
                device_recommendations=DeviceRecommendations(
                    air_cooler=AirCoolerRecommendation(0, 0, 0, 0, 0, 0, 0, rationale=["系统错误"]),
                    fresh_air_fan=FreshAirFanRecommendation(0, 0, 0, 0, 0, 0, rationale=["系统错误"]),
                    humidifier=HumidifierRecommendation(0, 0, 0, rationale=["系统错误"]),
                    grow_light=GrowLightRecommendation(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, rationale=["系统错误"])
                ),
                monitoring_points=MonitoringPoints(),
                metadata=DecisionMetadata(
                    data_sources=metadata["data_sources"],
                    warnings=metadata["warnings"],
                    errors=metadata["errors"]
                )
            )
        
        # ====================================================================
        # Final Summary
        # ====================================================================
        total_time = time.time() - start_time
        decision_output.metadata.total_processing_time = total_time
        
        logger.info(
            f"[DecisionAnalyzer] =========================================="
        )
        logger.info(
            f"[DecisionAnalyzer] Analysis completed"
        )
        logger.info(
            f"[DecisionAnalyzer] Status: {decision_output.status}"
        )
        logger.info(
            f"[DecisionAnalyzer] Total time: {total_time:.2f}s"
        )
        logger.info(
            f"[DecisionAnalyzer] Data sources: {len(decision_output.metadata.data_sources)}"
        )
        logger.info(
            f"[DecisionAnalyzer] Similar cases: {decision_output.metadata.similar_cases_count}"
        )
        logger.info(
            f"[DecisionAnalyzer] Warnings: {len(decision_output.metadata.warnings)}"
        )
        logger.info(
            f"[DecisionAnalyzer] Errors: {len(decision_output.metadata.errors)}"
        )
        logger.info(
            f"[DecisionAnalyzer] =========================================="
        )
        
        return decision_output
