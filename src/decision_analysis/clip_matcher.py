"""
CLIP Matcher Module

This module implements CLIP-based similarity matching to find historical cases
that are most similar to the current mushroom growth state.

It uses embedding similarity combined with environmental parameter matching
to find the top-K most similar historical cases, excluding current batch data.
"""

from datetime import date, datetime
from typing import Dict, List

import numpy as np
from loguru import logger
from sqlalchemy import Engine

from decision_analysis.data_models import SimilarCase


class CLIPMatcher:
    """
    CLIP similarity matcher for finding historical cases

    Uses embedding similarity combined with environmental parameter matching
    to find the most similar historical cases, excluding current batch data.
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
        growth_day_window: int = 3,
        multi_image_data: Dict = None,
        analysis_datetime: datetime = None,
    ) -> List[SimilarCase]:
        """
        Find similar historical cases using CLIP vector similarity
        Enhanced to exclude current batch and only search historical batches

        Process:
        1. Exclude current batch (in_date) from search
        2. Filter by growth day window (±growth_day_window)
        3. Use embedding similarity and environmental parameter closeness
        4. Apply multi-image weighting if available
        5. Return top-K most similar historical cases

        Args:
            query_embedding: Query vector (512 dimensions) - can be aggregated from multiple images
            room_id: Room number
            in_date: Entry date (current batch to be excluded)
            growth_day: Growth day
            top_k: Number of results to return
            date_window_days: Legacy parameter (kept for compatibility)
            growth_day_window: Growth day window (±days)
            multi_image_data: Multi-image analysis metadata (optional)

        Returns:
            List of SimilarCase objects with similarity scores and metadata
            Based on historical batches only (current batch excluded)

        Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, Historical Batch Exclusion
        """
        logger.info(
            f"[CLIPMatcher] Finding similar cases with historical batch exclusion: "
            f"room_id={room_id}, exclude_in_date={in_date}, growth_day={growth_day}, top_k={top_k}"
        )

        if multi_image_data:
            logger.info(
                f"[CLIPMatcher] Multi-image analysis: "
                f"total_images={multi_image_data.get('total_images', 0)}, "
                f"images_in_window={multi_image_data.get('images_in_window', 0)}, "
                f"avg_quality={multi_image_data.get('avg_quality', 'N/A')}"
            )

        try:
            # Extract current environmental parameters from multi_image_data if available
            current_env_params = {}
            if multi_image_data:
                current_env_params = {
                    "temperature": multi_image_data.get("current_temperature"),
                    "humidity": multi_image_data.get("current_humidity"),
                    "co2": multi_image_data.get("current_co2"),
                }

            # Use DataExtractor to find similar historical cases
            from decision_analysis.data_extractor import DataExtractor

            data_extractor = DataExtractor(self.db_engine)

            similar_df = data_extractor.find_top_similar_historical_cases(
                current_embedding=query_embedding,
                current_env_params=current_env_params,
                room_id=room_id,
                current_in_date=in_date,
                target_growth_day=growth_day,
                growth_day_window=growth_day_window,
                top_k=top_k,
                analysis_datetime=analysis_datetime,
                date_window_days=date_window_days,
            )

            if similar_df.empty:
                logger.warning("[CLIPMatcher] No similar historical cases found")
                return []

            # Convert DataFrame to SimilarCase objects
            similar_cases = []
            for _, row in similar_df.iterrows():
                try:
                    # Extract environmental parameters
                    temperature = row.get("temperature", 0.0)
                    humidity = row.get("humidity", 0.0)
                    co2 = row.get("co2", 0.0)

                    # Convert similarity score to percentage
                    similarity_score = float(row.get("combined_similarity", 0.0)) * 100

                    # Determine confidence level based on similarity
                    if similarity_score >= 80:
                        confidence_level = "high"
                    elif similarity_score >= 50:
                        confidence_level = "medium"
                    else:
                        confidence_level = "low"

                    similar_case = SimilarCase(
                        similarity_score=similarity_score,
                        confidence_level=confidence_level,
                        room_id=row["room_id"],
                        growth_day=row["growth_day"],
                        collection_time=row["collection_datetime"],
                        temperature=temperature,
                        humidity=humidity,
                        co2=co2,
                        air_cooler_params=row.get("air_cooler_config", {}),
                        fresh_air_params=row.get("fresh_fan_config", {}),
                        humidifier_params=row.get("humidifier_config", {}),
                        grow_light_params=row.get("light_config", {}),
                    )

                    similar_cases.append(similar_case)

                except Exception as e:
                    logger.warning(
                        f"[CLIPMatcher] Error creating SimilarCase object: {e}"
                    )
                    continue

            logger.info(
                f"[CLIPMatcher] Found {len(similar_cases)} similar cases, "
                f"avg_similarity={sum(case.similarity_score for case in similar_cases) / len(similar_cases):.2f}%"
            )

            return similar_cases

        except Exception as e:
            logger.error(
                f"[CLIPMatcher] Error finding similar cases: {e}", exc_info=True
            )
            return []

    def find_similar_cases_multi_image(
        self,
        query_embedding: np.ndarray,
        room_id: str,
        in_date: datetime = None,
        growth_day: int = 0,
        top_k: int = 3,
        date_window_days: int = 7,
        growth_day_window: int = 3,
        multi_image_boost: bool = True,
        image_count: int = 1,
        analysis_datetime: datetime = None,
    ) -> List[SimilarCase]:
        """
        Find similar cases with multi-image boost

        This enhanced version applies a boost to similarity scores when
        multiple images are available, improving confidence in matches.

        Args:
            query_embedding: Query embedding vector
            room_id: Room number
            in_date: Mushroom batch in_date
            growth_day: Current growth day
            top_k: Number of top similar cases to return
            date_window_days: Date window for filtering
            growth_day_window: Growth day window for filtering
            multi_image_boost: Whether to apply multi-image boost
            image_count: Number of images in current analysis

        Returns:
            List of similar cases with enhanced similarity scores
        """
        logger.info(
            f"[CLIPMatcher] Finding similar cases with multi-image boost (images: {image_count})"
        )

        # First get regular similar cases
        similar_cases = self.find_similar_cases(
            query_embedding=query_embedding,
            room_id=room_id,
            in_date=in_date,
            growth_day=growth_day,
            top_k=top_k,
            date_window_days=date_window_days,
            growth_day_window=growth_day_window,
            analysis_datetime=analysis_datetime,
        )

        if not multi_image_boost or image_count <= 1:
            return similar_cases

        # Apply multi-image boost
        boosted_cases = []
        for case in similar_cases:
            # Calculate boost factor based on image count
            # More images = higher confidence = higher boost
            boost_factor = min(1.0 + (image_count - 1) * 0.05, 1.2)  # Max 20% boost

            # Apply boost to similarity score
            boosted_similarity = min(case.similarity_score * boost_factor, 100.0)

            # Update confidence level if boosted
            confidence_level = case.confidence_level
            if boosted_similarity >= 85 and confidence_level != "high":
                confidence_level = "high"
            elif boosted_similarity >= 50 and confidence_level == "low":
                confidence_level = "medium"

            # Create boosted case
            boosted_case = SimilarCase(
                similarity_score=boosted_similarity,
                confidence_level=confidence_level,
                room_id=case.room_id,
                growth_day=case.growth_day,
                collection_time=case.collection_time,
                temperature=case.temperature,
                humidity=case.humidity,
                co2=case.co2,
                air_cooler_params=case.air_cooler_params,
                fresh_air_params=case.fresh_air_params,
                humidifier_params=case.humidifier_params,
                grow_light_params=case.grow_light_params,
            )

            boosted_cases.append(boosted_case)

            logger.debug(
                f"[CLIPMatcher] Boosted case similarity: {case.similarity_score:.2f}% -> {boosted_similarity:.2f}% "
                f"(boost factor: {boost_factor:.2f})"
            )

        logger.info(
            f"[CLIPMatcher] Applied multi-image boost to {len(boosted_cases)} cases "
            f"(avg boost: {(sum(bc.similarity_score for bc in boosted_cases) / len(boosted_cases)) - (sum(c.similarity_score for c in similar_cases) / len(similar_cases)):.2f}%)"
        )

        return boosted_cases

    def _apply_multi_image_boost(
        self, similarity_score: float, multi_image_data: Dict
    ) -> float:
        """
        Apply confidence boost based on multi-image analysis

        Args:
            similarity_score: Original similarity score
            multi_image_data: Multi-image analysis metadata

        Returns:
            Boosted similarity score
        """
        # Get multi-image parameters
        total_images = multi_image_data.get("total_images", 1)
        images_in_window = multi_image_data.get("images_in_window", 1)
        avg_quality = multi_image_data.get("avg_quality", 50.0)

        # Calculate boost factors
        image_count_boost = min(1.0 + (total_images - 1) * 0.05, 1.2)  # Max 20% boost
        quality_boost = 1.0 + (avg_quality - 50.0) / 100.0 * 0.1  # Quality-based boost
        consistency_boost = (
            1.0 + (images_in_window / max(total_images, 1)) * 0.1
        )  # Consistency boost

        # Apply combined boost
        total_boost = image_count_boost * quality_boost * consistency_boost
        boosted_score = min(similarity_score * total_boost, 100.0)

        logger.debug(
            f"[CLIPMatcher] Multi-image boost: {similarity_score:.2f}% -> {boosted_score:.2f}% "
            f"(boost factor: {total_boost:.3f})"
        )

        return boosted_score

    def aggregate_embeddings(
        self,
        embeddings: List[np.ndarray],
        weights: List[float] = None,
        method: str = "weighted_average",
    ) -> np.ndarray:
        """
        Aggregate multiple image embeddings into a single representative embedding

        Args:
            embeddings: List of embedding vectors
            weights: List of weights for each embedding (optional)
            method: Aggregation method ("weighted_average", "max_pooling", "concatenate")

        Returns:
            Aggregated embedding vector
        """
        if not embeddings:
            raise ValueError("No embeddings provided for aggregation")

        if len(embeddings) == 1:
            return embeddings[0]

        embeddings_array = np.array(embeddings)

        if weights is None:
            weights = np.ones(len(embeddings)) / len(embeddings)
        else:
            weights = np.array(weights)
            weights = weights / weights.sum()  # Normalize weights

        if method == "weighted_average":
            # Weighted average of embeddings
            aggregated = np.average(embeddings_array, axis=0, weights=weights)
        elif method == "max_pooling":
            # Element-wise maximum
            aggregated = np.max(embeddings_array, axis=0)
        elif method == "concatenate":
            # Concatenate and reduce dimensionality (simple approach)
            concatenated = np.concatenate(embeddings_array)
            # Use PCA or simple averaging to reduce back to original dimension
            # For simplicity, we'll reshape and average
            target_dim = embeddings[0].shape[0]
            reshaped = concatenated.reshape(-1, target_dim)
            aggregated = np.mean(reshaped, axis=0)
        else:
            raise ValueError(f"Unknown aggregation method: {method}")

        logger.debug(
            f"[CLIPMatcher] Aggregated {len(embeddings)} embeddings using {method}"
        )

        return aggregated

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
