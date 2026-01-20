"""
Data Extractor Module

This module is responsible for extracting and preprocessing data from PostgreSQL database.
It queries three main tables:
- MushroomImageEmbedding: Image embeddings and device configurations
- MushroomEnvDailyStats: Environmental statistics
- DeviceSetpointChange: Device setpoint change records

The extractor implements intelligent filtering based on room, time windows, and growth stages.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd
from loguru import logger
from sqlalchemy import Engine

from decision_analysis.data_models import (
    CurrentStateData,
    DeviceChangeRecord,
    EnvStatsData,
)


class DataExtractor:
    """
    Data extractor for decision analysis
    
    Extracts data from PostgreSQL database with intelligent filtering
    and preprocessing capabilities.
    """
    
    def __init__(self, db_engine: Engine):
        """
        Initialize data extractor
        
        Args:
            db_engine: SQLAlchemy database engine (pgsql_engine)
        """
        self.db_engine = db_engine
        logger.info("[DataExtractor] Initialized")
    
    def extract_embedding_data(
        self,
        room_id: str,
        target_datetime: datetime,
        time_window_days: int = 7,
        growth_day_window: int = 3,
        image_aggregation_window_minutes: int = 30
    ) -> pd.DataFrame:
        """
        Alias for extract_current_embedding_data to maintain compatibility
        
        Args:
            room_id: Room number (607/608/611/612)
            target_datetime: Target datetime for analysis
            time_window_days: Entry date time window (±days)
            growth_day_window: Growth day window (±days)
            image_aggregation_window_minutes: Time window for aggregating multiple images (minutes)
            
        Returns:
            DataFrame containing embedding data with multi-image support
        """
        return self.extract_current_embedding_data(
            room_id=room_id,
            target_datetime=target_datetime,
            time_window_days=time_window_days,
            growth_day_window=growth_day_window,
            image_aggregation_window_minutes=image_aggregation_window_minutes
        )
    
    def extract_current_embedding_data(
        self,
        room_id: str,
        target_datetime: datetime,
        time_window_days: int = 7,
        growth_day_window: int = 3,
        image_aggregation_window_minutes: int = 30
    ) -> pd.DataFrame:
        """
        Extract current image embedding data from MushroomImageEmbedding table
        Enhanced to support multi-image aggregation for comprehensive analysis
        
        New Implementation: Direct growth day-based filtering
        - Determines target growth day from recent data or parameters
        - Filters data directly by growth day window [target_growth_day ± growth_day_window]
        - No longer uses collection_datetime time window filtering
        - Prioritizes data by similarity, room_id match, and latest collection_datetime
        
        Args:
            room_id: Room number (607/608/611/612)
            target_datetime: Target datetime for analysis (used for aggregation window)
            time_window_days: Legacy parameter (kept for compatibility, not used in filtering)
            growth_day_window: Growth day window (±days) for filtering
            image_aggregation_window_minutes: Time window for aggregating multiple images (minutes)
            
        Returns:
            DataFrame containing embedding, env_sensor_status, device configs, etc.
            Enhanced with multi-image analysis metadata
            
        Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, Multi-Image Enhancement
        """
        logger.info(
            f"[DataExtractor] Extracting embedding data with multi-image support: "
            f"room_id={room_id}, datetime={target_datetime}, "
            f"growth_day_window=±{growth_day_window}days, "
            f"image_aggregation_window={image_aggregation_window_minutes}min"
        )
        
        try:
            from sqlalchemy import select, and_, desc
            from sqlalchemy.orm import Session
            from utils.create_table import MushroomImageEmbedding
            
            # Calculate time window for image aggregation (still used for metadata)
            aggregation_start = target_datetime - timedelta(minutes=image_aggregation_window_minutes)
            aggregation_end = target_datetime + timedelta(minutes=image_aggregation_window_minutes)
            
            with Session(self.db_engine) as session:
                # Step 1: Determine target growth day from the most recent record for this room
                reference_query = (
                    select(MushroomImageEmbedding.growth_day)
                    .where(
                        and_(
                            MushroomImageEmbedding.room_id == room_id,
                            MushroomImageEmbedding.collection_datetime <= target_datetime
                        )
                    )
                    .order_by(MushroomImageEmbedding.collection_datetime.desc())
                    .limit(1)
                )
                
                reference_result = session.execute(reference_query).first()
                
                if reference_result is None:
                    logger.warning(
                        f"[DataExtractor] No reference data found for room_id={room_id} "
                        f"before {target_datetime}"
                    )
                    return pd.DataFrame()
                
                target_growth_day = reference_result[0]
                min_growth_day = target_growth_day - growth_day_window
                max_growth_day = target_growth_day + growth_day_window
                
                logger.debug(
                    f"[DataExtractor] Using target_growth_day={target_growth_day}, "
                    f"range=[{min_growth_day}, {max_growth_day}]"
                )
                
                # Step 2: Query data directly based on growth day window
                # No collection_datetime filtering, only growth day-based filtering
                query = (
                    select(
                        MushroomImageEmbedding.id,
                        MushroomImageEmbedding.collection_datetime,
                        MushroomImageEmbedding.room_id,
                        MushroomImageEmbedding.in_date,
                        MushroomImageEmbedding.in_num,
                        MushroomImageEmbedding.growth_day,
                        MushroomImageEmbedding.embedding,
                        MushroomImageEmbedding.semantic_description,
                        MushroomImageEmbedding.llama_description,
                        MushroomImageEmbedding.image_quality_score,
                        MushroomImageEmbedding.env_sensor_status,
                        MushroomImageEmbedding.air_cooler_config,
                        MushroomImageEmbedding.fresh_fan_config,
                        MushroomImageEmbedding.humidifier_config,
                        MushroomImageEmbedding.light_config,
                        MushroomImageEmbedding.image_path,
                    )
                    .where(
                        and_(
                            # Growth day window filter (primary filter)
                            MushroomImageEmbedding.growth_day >= min_growth_day,
                            MushroomImageEmbedding.growth_day <= max_growth_day,
                        )
                    )
                    # Step 3: Order by priority: room_id match first, then latest collection_datetime
                    .order_by(
                        # Prioritize exact room_id match
                        (MushroomImageEmbedding.room_id == room_id).desc(),
                        # Then by latest collection_datetime
                        MushroomImageEmbedding.collection_datetime.desc()
                    )
                )
                
                # Execute query and convert to DataFrame
                result = session.execute(query)
                rows = result.fetchall()
                
                if not rows:
                    logger.warning(
                        f"[DataExtractor] No data found matching growth day filter: "
                        f"growth_day=[{min_growth_day}, {max_growth_day}]"
                    )
                    return pd.DataFrame()
                
                # Convert to DataFrame
                df = pd.DataFrame(
                    rows,
                    columns=[
                        'id', 'collection_datetime', 'room_id', 'in_date', 'in_num',
                        'growth_day', 'embedding', 'semantic_description',
                        'llama_description', 'image_quality_score', 'env_sensor_status',
                        'air_cooler_config', 'fresh_fan_config', 'humidifier_config',
                        'light_config', 'image_path'
                    ]
                )
                
                # Step 4: Further prioritize records from the target room_id
                target_room_records = df[df['room_id'] == room_id]
                if not target_room_records.empty:
                    # Use records from target room if available
                    df = target_room_records
                    logger.debug(
                        f"[DataExtractor] Using {len(df)} records from target room {room_id}"
                    )
                else:
                    # Use all records if no target room records found
                    logger.debug(
                        f"[DataExtractor] No records from target room {room_id}, "
                        f"using {len(df)} records from other rooms"
                    )
                
                # Step 5: Add multi-image analysis metadata
                df = self._add_multi_image_metadata(df, aggregation_start, aggregation_end)
                
                logger.info(
                    f"[DataExtractor] Successfully extracted {len(df)} records "
                    f"for room_id={room_id} with multi-image analysis"
                )
                
                return df
                
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to extract embedding data: {e}",
                exc_info=True
            )
            return pd.DataFrame()
    
    def extract_historical_embedding_data_for_similarity(
        self,
        room_id: str,
        current_in_date: date,
        target_growth_day: int,
        growth_day_window: int = 3,
        top_k: int = 3
    ) -> pd.DataFrame:
        """
        Extract historical embedding data for similarity matching
        
        This method specifically excludes the current batch (current_in_date) and only
        searches historical batches for similarity matching. It finds the top-k most
        similar historical cases based on growth stage and environmental parameters.
        
        Args:
            room_id: Target room number
            current_in_date: Current batch entry date (to be excluded)
            target_growth_day: Target growth day for matching
            growth_day_window: Growth day window (±days)
            top_k: Number of top similar cases to return
            
        Returns:
            DataFrame containing historical embedding data with environmental parameters
            Sorted by similarity and environmental parameter closeness
            
        Requirements: Historical batch exclusion, similarity-based matching
        """
        logger.info(
            f"[DataExtractor] Extracting historical embedding data for similarity matching: "
            f"room_id={room_id}, current_in_date={current_in_date}, "
            f"target_growth_day={target_growth_day}, growth_day_window=±{growth_day_window}, "
            f"top_k={top_k}"
        )
        
        try:
            from sqlalchemy import select, and_, not_
            from sqlalchemy.orm import Session
            from utils.create_table import MushroomImageEmbedding
            
            # Calculate growth day range
            min_growth_day = target_growth_day - growth_day_window
            max_growth_day = target_growth_day + growth_day_window
            
            logger.debug(
                f"[DataExtractor] Historical search parameters: "
                f"exclude_in_date={current_in_date}, "
                f"growth_day_range=[{min_growth_day}, {max_growth_day}]"
            )
            
            with Session(self.db_engine) as session:
                # Query historical data excluding current batch
                query = (
                    select(
                        MushroomImageEmbedding.id,
                        MushroomImageEmbedding.collection_datetime,
                        MushroomImageEmbedding.room_id,
                        MushroomImageEmbedding.in_date,
                        MushroomImageEmbedding.in_num,
                        MushroomImageEmbedding.growth_day,
                        MushroomImageEmbedding.embedding,
                        MushroomImageEmbedding.semantic_description,
                        MushroomImageEmbedding.llama_description,
                        MushroomImageEmbedding.image_quality_score,
                        MushroomImageEmbedding.env_sensor_status,
                        MushroomImageEmbedding.air_cooler_config,
                        MushroomImageEmbedding.fresh_fan_config,
                        MushroomImageEmbedding.humidifier_config,
                        MushroomImageEmbedding.light_config,
                        MushroomImageEmbedding.image_path,
                    )
                    .where(
                        and_(
                            # Exclude current batch
                            MushroomImageEmbedding.in_date != current_in_date,
                            # Growth day window filter
                            MushroomImageEmbedding.growth_day >= min_growth_day,
                            MushroomImageEmbedding.growth_day <= max_growth_day,
                            # Ensure we have valid embedding data
                            MushroomImageEmbedding.embedding.isnot(None),
                            # Ensure we have environmental sensor data
                            MushroomImageEmbedding.env_sensor_status.isnot(None)
                        )
                    )
                    # Order by collection_datetime to get diverse historical data
                    .order_by(MushroomImageEmbedding.collection_datetime.desc())
                )
                
                # Execute query
                result = session.execute(query)
                rows = result.fetchall()
                
                if not rows:
                    logger.warning(
                        f"[DataExtractor] No historical data found for similarity matching: "
                        f"exclude_in_date={current_in_date}, "
                        f"growth_day_range=[{min_growth_day}, {max_growth_day}]"
                    )
                    return pd.DataFrame()
                
                # Convert to DataFrame
                df = pd.DataFrame(
                    rows,
                    columns=[
                        'id', 'collection_datetime', 'room_id', 'in_date', 'in_num',
                        'growth_day', 'embedding', 'semantic_description',
                        'llama_description', 'image_quality_score', 'env_sensor_status',
                        'air_cooler_config', 'fresh_fan_config', 'humidifier_config',
                        'light_config', 'image_path'
                    ]
                )
                
                # Extract environmental parameters for similarity calculation
                df = self._extract_env_parameters_from_sensor_data(df)
                
                # Add batch information for analysis
                df['is_historical_batch'] = True
                df['excluded_current_batch'] = current_in_date
                
                logger.info(
                    f"[DataExtractor] Successfully extracted {len(df)} historical records "
                    f"from {df['in_date'].nunique()} different batches for similarity matching"
                )
                
                # Log batch distribution
                batch_counts = df['in_date'].value_counts().head(5)
                logger.debug(
                    f"[DataExtractor] Top historical batches: "
                    f"{dict(batch_counts)}"
                )
                
                return df
                
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to extract historical embedding data: {e}",
                exc_info=True
            )
            return pd.DataFrame()
    
    def _extract_env_parameters_from_sensor_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract environmental parameters from sensor data for similarity matching
        
        Args:
            df: DataFrame with env_sensor_status column
            
        Returns:
            DataFrame with additional environmental parameter columns
        """
        if df.empty or 'env_sensor_status' not in df.columns:
            return df
        
        try:
            import json
            
            # Initialize environmental parameter columns
            df['temperature'] = None
            df['humidity'] = None
            df['co2'] = None
            
            # Extract parameters from JSON sensor data
            for idx, row in df.iterrows():
                if row['env_sensor_status'] is not None:
                    try:
                        if isinstance(row['env_sensor_status'], str):
                            sensor_data = json.loads(row['env_sensor_status'])
                        else:
                            sensor_data = row['env_sensor_status']
                        
                        # Extract temperature, humidity, CO2
                        df.at[idx, 'temperature'] = sensor_data.get('temperature')
                        df.at[idx, 'humidity'] = sensor_data.get('humidity')
                        df.at[idx, 'co2'] = sensor_data.get('co2')
                        
                    except (json.JSONDecodeError, TypeError, AttributeError) as e:
                        logger.debug(f"[DataExtractor] Failed to parse sensor data for row {idx}: {e}")
                        continue
            
            # Convert to numeric types
            df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce')
            df['humidity'] = pd.to_numeric(df['humidity'], errors='coerce')
            df['co2'] = pd.to_numeric(df['co2'], errors='coerce')
            
            # Log extraction statistics
            valid_temp = df['temperature'].notna().sum()
            valid_humidity = df['humidity'].notna().sum()
            valid_co2 = df['co2'].notna().sum()
            
            logger.debug(
                f"[DataExtractor] Environmental parameter extraction: "
                f"temperature={valid_temp}/{len(df)}, "
                f"humidity={valid_humidity}/{len(df)}, "
                f"co2={valid_co2}/{len(df)}"
            )
            
            return df
            
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to extract environmental parameters: {e}",
                exc_info=True
            )
            return df
    
    def find_top_similar_historical_cases(
        self,
        current_embedding: 'np.ndarray',
        current_env_params: dict,
        room_id: str,
        current_in_date: date,
        target_growth_day: int,
        growth_day_window: int = 3,
        top_k: int = 3
    ) -> pd.DataFrame:
        """
        Find top-k most similar historical cases based on embedding similarity
        and environmental parameter closeness
        
        Args:
            current_embedding: Current embedding vector
            current_env_params: Current environmental parameters (temp, humidity, co2)
            room_id: Target room number
            current_in_date: Current batch entry date (to be excluded)
            target_growth_day: Target growth day
            growth_day_window: Growth day window (±days)
            top_k: Number of top similar cases to return
            
        Returns:
            DataFrame with top-k most similar historical cases
            Includes similarity scores and environmental parameter differences
        """
        logger.info(
            f"[DataExtractor] Finding top-{top_k} similar historical cases: "
            f"room_id={room_id}, current_in_date={current_in_date}, "
            f"target_growth_day={target_growth_day}"
        )
        
        try:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            
            # Get historical data
            historical_df = self.extract_historical_embedding_data_for_similarity(
                room_id=room_id,
                current_in_date=current_in_date,
                target_growth_day=target_growth_day,
                growth_day_window=growth_day_window,
                top_k=top_k * 5  # Get more candidates for better selection
            )
            
            if historical_df.empty:
                logger.warning("[DataExtractor] No historical data available for similarity matching")
                return pd.DataFrame()
            
            # Calculate embedding similarities
            similarities = []
            valid_indices = []
            
            for idx, row in historical_df.iterrows():
                try:
                    if row['embedding'] is not None:
                        hist_embedding = np.array(row['embedding'])
                        if hist_embedding.shape == current_embedding.shape:
                            similarity = cosine_similarity(
                                [current_embedding], [hist_embedding]
                            )[0][0]
                            similarities.append(similarity)
                            valid_indices.append(idx)
                        else:
                            logger.debug(f"[DataExtractor] Embedding shape mismatch for row {idx}")
                    else:
                        logger.debug(f"[DataExtractor] No embedding data for row {idx}")
                except Exception as e:
                    logger.debug(f"[DataExtractor] Error calculating similarity for row {idx}: {e}")
                    continue
            
            if not similarities:
                logger.warning("[DataExtractor] No valid embeddings found for similarity calculation")
                return pd.DataFrame()
            
            # Filter to valid rows and add similarity scores
            valid_df = historical_df.loc[valid_indices].copy()
            valid_df['embedding_similarity'] = similarities
            
            # Calculate environmental parameter differences
            current_temp = current_env_params.get('temperature')
            current_humidity = current_env_params.get('humidity')
            current_co2 = current_env_params.get('co2')
            
            valid_df['temp_diff'] = None
            valid_df['humidity_diff'] = None
            valid_df['co2_diff'] = None
            valid_df['env_similarity_score'] = 0.0
            
            for idx, row in valid_df.iterrows():
                temp_diff = abs(row['temperature'] - current_temp) if (
                    current_temp is not None and row['temperature'] is not None
                ) else float('inf')
                
                humidity_diff = abs(row['humidity'] - current_humidity) if (
                    current_humidity is not None and row['humidity'] is not None
                ) else float('inf')
                
                co2_diff = abs(row['co2'] - current_co2) if (
                    current_co2 is not None and row['co2'] is not None
                ) else float('inf')
                
                valid_df.at[idx, 'temp_diff'] = temp_diff
                valid_df.at[idx, 'humidity_diff'] = humidity_diff
                valid_df.at[idx, 'co2_diff'] = co2_diff
                
                # Calculate combined environmental similarity score
                # Lower differences = higher similarity
                env_score = 0.0
                if temp_diff != float('inf'):
                    env_score += 1.0 / (1.0 + temp_diff / 10.0)  # Normalize by 10°C
                if humidity_diff != float('inf'):
                    env_score += 1.0 / (1.0 + humidity_diff / 20.0)  # Normalize by 20%
                if co2_diff != float('inf'):
                    env_score += 1.0 / (1.0 + co2_diff / 500.0)  # Normalize by 500ppm
                
                valid_df.at[idx, 'env_similarity_score'] = env_score
            
            # Calculate combined similarity score
            # 70% embedding similarity + 30% environmental similarity
            max_env_score = valid_df['env_similarity_score'].max()
            if max_env_score > 0:
                normalized_env_scores = valid_df['env_similarity_score'] / max_env_score
            else:
                normalized_env_scores = 0.0
            
            valid_df['combined_similarity'] = (
                0.7 * valid_df['embedding_similarity'] + 
                0.3 * normalized_env_scores
            )
            
            # Sort by combined similarity and select top-k
            top_similar = valid_df.nlargest(top_k, 'combined_similarity')
            
            logger.info(
                f"[DataExtractor] Found {len(top_similar)} similar historical cases "
                f"with avg embedding similarity: {top_similar['embedding_similarity'].mean():.3f}, "
                f"avg combined similarity: {top_similar['combined_similarity'].mean():.3f}"
            )
            
            # Log top cases
            for i, (_, row) in enumerate(top_similar.iterrows(), 1):
                logger.debug(
                    f"[DataExtractor] Top-{i}: batch={row['in_date']}, "
                    f"growth_day={row['growth_day']}, "
                    f"embedding_sim={row['embedding_similarity']:.3f}, "
                    f"combined_sim={row['combined_similarity']:.3f}, "
                    f"temp_diff={row['temp_diff']:.1f}°C, "
                    f"humidity_diff={row['humidity_diff']:.1f}%, "
                    f"co2_diff={row['co2_diff']:.0f}ppm"
                )
            
            return top_similar
            
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to find similar historical cases: {e}",
                exc_info=True
            )
            return pd.DataFrame()

    def _add_multi_image_metadata(
        self, 
        df: pd.DataFrame, 
        aggregation_start: datetime, 
        aggregation_end: datetime
    ) -> pd.DataFrame:
        """
        Add multi-image analysis metadata to the DataFrame
        
        Args:
            df: DataFrame with image embedding data
            aggregation_start: Start time for image aggregation window
            aggregation_end: End time for image aggregation window
            
        Returns:
            DataFrame with added multi-image metadata columns
        """
        if df.empty:
            return df
        
        # Identify images within aggregation window
        df['within_aggregation_window'] = (
            (df['collection_datetime'] >= aggregation_start) & 
            (df['collection_datetime'] <= aggregation_end)
        )
        
        # Count images within aggregation window
        images_in_window = df[df['within_aggregation_window']].shape[0]
        
        # Add metadata columns
        df['total_images_for_analysis'] = len(df)
        df['images_in_aggregation_window'] = images_in_window
        df['aggregation_window_start'] = aggregation_start
        df['aggregation_window_end'] = aggregation_end
        
        # Calculate image quality statistics
        if 'image_quality_score' in df.columns:
            quality_scores = df['image_quality_score'].dropna()
            if not quality_scores.empty:
                df['avg_image_quality'] = quality_scores.mean()
                df['min_image_quality'] = quality_scores.min()
                df['max_image_quality'] = quality_scores.max()
            else:
                df['avg_image_quality'] = None
                df['min_image_quality'] = None
                df['max_image_quality'] = None
        
        # Add time-based weighting for analysis
        # More recent images get higher weights
        time_diffs = (df['collection_datetime'] - aggregation_start).dt.total_seconds()
        max_time_diff = time_diffs.max() if not time_diffs.empty else 1
        df['time_weight'] = 1.0 - (time_diffs / max_time_diff) * 0.5  # Weight range: 0.5 to 1.0
        
        logger.debug(
            f"[DataExtractor] Added multi-image metadata: "
            f"total_images={len(df)}, images_in_window={images_in_window}"
        )
        
        return df
    
    def extract_env_daily_stats(
        self,
        room_id: str,
        target_date: date,
        days_range: int = 1
    ) -> pd.DataFrame:
        """
        Extract environmental daily statistics from MushroomEnvDailyStats table
        
        Extracts environmental statistics for target_date ± days_range and computes
        trend information (temperature/humidity/CO2 change rates and directions).
        
        Args:
            room_id: Room number
            target_date: Target date
            days_range: Number of days before and after target date (default: 1)
            
        Returns:
            DataFrame containing environmental statistics with trend information
            Columns include: room_id, stat_date, in_day_num, is_growth_phase,
                           temp_median, temp_min, temp_max, temp_q25, temp_q75,
                           humidity_median, humidity_min, humidity_max, humidity_q25, humidity_q75,
                           co2_median, co2_min, co2_max, co2_q25, co2_q75,
                           temp_change_rate, humidity_change_rate, co2_change_rate,
                           temp_trend, humidity_trend, co2_trend
            
        Requirements: 2.1, 2.2, 2.3, 2.6
        """
        logger.info(
            f"[DataExtractor] Extracting env stats: "
            f"room_id={room_id}, date={target_date}, days_range=±{days_range}"
        )
        
        try:
            from sqlalchemy import select, and_
            from sqlalchemy.orm import Session
            from utils.create_table import MushroomEnvDailyStats
            
            # Calculate date range
            min_date = target_date - timedelta(days=days_range)
            max_date = target_date + timedelta(days=days_range)
            
            logger.debug(
                f"[DataExtractor] Date range: [{min_date}, {max_date}]"
            )
            
            with Session(self.db_engine) as session:
                # Build query using idx_room_date index
                query = (
                    select(
                        MushroomEnvDailyStats.room_id,
                        MushroomEnvDailyStats.stat_date,
                        MushroomEnvDailyStats.in_day_num,
                        MushroomEnvDailyStats.is_growth_phase,
                        # Temperature statistics
                        MushroomEnvDailyStats.temp_median,
                        MushroomEnvDailyStats.temp_min,
                        MushroomEnvDailyStats.temp_max,
                        MushroomEnvDailyStats.temp_q25,
                        MushroomEnvDailyStats.temp_q75,
                        # Humidity statistics
                        MushroomEnvDailyStats.humidity_median,
                        MushroomEnvDailyStats.humidity_min,
                        MushroomEnvDailyStats.humidity_max,
                        MushroomEnvDailyStats.humidity_q25,
                        MushroomEnvDailyStats.humidity_q75,
                        # CO2 statistics
                        MushroomEnvDailyStats.co2_median,
                        MushroomEnvDailyStats.co2_min,
                        MushroomEnvDailyStats.co2_max,
                        MushroomEnvDailyStats.co2_q25,
                        MushroomEnvDailyStats.co2_q75,
                    )
                    .where(
                        and_(
                            # Room filter (uses idx_room_date)
                            MushroomEnvDailyStats.room_id == room_id,
                            # Date range filter (uses idx_room_date)
                            MushroomEnvDailyStats.stat_date >= min_date,
                            MushroomEnvDailyStats.stat_date <= max_date,
                        )
                    )
                    .order_by(MushroomEnvDailyStats.stat_date.asc())  # Ascending order for trend calculation
                )
                
                # Execute query
                result = session.execute(query)
                rows = result.fetchall()
                
                if not rows:
                    logger.warning(
                        f"[DataExtractor] No env stats found for room_id={room_id}, "
                        f"date_range=[{min_date}, {max_date}]"
                    )
                    return pd.DataFrame()
                
                # Convert to DataFrame
                df = pd.DataFrame(
                    rows,
                    columns=[
                        'room_id', 'stat_date', 'in_day_num', 'is_growth_phase',
                        'temp_median', 'temp_min', 'temp_max', 'temp_q25', 'temp_q75',
                        'humidity_median', 'humidity_min', 'humidity_max', 'humidity_q25', 'humidity_q75',
                        'co2_median', 'co2_min', 'co2_max', 'co2_q25', 'co2_q75',
                    ]
                )
                
                logger.info(
                    f"[DataExtractor] Successfully extracted {len(df)} env stat records "
                    f"for room_id={room_id}"
                )
                
                # Compute trend information (requirement 2.6)
                df = self._compute_env_trends(df)
                
                return df
                
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to extract env stats: {e}",
                exc_info=True
            )
            return pd.DataFrame()
    
    def _compute_env_trends(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute environmental parameter trends from adjacent day statistics
        
        Calculates:
        - Change rates: (current - previous) / previous * 100 (%)
        - Trend directions: "上升" (rising), "下降" (falling), "稳定" (stable)
        
        Args:
            df: DataFrame with environmental statistics sorted by stat_date
            
        Returns:
            DataFrame with added trend columns:
            - temp_change_rate, humidity_change_rate, co2_change_rate
            - temp_trend, humidity_trend, co2_trend
            
        Requirements: 2.6
        """
        if df.empty or len(df) < 2:
            # Not enough data to compute trends
            logger.debug("[DataExtractor] Insufficient data for trend computation")
            df['temp_change_rate'] = None
            df['humidity_change_rate'] = None
            df['co2_change_rate'] = None
            df['temp_trend'] = None
            df['humidity_trend'] = None
            df['co2_trend'] = None
            return df
        
        # Initialize trend columns
        df['temp_change_rate'] = None
        df['humidity_change_rate'] = None
        df['co2_change_rate'] = None
        df['temp_trend'] = None
        df['humidity_trend'] = None
        df['co2_trend'] = None
        
        # Compute change rates and trends for each row (compared to previous row)
        for i in range(1, len(df)):
            prev_row = df.iloc[i - 1]
            curr_row = df.iloc[i]
            
            # Temperature trend
            if pd.notna(prev_row['temp_median']) and pd.notna(curr_row['temp_median']):
                if prev_row['temp_median'] != 0:
                    temp_change = (curr_row['temp_median'] - prev_row['temp_median']) / prev_row['temp_median'] * 100
                    df.at[i, 'temp_change_rate'] = round(temp_change, 2)
                    
                    # Determine trend direction (threshold: ±2%)
                    if temp_change > 2:
                        df.at[i, 'temp_trend'] = "上升"
                    elif temp_change < -2:
                        df.at[i, 'temp_trend'] = "下降"
                    else:
                        df.at[i, 'temp_trend'] = "稳定"
            
            # Humidity trend
            if pd.notna(prev_row['humidity_median']) and pd.notna(curr_row['humidity_median']):
                if prev_row['humidity_median'] != 0:
                    humidity_change = (curr_row['humidity_median'] - prev_row['humidity_median']) / prev_row['humidity_median'] * 100
                    df.at[i, 'humidity_change_rate'] = round(humidity_change, 2)
                    
                    # Determine trend direction (threshold: ±3%)
                    if humidity_change > 3:
                        df.at[i, 'humidity_trend'] = "上升"
                    elif humidity_change < -3:
                        df.at[i, 'humidity_trend'] = "下降"
                    else:
                        df.at[i, 'humidity_trend'] = "稳定"
            
            # CO2 trend
            if pd.notna(prev_row['co2_median']) and pd.notna(curr_row['co2_median']):
                if prev_row['co2_median'] != 0:
                    co2_change = (curr_row['co2_median'] - prev_row['co2_median']) / prev_row['co2_median'] * 100
                    df.at[i, 'co2_change_rate'] = round(co2_change, 2)
                    
                    # Determine trend direction (threshold: ±5%)
                    if co2_change > 5:
                        df.at[i, 'co2_trend'] = "上升"
                    elif co2_change < -5:
                        df.at[i, 'co2_trend'] = "下降"
                    else:
                        df.at[i, 'co2_trend'] = "稳定"
        
        logger.debug(
            f"[DataExtractor] Computed trends for {len(df)} records"
        )
        
        return df
    
    def extract_device_changes(
        self,
        room_id: str,
        start_time: datetime,
        end_time: datetime,
        device_types: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Extract device setpoint change records from DeviceSetpointChange table
        
        Queries the DeviceSetpointChange table with:
        - Room ID filtering
        - Time range filtering (start_time to end_time)
        - Optional device type filtering
        - Results sorted by change_time in descending order
        - Uses idx_room_change_time index for optimization
        
        Args:
            room_id: Room number
            start_time: Start time of the query range
            end_time: End time of the query range
            device_types: List of device types to filter (optional)
                         e.g., ['air_cooler', 'fresh_air_fan', 'humidifier', 'grow_light']
            
        Returns:
            DataFrame containing device change records with columns:
            - room_id: Room number
            - device_type: Device type
            - device_name: Device name
            - point_name: Point name
            - point_description: Point description
            - change_time: Change timestamp
            - previous_value: Value before change
            - current_value: Value after change
            - change_magnitude: Magnitude of change
            - change_type: Type of change
            - change_detail: Change details
            
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
        """
        logger.info(
            f"[DataExtractor] Extracting device changes: "
            f"room_id={room_id}, time_range=[{start_time}, {end_time}]"
        )
        
        if device_types:
            logger.debug(f"[DataExtractor] Filtering by device_types: {device_types}")
        
        try:
            from sqlalchemy import select, and_
            from sqlalchemy.orm import Session
            from utils.create_table import DeviceSetpointChange
            
            with Session(self.db_engine) as session:
                # Build query with filters
                # Uses idx_room_change_time index for room_id + change_time filtering
                conditions = [
                    DeviceSetpointChange.room_id == room_id,
                    DeviceSetpointChange.change_time >= start_time,
                    DeviceSetpointChange.change_time <= end_time,
                ]
                
                # Add device type filter if specified (uses idx_device_type index)
                if device_types:
                    conditions.append(DeviceSetpointChange.device_type.in_(device_types))
                
                query = (
                    select(
                        DeviceSetpointChange.room_id,
                        DeviceSetpointChange.device_type,
                        DeviceSetpointChange.device_name,
                        DeviceSetpointChange.point_name,
                        DeviceSetpointChange.point_description,
                        DeviceSetpointChange.change_time,
                        DeviceSetpointChange.previous_value,
                        DeviceSetpointChange.current_value,
                        DeviceSetpointChange.change_magnitude,
                        DeviceSetpointChange.change_type,
                        DeviceSetpointChange.change_detail,
                    )
                    .where(and_(*conditions))
                    .order_by(DeviceSetpointChange.change_time.desc())  # Descending order (most recent first)
                )
                
                # Execute query
                result = session.execute(query)
                rows = result.fetchall()
                
                if not rows:
                    logger.warning(
                        f"[DataExtractor] No device changes found for room_id={room_id}, "
                        f"time_range=[{start_time}, {end_time}]"
                    )
                    return pd.DataFrame()
                
                # Convert to DataFrame
                df = pd.DataFrame(
                    rows,
                    columns=[
                        'room_id', 'device_type', 'device_name', 'point_name',
                        'point_description', 'change_time', 'previous_value',
                        'current_value', 'change_magnitude', 'change_type',
                        'change_detail'
                    ]
                )
                
                logger.info(
                    f"[DataExtractor] Successfully extracted {len(df)} device change records "
                    f"for room_id={room_id}"
                )
                
                # Log summary by device type
                if not df.empty:
                    device_type_counts = df['device_type'].value_counts().to_dict()
                    logger.debug(
                        f"[DataExtractor] Device changes by type: {device_type_counts}"
                    )
                
                return df
                
        except Exception as e:
            logger.error(
                f"[DataExtractor] Failed to extract device changes: {e}",
                exc_info=True
            )
            return pd.DataFrame()
    
    def validate_env_params(self, data: pd.DataFrame) -> List[str]:
        """
        Validate environmental parameters are within reasonable ranges
        
        Validates:
        - Temperature: 0-40°C
        - Humidity: 0-100%
        - CO2: 0-5000ppm
        
        When parameters are out of range, warning logs are recorded and
        warning messages are returned. The data itself is not modified.
        
        Args:
            data: DataFrame containing environmental parameters.
                  Expected columns: 'temperature', 'humidity', 'co2'
                  May also contain columns like 'temp_median', 'temp_min', 'temp_max',
                  'humidity_median', 'humidity_min', 'humidity_max',
                  'co2_median', 'co2_min', 'co2_max' for statistical data
            
        Returns:
            List of warning messages for out-of-range values
            
        Requirements: 11.1
        """
        warnings = []
        
        if data.empty:
            logger.debug("[DataExtractor] Empty DataFrame provided for validation")
            return warnings
        
        logger.info(
            f"[DataExtractor] Validating environmental parameters for {len(data)} records"
        )
        
        # Define validation ranges
        TEMP_MIN, TEMP_MAX = 0.0, 40.0  # °C
        HUMIDITY_MIN, HUMIDITY_MAX = 0.0, 100.0  # %
        CO2_MIN, CO2_MAX = 0.0, 5000.0  # ppm
        
        # Track columns to validate
        temp_columns = []
        humidity_columns = []
        co2_columns = []
        
        # Identify which columns exist in the DataFrame
        if 'temperature' in data.columns:
            temp_columns.append('temperature')
        if 'temp_median' in data.columns:
            temp_columns.extend(['temp_median', 'temp_min', 'temp_max', 'temp_q25', 'temp_q75'])
        
        if 'humidity' in data.columns:
            humidity_columns.append('humidity')
        if 'humidity_median' in data.columns:
            humidity_columns.extend(['humidity_median', 'humidity_min', 'humidity_max', 'humidity_q25', 'humidity_q75'])
        
        if 'co2' in data.columns:
            co2_columns.append('co2')
        if 'co2_median' in data.columns:
            co2_columns.extend(['co2_median', 'co2_min', 'co2_max', 'co2_q25', 'co2_q75'])
        
        # Filter to only existing columns
        temp_columns = [col for col in temp_columns if col in data.columns]
        humidity_columns = [col for col in humidity_columns if col in data.columns]
        co2_columns = [col for col in co2_columns if col in data.columns]
        
        # Validate temperature parameters
        for col in temp_columns:
            for idx, value in data[col].items():
                if pd.notna(value):  # Skip NaN/None values
                    if value < TEMP_MIN or value > TEMP_MAX:
                        warning_msg = (
                            f"Temperature out of range: {col}={value:.2f}°C "
                            f"(valid range: {TEMP_MIN}-{TEMP_MAX}°C) at index {idx}"
                        )
                        warnings.append(warning_msg)
                        logger.warning(f"[DataExtractor] {warning_msg}")
        
        # Validate humidity parameters
        for col in humidity_columns:
            for idx, value in data[col].items():
                if pd.notna(value):  # Skip NaN/None values
                    if value < HUMIDITY_MIN or value > HUMIDITY_MAX:
                        warning_msg = (
                            f"Humidity out of range: {col}={value:.2f}% "
                            f"(valid range: {HUMIDITY_MIN}-{HUMIDITY_MAX}%) at index {idx}"
                        )
                        warnings.append(warning_msg)
                        logger.warning(f"[DataExtractor] {warning_msg}")
        
        # Validate CO2 parameters
        for col in co2_columns:
            for idx, value in data[col].items():
                if pd.notna(value):  # Skip NaN/None values
                    if value < CO2_MIN or value > CO2_MAX:
                        warning_msg = (
                            f"CO2 out of range: {col}={value:.2f}ppm "
                            f"(valid range: {CO2_MIN}-{CO2_MAX}ppm) at index {idx}"
                        )
                        warnings.append(warning_msg)
                        logger.warning(f"[DataExtractor] {warning_msg}")
        
        if warnings:
            logger.warning(
                f"[DataExtractor] Validation found {len(warnings)} out-of-range values"
            )
        else:
            logger.info(
                "[DataExtractor] All environmental parameters are within valid ranges"
            )
        
        return warnings
