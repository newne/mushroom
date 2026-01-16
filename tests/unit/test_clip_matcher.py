"""
Unit tests for CLIPMatcher

Tests cover:
- Normal operation with valid inputs
- Edge cases (empty results, no matches)
- Boundary conditions (date/growth day windows)
- Error handling (database errors, invalid inputs)
- Confidence level calculation
- Distance to similarity conversion
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import numpy as np
import pytest
from sqlalchemy import create_engine

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from decision_analysis.clip_matcher import CLIPMatcher
from decision_analysis.data_models import SimilarCase


class TestCLIPMatcher:
    """Unit tests for CLIPMatcher class"""
    
    @pytest.fixture
    def mock_engine(self):
        """Create a mock database engine"""
        return Mock()
    
    @pytest.fixture
    def matcher(self, mock_engine):
        """Create a CLIPMatcher instance with mock engine"""
        return CLIPMatcher(mock_engine)
    
    @pytest.fixture
    def sample_embedding(self):
        """Create a sample 512-dimensional embedding"""
        return np.random.rand(512).astype(np.float32)
    
    def test_init(self, mock_engine):
        """Test CLIPMatcher initialization"""
        matcher = CLIPMatcher(mock_engine)
        assert matcher.db_engine == mock_engine
    
    def test_calculate_confidence_level_high(self, matcher):
        """Test confidence level calculation for high similarity (>60%)"""
        assert matcher._calculate_confidence_level(100.0) == "high"
        assert matcher._calculate_confidence_level(80.0) == "high"
        assert matcher._calculate_confidence_level(61.0) == "high"
    
    def test_calculate_confidence_level_medium(self, matcher):
        """Test confidence level calculation for medium similarity (20-60%)"""
        assert matcher._calculate_confidence_level(60.0) == "medium"
        assert matcher._calculate_confidence_level(40.0) == "medium"
        assert matcher._calculate_confidence_level(20.0) == "medium"
    
    def test_calculate_confidence_level_low(self, matcher):
        """Test confidence level calculation for low similarity (<20%)"""
        assert matcher._calculate_confidence_level(19.9) == "low"
        assert matcher._calculate_confidence_level(10.0) == "low"
        assert matcher._calculate_confidence_level(0.0) == "low"
    
    def test_distance_to_similarity_identical(self, matcher):
        """Test distance to similarity conversion for identical vectors (distance=0)"""
        similarity = matcher._distance_to_similarity(0.0)
        assert similarity == 100.0
    
    def test_distance_to_similarity_opposite(self, matcher):
        """Test distance to similarity conversion for opposite vectors (distance=2)"""
        similarity = matcher._distance_to_similarity(2.0)
        assert similarity == 0.0
    
    def test_distance_to_similarity_mid_range(self, matcher):
        """Test distance to similarity conversion for mid-range distances"""
        # Distance 1.0 should give 25% similarity
        similarity = matcher._distance_to_similarity(1.0)
        assert 24.0 <= similarity <= 26.0
        
        # Distance 0.5 should give ~56% similarity
        similarity = matcher._distance_to_similarity(0.5)
        assert 55.0 <= similarity <= 57.0
    
    def test_distance_to_similarity_clamping(self, matcher):
        """Test distance to similarity conversion clamps values correctly"""
        # Negative distance should be clamped to 0
        similarity = matcher._distance_to_similarity(-1.0)
        assert similarity == 100.0
        
        # Distance > 2.0 should be clamped to 2.0
        similarity = matcher._distance_to_similarity(5.0)
        assert similarity == 0.0
    
    def test_find_similar_cases_empty_result(self, matcher, sample_embedding):
        """Test find_similar_cases when no matches are found"""
        # Mock database connection that returns empty result
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="999",  # Non-existent room
            in_date=date(2024, 1, 1),
            growth_day=10,
            top_k=3
        )
        
        assert similar_cases == []
    
    def test_find_similar_cases_database_error(self, matcher, sample_embedding):
        """Test find_similar_cases handles database errors gracefully"""
        # Mock database connection that raises an exception
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database connection failed")
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="611",
            in_date=date(2024, 1, 1),
            growth_day=10,
            top_k=3
        )
        
        # Should return empty list on error
        assert similar_cases == []
    
    def test_find_similar_cases_with_valid_results(self, matcher, sample_embedding):
        """Test find_similar_cases with valid database results"""
        # Mock database result
        mock_row = Mock()
        mock_row.room_id = "611"
        mock_row.growth_day = 10
        mock_row.collection_datetime = datetime(2024, 1, 1, 12, 0, 0)
        mock_row.distance = 0.5  # Should give ~56% similarity
        mock_row.env_sensor_status = {
            "temperature": 18.5,
            "humidity": 95.0,
            "co2": 2000.0
        }
        mock_row.air_cooler_config = {"temp_set": 18.0}
        mock_row.fresh_fan_config = {"mode": 1}
        mock_row.humidifier_config = {"on": 90}
        mock_row.light_config = {"model": 1}
        
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="611",
            in_date=date(2024, 1, 1),
            growth_day=10,
            top_k=3
        )
        
        assert len(similar_cases) == 1
        case = similar_cases[0]
        
        # Verify case attributes
        assert case.room_id == "611"
        assert case.growth_day == 10
        assert case.collection_time == datetime(2024, 1, 1, 12, 0, 0)
        assert 55.0 <= case.similarity_score <= 57.0  # ~56% for distance 0.5
        assert case.confidence_level == "medium"
        assert case.temperature == 18.5
        assert case.humidity == 95.0
        assert case.co2 == 2000.0
        assert case.air_cooler_params == {"temp_set": 18.0}
        assert case.fresh_air_params == {"mode": 1}
        assert case.humidifier_params == {"on": 90}
        assert case.grow_light_params == {"model": 1}
    
    def test_find_similar_cases_missing_env_status(self, matcher, sample_embedding):
        """Test find_similar_cases handles missing env_sensor_status"""
        # Mock database result with None env_sensor_status
        mock_row = Mock()
        mock_row.room_id = "611"
        mock_row.growth_day = 10
        mock_row.collection_datetime = datetime(2024, 1, 1, 12, 0, 0)
        mock_row.distance = 0.0
        mock_row.env_sensor_status = None  # Missing environmental data
        mock_row.air_cooler_config = {}
        mock_row.fresh_fan_config = {}
        mock_row.humidifier_config = {}
        mock_row.light_config = {}
        
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="611",
            in_date=date(2024, 1, 1),
            growth_day=10,
            top_k=3
        )
        
        assert len(similar_cases) == 1
        case = similar_cases[0]
        
        # Should use default values (0.0) for missing environmental data
        assert case.temperature == 0.0
        assert case.humidity == 0.0
        assert case.co2 == 0.0
    
    def test_find_similar_cases_low_confidence_warning(self, matcher, sample_embedding):
        """Test find_similar_cases logs warning for low confidence matches"""
        # Mock database result with high distance (low similarity)
        mock_row = Mock()
        mock_row.room_id = "611"
        mock_row.growth_day = 10
        mock_row.collection_datetime = datetime(2024, 1, 1, 12, 0, 0)
        mock_row.distance = 1.8  # Should give ~1% similarity (low confidence)
        mock_row.env_sensor_status = {"temperature": 18.5, "humidity": 95.0, "co2": 2000.0}
        mock_row.air_cooler_config = {}
        mock_row.fresh_fan_config = {}
        mock_row.humidifier_config = {}
        mock_row.light_config = {}
        
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        with patch('decision_analysis.clip_matcher.logger') as mock_logger:
            similar_cases = matcher.find_similar_cases(
                query_embedding=sample_embedding,
                room_id="611",
                in_date=date(2024, 1, 1),
                growth_day=10,
                top_k=3
            )
            
            # Verify warning was logged for low confidence
            assert any(
                call[0][0].startswith("[CLIPMatcher] Low confidence match found")
                for call in mock_logger.warning.call_args_list
            )
        
        assert len(similar_cases) == 1
        assert similar_cases[0].confidence_level == "low"
    
    def test_find_similar_cases_respects_top_k(self, matcher, sample_embedding):
        """Test find_similar_cases respects top_k parameter"""
        # Mock database result with multiple rows
        mock_rows = []
        for i in range(5):
            mock_row = Mock()
            mock_row.room_id = "611"
            mock_row.growth_day = 10 + i
            mock_row.collection_datetime = datetime(2024, 1, 1, 12, 0, 0)
            mock_row.distance = 0.1 * i
            mock_row.env_sensor_status = {"temperature": 18.5, "humidity": 95.0, "co2": 2000.0}
            mock_row.air_cooler_config = {}
            mock_row.fresh_fan_config = {}
            mock_row.humidifier_config = {}
            mock_row.light_config = {}
            mock_rows.append(mock_row)
        
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        # Test with top_k=3
        similar_cases = matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="611",
            in_date=date(2024, 1, 1),
            growth_day=10,
            top_k=3
        )
        
        # Should return all 5 rows (database LIMIT is applied in SQL)
        assert len(similar_cases) == 5
    
    def test_find_similar_cases_date_window_calculation(self, matcher, sample_embedding):
        """Test find_similar_cases calculates date windows correctly"""
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        matcher.db_engine.connect.return_value = mock_conn
        
        target_date = date(2024, 1, 15)
        
        matcher.find_similar_cases(
            query_embedding=sample_embedding,
            room_id="611",
            in_date=target_date,
            growth_day=10,
            top_k=3,
            date_window_days=7,
            growth_day_window=3
        )
        
        # Verify SQL query was called with correct date range
        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        
        assert params["min_date"] == date(2024, 1, 8)  # 15 - 7
        assert params["max_date"] == date(2024, 1, 22)  # 15 + 7
        assert params["min_growth_day"] == 7  # 10 - 3
        assert params["max_growth_day"] == 13  # 10 + 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
