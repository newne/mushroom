"""
CLIP Matcher Module

This module implements CLIP-based similarity matching to find historical cases
that are most similar to the current mushroom growth state.

It uses pgvector's vector similarity search capabilities to efficiently find
the top-K most similar cases based on image embedding vectors.
"""

from datetime import date
from typing import Dict, List

import numpy as np
from loguru import logger
from sqlalchemy import Engine

from decision_analysis.data_models import SimilarCase


class CLIPMatcher:
    """
    CLIP similarity matcher for finding historical cases
    
    Uses pgvector's vector similarity search to find the most similar
    historical cases based on image embeddings.
    """
    
    def __init__(self, db_engine: Engine):
        """
        Initialize CLIP matcher
        
        Args:
            db_engine: SQLAlchemy database engine (pgsql_engine)
        """
        self.db_engine = db_engine
        logger.info("[CLIPMatcher] Initialized")
    
    def find_similar_cases(
        self,
        query_embedding: np.ndarray,
        room_id: str,
        in_date: date,
        growth_day: int,
        top_k: int = 3,
        date_window_days: int = 7,
        growth_day_window: int = 3
    ) -> List[SimilarCase]:
        """
        Find similar historical cases using CLIP vector similarity
        
        Process:
        1. Filter by room_id (same room)
        2. Filter by entry date window (±date_window_days)
        3. Filter by growth day window (±growth_day_window)
        4. Use pgvector's <-> operator for similarity search
        5. Return top-K most similar cases
        
        Args:
            query_embedding: Query vector (512 dimensions)
            room_id: Room number
            in_date: Entry date
            growth_day: Growth day
            top_k: Number of results to return
            date_window_days: Entry date window (±days)
            growth_day_window: Growth day window (±days)
            
        Returns:
            List of SimilarCase objects with similarity scores and metadata
            
        Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
        """
        logger.info(
            f"[CLIPMatcher] Finding similar cases: "
            f"room_id={room_id}, growth_day={growth_day}, top_k={top_k}"
        )
        
        try:
            from datetime import timedelta
            from sqlalchemy import text
            
            # Calculate date range
            min_date = in_date - timedelta(days=date_window_days)
            max_date = in_date + timedelta(days=date_window_days)
            
            # Calculate growth day range
            min_growth_day = growth_day - growth_day_window
            max_growth_day = growth_day + growth_day_window
            
            # Convert numpy array to list for SQL query
            embedding_list = query_embedding.tolist()
            
            # Build SQL query with pgvector similarity search
            # Using <-> operator for L2 distance (lower is more similar)
            query = text("""
                SELECT 
                    room_id,
                    growth_day,
                    collection_datetime,
                    env_sensor_status,
                    air_cooler_config,
                    fresh_fan_config,
                    humidifier_config,
                    light_config,
                    embedding <-> :query_vector AS distance
                FROM mushroom_embedding
                WHERE room_id = :room_id
                    AND in_date BETWEEN :min_date AND :max_date
                    AND growth_day BETWEEN :min_growth_day AND :max_growth_day
                ORDER BY embedding <-> :query_vector
                LIMIT :top_k
            """)
            
            # Execute query
            with self.db_engine.connect() as conn:
                result = conn.execute(
                    query,
                    {
                        "query_vector": str(embedding_list),
                        "room_id": room_id,
                        "min_date": min_date,
                        "max_date": max_date,
                        "min_growth_day": min_growth_day,
                        "max_growth_day": max_growth_day,
                        "top_k": top_k
                    }
                )
                
                rows = result.fetchall()
            
            # Convert results to SimilarCase objects
            similar_cases = []
            for row in rows:
                # Calculate similarity score from distance
                # L2 distance ranges from 0 (identical) to ~2 (opposite)
                # Convert to 0-100 percentage (100 = identical, 0 = very different)
                distance = row.distance
                similarity_score = self._distance_to_similarity(distance)
                
                # Extract environmental parameters
                env_status = row.env_sensor_status or {}
                temperature = env_status.get('temperature', 0.0)
                humidity = env_status.get('humidity', 0.0)
                co2 = env_status.get('co2', 0.0)
                
                # Calculate confidence level
                confidence_level = self._calculate_confidence_level(similarity_score)
                
                # Create SimilarCase object
                similar_case = SimilarCase(
                    similarity_score=similarity_score,
                    confidence_level=confidence_level,
                    room_id=row.room_id,
                    growth_day=row.growth_day,
                    collection_time=row.collection_datetime,
                    temperature=temperature,
                    humidity=humidity,
                    co2=co2,
                    air_cooler_params=row.air_cooler_config or {},
                    fresh_air_params=row.fresh_fan_config or {},
                    humidifier_params=row.humidifier_config or {},
                    grow_light_params=row.light_config or {}
                )
                
                similar_cases.append(similar_case)
                
                # Log low confidence warning
                if confidence_level == "low":
                    logger.warning(
                        f"[CLIPMatcher] Low confidence match found: "
                        f"similarity={similarity_score:.2f}%, growth_day={row.growth_day}"
                    )
            
            logger.info(
                f"[CLIPMatcher] Found {len(similar_cases)} similar cases, "
                f"avg_similarity={sum(c.similarity_score for c in similar_cases) / len(similar_cases) if similar_cases else 0:.2f}%"
            )
            
            return similar_cases
            
        except Exception as e:
            logger.error(f"[CLIPMatcher] Error finding similar cases: {e}")
            logger.exception(e)
            return []
    
    def _calculate_confidence_level(self, similarity_score: float) -> str:
        """
        Calculate confidence level based on similarity score
        
        Confidence levels:
        - high: similarity_score > 60%
        - medium: 20% <= similarity_score <= 60%
        - low: similarity_score < 20%
        
        Args:
            similarity_score: Similarity score (0-100)
            
        Returns:
            Confidence level string ("high" | "medium" | "low")
            
        Requirements: 4.6
        """
        if similarity_score > 60:
            return "high"
        elif similarity_score >= 20:
            return "medium"
        else:
            return "low"
    
    def _distance_to_similarity(self, distance: float) -> float:
        """
        Convert L2 distance to similarity score (0-100)
        
        L2 distance for normalized vectors typically ranges from:
        - 0.0 (identical vectors) to ~2.0 (opposite vectors)
        
        We convert this to a 0-100 similarity score where:
        - distance 0.0 -> similarity 100%
        - distance 2.0 -> similarity 0%
        
        Args:
            distance: L2 distance from pgvector
            
        Returns:
            Similarity score (0-100)
            
        Requirements: 4.4
        """
        # Clamp distance to reasonable range
        distance = max(0.0, min(distance, 2.0))
        
        # Convert to similarity percentage
        # Using exponential decay for better discrimination
        # similarity = 100 * (1 - distance/2)^2
        similarity = 100.0 * (1.0 - distance / 2.0) ** 2
        
        return round(similarity, 2)
